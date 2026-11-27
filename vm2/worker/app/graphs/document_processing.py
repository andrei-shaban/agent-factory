"""
Document Processing Graph — Пайплайн обработки ПЗД (акты КС-2/КС-3, УПД).
Production Blueprint v5.3.1 — ВМ №2

События публикуются в event_bus, чекпоинты — в PostgreSQL,
отмена — через cancellation-модуль.
HITL-узлы: interrupt() размещается в отдельной ноде БЕЗ побочных эффектов;
подготовка выполняется в отдельной ноде перед interrupt-нодой.
Трейсинг Langfuse подключается на этапе 11 (D7).
"""

import json
import operator
import uuid
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from worker.app.cancellation import RunCancelledError, _check_cancellation
from worker.app.event_bus import event_bus
from worker.app.sandbox import SandboxClient
from worker.app.settings import get_settings

logger = structlog.get_logger()


class DocumentState(TypedDict):
    run_id: str
    thread_id: str
    user_id: str
    document_key: str
    document_type: str
    contract_id: Optional[str]
    ocr_text: Optional[str]
    ocr_tables: Optional[List[Dict[str, Any]]]
    positions: Optional[List[Dict[str, Any]]]
    verification: Optional[List[Dict[str, Any]]]
    mismatches: Optional[List[Dict[str, Any]]]
    specialist_decision: Optional[Dict[str, Any]]
    specialist_interrupt_id: Optional[str]
    specialist_form_data: Optional[Dict[str, Any]]
    output_artifact_key: Optional[str]
    approver_decision: Optional[Dict[str, Any]]
    approver_interrupt_id: Optional[str]
    approver_form_data: Optional[Dict[str, Any]]
    status: str
    messages: Annotated[List[Dict[str, Any]], operator.add]


async def load_and_ocr(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    node_name = "load_and_ocr"
    log = logger.bind(run_id=run_id, node=node_name)

    await _check_cancellation(run_id)
    await event_bus.publish_node_started(thread_id, run_id, node_name)

    try:
        log.info("Loading document and running OCR")
        settings = get_settings()
        file_bytes = await s3_download(settings, state["document_key"])
        ocr_result = await gpu_ocr(file_bytes, "pdf")

        pages = ocr_result.get("pages") or len(ocr_result.get("layoutParsingResults", []))
        ocr_text = ocr_result.get("fullText", "")
        if not ocr_text:
            raise ValueError("OCR returned empty text (layoutParsingResults empty)")

        log.info("OCR completed", pages=pages)
        await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"pages": pages})

        return {
            "status": "ocr_done",
            "ocr_text": ocr_text,
            "ocr_tables": ocr_result.get("tables", []),
            "messages": [{"role": "system", "content": f"Документ распознан: {len(file_bytes)} байт"}],
        }
    except RunCancelledError:
        raise
    except Exception as e:
        log.error("OCR failed", error=str(e))
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


async def extract_positions(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    node_name = "extract_positions"
    log = logger.bind(run_id=run_id, node=node_name)

    await _check_cancellation(run_id)
    await event_bus.publish_node_started(thread_id, run_id, node_name)

    try:
        prompt = (
            "Извлеки из текста акта все позиции. Верни JSON-массив объектов с полями: "
            "number, name, unit, quantity, price, amount. Извлекай строго по данным документа.\n\n"
            f"Текст акта:\n{state['ocr_text']}"
        )
        result = await gpu_chat(
            [
                {"role": "system", "content": "Ты — точный экстрактор данных из закрывающих документов."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
        positions = json.loads(result["choices"][0]["message"]["content"])

        log.info("Positions extracted", count=len(positions))
        await event_bus.publish_node_finished(
            thread_id, run_id, node_name, output={"positions_count": len(positions)}
        )

        return {
            "status": "positions_extracted",
            "positions": positions,
            "messages": [{"role": "assistant", "content": f"Извлечено позиций: {len(positions)}"}],
        }
    except RunCancelledError:
        raise
    except Exception as e:
        log.error("Position extraction failed", error=str(e))
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


async def match_rates(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    node_name = "match_rates"
    log = logger.bind(run_id=run_id, node=node_name)

    await _check_cancellation(run_id)
    await event_bus.publish_node_started(thread_id, run_id, node_name)

    try:
        contract_id = state.get("contract_id")
        verification: List[Dict[str, Any]] = []
        mismatches: List[Dict[str, Any]] = []

        rates = await corp_rates(contract_id)

        for pos in state["positions"]:
            matched = _fuzzy_match_position(pos["name"], rates)
            if matched is None:
                mismatches.append({"position": pos, "reason": "missing_rate", "confidence": 0.0})
                verification.append({"position_number": pos["number"], "status": "missing_rate"})
            else:
                price_diff = abs(float(pos.get("price", 0)) - float(matched["price"]))
                if price_diff > 0.01:
                    mismatches.append({
                        "position": pos,
                        "expected_price": matched["price"],
                        "actual_price": pos.get("price"),
                        "difference": price_diff,
                        "reason": "mismatched",
                        "confidence": 0.9,
                        "page_number": pos.get("page"),
                    })
                    verification.append({"position_number": pos["number"], "status": "mismatched"})
                else:
                    verification.append({"position_number": pos["number"], "status": "matched"})

        await event_bus.publish_node_finished(
            thread_id, run_id, node_name,
            output={"verified": len(verification), "mismatches": len(mismatches)},
        )

        return {
            "status": "rates_matched",
            "verification": verification,
            "mismatches": mismatches,
            "messages": [{"role": "assistant", "content": f"Сверка: расхождений {len(mismatches)}"}],
        }
    except RunCancelledError:
        raise
    except Exception as e:
        log.error("Rate matching failed", error=str(e))
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


def _fuzzy_match_position(name: str, rates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    from difflib import SequenceMatcher

    best, best_score = None, 0.0
    for r in rates:
        score = SequenceMatcher(None, name.lower(), r["item_name"].lower()).ratio()
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.75 else None


async def specialist_review_prepare(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    node_name = "specialist_review_prepare"
    log = logger.bind(run_id=run_id, node=node_name)

    await _check_cancellation(run_id)
    await event_bus.publish_node_started(thread_id, run_id, node_name)

    try:
        if not state.get("mismatches"):
            log.info("No mismatches, skipping specialist review")
            await event_bus.publish_node_finished(
                thread_id, run_id, node_name, output={"auto_approved": True}
            )
            return {"status": "auto_approved", "specialist_decision": {"approved": True, "auto": True}}

        form_data = {
            "type": "hitl_form",
            "form_id": "specialist_verification",
            "title": "Проверка расхождений",
            "mismatches": state["mismatches"],
            "allowed_actions": ["approve", "correct", "reject"],
        }
        interrupt_id = str(uuid.uuid4())
        await event_bus.publish_hitl_interrupt(thread_id, run_id, interrupt_id, form_data)

        log.info("Prepared for specialist review", interrupt_id=interrupt_id)
        return {"specialist_interrupt_id": interrupt_id, "specialist_form_data": form_data}
    except RunCancelledError:
        raise
    except Exception as e:
        log.error("Specialist review prepare failed", error=str(e))
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


async def specialist_review_interrupt(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    log = logger.bind(run_id=run_id, node="specialist_review_interrupt")

    await _check_cancellation(run_id)

    if state.get("specialist_decision", {}).get("auto"):
        return state

    interrupt_id = state.get("specialist_interrupt_id")
    form_data = state.get("specialist_form_data")
    if not interrupt_id or not form_data:
        raise ValueError("specialist_interrupt_id and specialist_form_data required")

    decision = interrupt(form_data)

    await _check_cancellation(run_id)
    await event_bus.publish_hitl_resumed(thread_id, run_id, interrupt_id, decision)
    log.info("Specialist decision received", approved=decision.get("approved"))

    # D3: отказ специалиста — терминальный исход, а не промежуточный статус.
    return {
        "status": "specialist_reviewed" if decision.get("approved") else "rejected",
        "specialist_decision": decision,
    }


async def generate_output(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    node_name = "generate_output"
    log = logger.bind(run_id=run_id, node=node_name)

    await _check_cancellation(run_id)
    await event_bus.publish_node_started(thread_id, run_id, node_name)

    try:
        settings = get_settings()
        sandbox = SandboxClient(settings)
        final_positions = _apply_specialist_corrections(
            state["positions"], state.get("specialist_decision", {})
        )

        result = await sandbox.execute_tool(
            image="factory/toolset-excel:1.0.0",
            cmd=["python", "/app/tools/generate_sap_template.py"],
            input_files={
                "positions.json": _to_bytes(final_positions),
                "meta.json": _to_bytes({
                    "run_id": state["run_id"],
                    "contract_id": state.get("contract_id"),
                    "document_type": state["document_type"],
                }),
            },
        )

        if not result.success:
            return {
                "status": "failed",
                "messages": [{"role": "error", "content": result.error_message}],
            }

        output_bytes = result.artifacts.get("output.xlsx", b"")
        if not output_bytes:
            # D3: пустой артефакт — терминальная ошибка, не повод идти на подпись.
            return {
                "status": "failed",
                "messages": [{"role": "error", "content": "Sandbox returned empty output.xlsx"}],
            }

        output_key = f"runs/{state['run_id']}/final_act.xlsx"
        await s3_upload(
            settings, output_key, output_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        await event_bus.publish_node_finished(
            thread_id, run_id, node_name,
            output={"artifact_key": output_key, "size": len(output_bytes)},
        )

        return {
            "status": "output_generated",
            "output_artifact_key": output_key,
            "messages": [{"role": "assistant", "content": "Шаблон SAP сформирован"}],
        }
    except RunCancelledError:
        raise
    except Exception as e:
        log.error("Output generation failed", error=str(e))
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


def _apply_specialist_corrections(positions, decision):
    if not decision.get("corrections"):
        return positions
    corrected = [dict(p) for p in positions]
    for corr in decision["corrections"]:
        for p in corrected:
            if p["number"] == corr["position_number"]:
                p.update(corr["values"])
    return corrected


def _to_bytes(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


async def approver_sign_prepare(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    node_name = "approver_sign_prepare"
    log = logger.bind(run_id=run_id, node=node_name)

    await _check_cancellation(run_id)
    await event_bus.publish_node_started(thread_id, run_id, node_name)

    try:
        form_data = {
            "type": "hitl_form",
            "form_id": "approver_sign",
            "title": "Утверждение акта",
            "output_artifact": state["output_artifact_key"],
            "summary": _build_summary(state),
            "allowed_actions": ["sign", "reject"],
        }
        interrupt_id = str(uuid.uuid4())
        await event_bus.publish_hitl_interrupt(thread_id, run_id, interrupt_id, form_data)
        return {"approver_interrupt_id": interrupt_id, "approver_form_data": form_data}
    except RunCancelledError:
        raise
    except Exception as e:
        log.error("Approver sign prepare failed", error=str(e))
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


async def approver_sign_interrupt(state: DocumentState) -> DocumentState:
    await _check_cancellation(state["run_id"])

    interrupt_id = state.get("approver_interrupt_id")
    form_data = state.get("approver_form_data")
    if not interrupt_id or not form_data:
        raise ValueError("approver_interrupt_id and approver_form_data required")

    decision = interrupt(form_data)

    await _check_cancellation(state["run_id"])
    await event_bus.publish_hitl_resumed(state["thread_id"], state["run_id"], interrupt_id, decision)

    return {
        "status": "approved" if decision.get("signed") else "rejected",
        "approver_decision": decision,
    }


def _build_summary(state):
    return {
        "total_positions": len(state["positions"]),
        "matched": sum(1 for v in state["verification"] if v["status"] == "matched"),
        "contract_id": state.get("contract_id"),
    }


async def finalize(state: DocumentState) -> DocumentState:
    run_id, thread_id = state["run_id"], state["thread_id"]
    node_name = "finalize"
    log = logger.bind(run_id=run_id, node=node_name)

    await _check_cancellation(run_id)
    await event_bus.publish_node_started(thread_id, run_id, node_name)

    try:
        status = state["status"]

        # D3: терминальное событие публикуется на ЛЮБОМ исходе —
        # иначе SSE-поток Gateway не закрывается.
        if status in ("failed", "rejected"):
            await event_bus.publish_run_failed(
                thread_id, run_id, error_message=f"Run finished with status: {status}"
            )
        else:
            await event_bus.publish_run_finished(thread_id, run_id, state)

        await event_bus.publish_node_finished(thread_id, run_id, node_name)
        return {"messages": [{"role": "system", "content": f"Ран завершён: {status}"}]}
    except RunCancelledError:
        raise
    except Exception as e:
        log.error("Finalization failed", error=str(e))
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


def route_after_specialist_prepare(state) -> Literal["specialist_review_interrupt", "finalize"]:
    return "finalize" if state.get("specialist_decision", {}).get("auto") else "specialist_review_interrupt"


def route_after_review(state) -> Literal["generate_output", "finalize"]:
    return "generate_output" if state.get("specialist_decision", {}).get("approved") else "finalize"


def route_after_output(state) -> Literal["approver_sign_prepare", "finalize"]:
    # D3: при сбое генерации не выходим на подпись несуществующего артефакта.
    return "finalize" if state.get("status") == "failed" else "approver_sign_prepare"


def route_after_approve(state) -> Literal["finalize"]:
    return "finalize"


def build_document_processing_graph() -> StateGraph:
    graph = StateGraph(DocumentState)

    for name, fn in [
        ("load_and_ocr", load_and_ocr),
        ("extract_positions", extract_positions),
        ("match_rates", match_rates),
        ("specialist_review_prepare", specialist_review_prepare),
        ("specialist_review_interrupt", specialist_review_interrupt),
        ("generate_output", generate_output),
        ("approver_sign_prepare", approver_sign_prepare),
        ("approver_sign_interrupt", approver_sign_interrupt),
        ("finalize", finalize),
    ]:
        graph.add_node(name, fn)

    graph.add_edge(START, "load_and_ocr")
    graph.add_edge("load_and_ocr", "extract_positions")
    graph.add_edge("extract_positions", "match_rates")
    graph.add_edge("match_rates", "specialist_review_prepare")
    graph.add_conditional_edges("specialist_review_prepare", route_after_specialist_prepare)
    graph.add_conditional_edges("specialist_review_interrupt", route_after_review)
    graph.add_conditional_edges("generate_output", route_after_output)
    graph.add_edge("approver_sign_prepare", "approver_sign_interrupt")
    graph.add_conditional_edges("approver_sign_interrupt", route_after_approve)
    graph.add_edge("finalize", END)

    return graph


from worker.app.graphs.registry import graph_registry  # noqa: E402

graph_registry.register("document_processing", build_document_processing_graph())


# --- Интеграции (экземпляры клиентов реэкспортированы в integrations/__init__.py, D1) ---

async def s3_download(settings, key):
    from worker.app.integrations import s3_client
    return await s3_client.download_file(bucket=settings.seaweed_s3_bucket_factory, key=key)


async def s3_upload(settings, key, content, content_type):
    from worker.app.integrations import s3_client
    return await s3_client.upload_file(
        bucket=settings.seaweed_s3_bucket_factory, key=key,
        file_content=content, content_type=content_type,
    )


async def gpu_ocr(file_bytes, file_type):
    from worker.app.integrations import gpu_client
    return await gpu_client.ocr(file_content=file_bytes, file_type=file_type)


async def gpu_chat(messages, temperature):
    from worker.app.integrations import gpu_client
    return await gpu_client.chat_completion(messages=messages, temperature=temperature)


async def corp_rates(contract_id):
    from worker.app.integrations import corp_pg_client
    return await corp_pg_client.get_rates_for_contract(contract_id=contract_id, as_of_date=None)