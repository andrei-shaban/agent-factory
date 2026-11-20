"""
Document Processing Graph — Пайплайн обработки ПЗД (акты КС-2/КС-3, УПД).
Production Blueprint v5.1 — ВМ №2

Бизнес-логика (Ф1–Ф5):
  Ф1. Загрузка и распознавание документа (OCR на .228).
  Ф2. Извлечение позиций акта (LLM-экстракция).
  Ф3. Сверка позиций со ставками ЕРР (корп PG, нечёткий поиск).
  Ф4. HITL-проверка специалистом (расхождения, низкая уверенность).
  Ф5. Генерация выходного шаблона SAP/Визирь (песочница gVisor)
      + HITL-утверждение ЗГД + финализация артефактов.

Observability:
  - События AG-UI → Redis Streams (event_bus) для SSE-relay через Gateway.
  - Трейсы → Langfuse (langfuse_client): trace на весь запуск, span на узел.
  - Чекпоинты состояния — в PostgreSQL (thread_id).

Интегрируется с GraphRegistry (worker.app.graphs.registry).
"""

import json
import operator
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from worker.app.event_bus import event_bus
from worker.app.integrations import corp_pg_client, gpu_client, langfuse_client, s3_client
from worker.app.sandbox import SandboxClient
from worker.app.settings import get_settings

logger = structlog.get_logger()

# ==============================================================================
# ГЛОБАЛЬНЫЙ КЭШ TRACE'ОВ (по run_id)
# ==============================================================================
# Trace создаётся при первом узле, завершается в finalize.
# Хранится в памяти процесса воркера — безопасно, т.к. run_id уникален.
_active_traces: Dict[str, Any] = {}
_active_spans: Dict[str, Any] = {}  # ключ: f"{run_id}:{node_name}"


def _get_or_create_trace(state: DocumentState) -> Any:
    """Получить или создать Langfuse trace для run_id."""
    run_id = state["run_id"]
    if run_id not in _active_traces:
        _active_traces[run_id] = langfuse_client.create_trace(
            run_id=run_id,
            thread_id=state["thread_id"],
            graph_name="document_processing",
            user_id=state.get("user_id", "unknown"),
            metadata={
                "document_type": state.get("document_type"),
                "contract_id": state.get("contract_id"),
                "document_key": state.get("document_key"),
            },
        )
    return _active_traces[run_id]


def _start_span(state: DocumentState, node_name: str, input_data: Optional[Dict[str, Any]] = None) -> Any:
    """Создать span для узла."""
    trace = _get_or_create_trace(state)
    key = f"{state['run_id']}:{node_name}"
    span = langfuse_client.create_span(
        trace=trace,
        name=node_name,
        input_data=input_data,
        metadata={"thread_id": state["thread_id"]},
    )
    _active_spans[key] = span
    return span


def _end_span(state: DocumentState, node_name: str, output_data: Optional[Dict[str, Any]] = None, status: str = "success") -> None:
    """Завершить span узла."""
    key = f"{state['run_id']}:{node_name}"
    span = _active_spans.pop(key, None)
    if span:
        langfuse_client.end_span(span, output_data=output_data, status=status)


def _record_error(state: DocumentState, node_name: str, error: Exception) -> None:
    """Записать ошибку в span и trace."""
    key = f"{state['run_id']}:{node_name}"
    span = _active_spans.pop(key, None)
    if span:
        langfuse_client.end_span(span, output_data={"error": str(error)}, status="error")
    trace = _active_traces.get(state["run_id"])
    if trace:
        langfuse_client.create_event(
            trace=trace,
            name="error",
            input_data={"node": node_name},
            output_data={"error": str(error), "type": type(error).__name__},
        )


def _finalize_trace(state: DocumentState) -> None:
    """Завершить trace при финализации графа."""
    run_id = state["run_id"]
    trace = _active_traces.pop(run_id, None)
    if trace:
        langfuse_client.create_event(
            trace=trace,
            name="run_finished",
            output_data={"status": state.get("status")},
        )
        # Langfuse SDK автоматически завершит trace при flush


# ==============================================================================
# СОСТОЯНИЕ ГРАФА
# ==============================================================================


class DocumentState(TypedDict):
    """
    Состояние графа обработки ПЗД.
    Чекпоинтится в PostgreSQL по thread_id.
    """

    # Идентификаторы (из RunAgentInput Gateway)
    run_id: str
    thread_id: str
    user_id: str

    # Входящий документ
    document_key: str            # путь к файлу в S3 (factory-artifacts)
    document_type: str           # 'ks2' | 'ks3' | 'upd' | 'act'
    contract_id: Optional[str]   # привязка к договору (если известна)

    # Ф1: результат OCR
    ocr_text: Optional[str]
    ocr_tables: Optional[List[Dict[str, Any]]]

    # Ф2: извлечённые позиции
    positions: Optional[List[Dict[str, Any]]]

    # Ф3: результаты сверки со ставками ЕРР
    verification: Optional[List[Dict[str, Any]]]
    mismatches: Optional[List[Dict[str, Any]]]

    # Ф4: решение специалиста (HITL)
    specialist_decision: Optional[Dict[str, Any]]

    # Ф5: сгенерированный выходной шаблон
    output_artifact_key: Optional[str]

    # Финальное утверждение ЗГД (HITL)
    approver_decision: Optional[Dict[str, Any]]

    # Статус и сообщения для AG-UI
    status: str
    messages: Annotated[List[Dict[str, Any]], operator.add]


# ==============================================================================
# УЗЛЫ ГРАФА (ноды)
# ==============================================================================


async def load_and_ocr(state: DocumentState) -> DocumentState:
    """Ф1: Загрузка PDF из S3 и распознавание через OCR (.228)."""
    run_id = state["run_id"]
    thread_id = state["thread_id"]
    node_name = "load_and_ocr"
    log = logger.bind(run_id=run_id, node=node_name)

    await event_bus.publish_node_started(thread_id, run_id, node_name)
    _start_span(state, node_name, input_data={"document_key": state.get("document_key")})

    try:
        log.info("Loading document and running OCR")
        settings = get_settings()

        file_bytes = await s3_client.download_file(
            bucket=settings.seaweed_s3_bucket_factory,
            key=state["document_key"],
        )

        ocr_result = await gpu_client.ocr(file_content=file_bytes, file_type="pdf")

        pages = len(ocr_result.get("layoutParsingResults", []))
        log.info("OCR completed", pages=pages)

        _end_span(state, node_name, output_data={"pages": pages, "text_length": len(ocr_result.get("fullText", ""))})
        await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"pages": pages})

        return {
            "status": "ocr_done",
            "ocr_text": ocr_result.get("fullText", ""),
            "ocr_tables": ocr_result.get("tables", []),
            "messages": [{"role": "system", "content": f"Документ распознан: {len(file_bytes)} байт"}],
        }
    except Exception as e:
        log.error("OCR failed", error=str(e))
        _record_error(state, node_name, e)
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


async def extract_positions(state: DocumentState) -> DocumentState:
    """Ф2: Извлечение позиций акта через LLM-экстракцию."""
    run_id = state["run_id"]
    thread_id = state["thread_id"]
    node_name = "extract_positions"
    log = logger.bind(run_id=run_id, node=node_name)

    await event_bus.publish_node_started(thread_id, run_id, node_name)
    _start_span(state, node_name, input_data={"ocr_text_length": len(state.get("ocr_text", ""))})

    try:
        log.info("Extracting positions via LLM")

        prompt = (
            "Извлеки из текста акта все позиции. Верни JSON-массив объектов с полями: "
            "number (номер позиции), name (наименование работ/материалов), "
            "unit (ед. изм.), quantity (объём), price (цена), amount (стоимость). "
            "Извлекай строго по данным документа, не выдумывай значения.\n\n"
            f"Текст акта:\n{state['ocr_text']}"
        )

        result = await gpu_client.chat_completion(
            messages=[
                {"role": "system", "content": "Ты — точный экстрактор данных из закрывающих документов."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )

        content = result["choices"][0]["message"]["content"]
        positions = json.loads(content)

        log.info("Positions extracted", count=len(positions))

        _end_span(state, node_name, output_data={"positions_count": len(positions)})
        await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"positions_count": len(positions)})

        return {
            "status": "positions_extracted",
            "positions": positions,
            "messages": [{"role": "assistant", "content": f"Извлечено позиций: {len(positions)}"}],
        }
    except Exception as e:
        log.error("Position extraction failed", error=str(e))
        _record_error(state, node_name, e)
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


async def match_rates(state: DocumentState) -> DocumentState:
    """Ф3: Сверка позиций со ставками ЕРР (корп PG, нечёткий поиск)."""
    run_id = state["run_id"]
    thread_id = state["thread_id"]
    node_name = "match_rates"
    log = logger.bind(run_id=run_id, node=node_name)

    await event_bus.publish_node_started(thread_id, run_id, node_name)
    _start_span(state, node_name, input_data={"positions_count": len(state.get("positions", [])), "contract_id": state.get("contract_id")})

    try:
        log.info("Matching positions against rate registry")

        contract_id = state.get("contract_id")
        verification: List[Dict[str, Any]] = []
        mismatches: List[Dict[str, Any]] = []

        for pos in state["positions"]:
            rates = await corp_pg_client.get_rates_for_contract(
                contract_id=contract_id,
                as_of_date=None,
            )
            matched = _fuzzy_match_position(pos["name"], rates)

            if matched is None:
                mismatches.append({
                    "position": pos,
                    "reason": "missing_rate",
                    "confidence": 0.0,
                })
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

        log.info("Matching complete", verified=len(verification), mismatches=len(mismatches))

        _end_span(state, node_name, output_data={"verified": len(verification), "mismatches": len(mismatches)})
        await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"verified": len(verification), "mismatches": len(mismatches)})

        return {
            "status": "rates_matched",
            "verification": verification,
            "mismatches": mismatches,
            "messages": [{"role": "assistant", "content": f"Сверка: расхождений {len(mismatches)}"}],
        }
    except Exception as e:
        log.error("Rate matching failed", error=str(e))
        _record_error(state, node_name, e)
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


def _fuzzy_match_position(name: str, rates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Нечёткое сопоставление наименования позиции со ставками ЕРР."""
    from difflib import SequenceMatcher
    best, best_score = None, 0.0
    for r in rates:
        score = SequenceMatcher(None, name.lower(), r["item_name"].lower()).ratio()
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.75 else None


async def specialist_review(state: DocumentState) -> DocumentState:
    """Ф4: HITL — проверка специалистом расхождений и низкой уверенности."""
    run_id = state["run_id"]
    thread_id = state["thread_id"]
    node_name = "specialist_review"
    log = logger.bind(run_id=run_id, node=node_name)

    await event_bus.publish_node_started(thread_id, run_id, node_name)
    _start_span(state, node_name, input_data={"mismatches_count": len(state.get("mismatches", []))})

    try:
        if not state.get("mismatches"):
            log.info("No mismatches, skipping specialist review")
            _end_span(state, node_name, output_data={"auto_approved": True})
            await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"auto_approved": True})
            return {"status": "auto_approved", "specialist_decision": {"approved": True, "auto": True}}

        form_data = {
            "type": "hitl_form",
            "form_id": "specialist_verification",
            "title": "Проверка расхождений",
            "mismatches": state["mismatches"],
            "allowed_actions": ["approve", "correct", "reject"],
        }
        interrupt_id = f"specialist_{run_id}"

        # HITL-события для SSE и Langfuse
        await event_bus.publish_hitl_interrupt(thread_id, run_id, interrupt_id, form_data)
        langfuse_client.create_event(
            trace=_active_traces.get(run_id),
            name="hitl_interrupt",
            input_data={"form_id": "specialist_verification", "mismatches_count": len(state["mismatches"])},
        )

        log.info("Pausing for specialist review")
        decision = interrupt(form_data)

        await event_bus.publish_hitl_resumed(thread_id, run_id, interrupt_id, decision)
        langfuse_client.create_event(
            trace=_active_traces.get(run_id),
            name="hitl_resumed",
            output_data={"decision": decision},
        )

        _end_span(state, node_name, output_data={"decision": decision})
        await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"decision": decision})

        log.info("Specialist decision received", approved=decision.get("approved"))
        return {"status": "specialist_reviewed", "specialist_decision": decision}
    except Exception as e:
        log.error("Specialist review failed", error=str(e))
        _record_error(state, node_name, e)
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


async def generate_output(state: DocumentState) -> DocumentState:
    """Ф5: Генерация выходного шаблона SAP/Визирь в песочнице gVisor."""
    run_id = state["run_id"]
    thread_id = state["thread_id"]
    node_name = "generate_output"
    log = logger.bind(run_id=run_id, node=node_name)

    await event_bus.publish_node_started(thread_id, run_id, node_name)
    _start_span(state, node_name, input_data={"positions_count": len(state.get("positions", []))})

    try:
        log.info("Generating output template in sandbox")

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
            log.error("Sandbox generation failed", stderr=result.stderr)
            _end_span(state, node_name, output_data={"error": result.error_message}, status="error")
            await event_bus.publish_error(thread_id, run_id, Exception(result.error_message), node_name=node_name)
            return {
                "status": "failed",
                "messages": [{"role": "error", "content": result.error_message}],
            }

        output_bytes = result.artifacts.get("output.xlsx", b"")
        output_key = f"runs/{state['run_id']}/final_act.xlsx"
        await s3_client.upload_file(
            bucket=settings.seaweed_s3_bucket_factory,
            key=output_key,
            file_content=output_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        log.info("Output generated", key=output_key, size=len(output_bytes))

        _end_span(state, node_name, output_data={"artifact_key": output_key, "size": len(output_bytes)})
        await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"artifact_key": output_key, "size": len(output_bytes)})

        return {
            "status": "output_generated",
            "output_artifact_key": output_key,
            "messages": [{"role": "assistant", "content": "Шаблон SAP сформирован"}],
        }
    except Exception as e:
        log.error("Output generation failed", error=str(e))
        _record_error(state, node_name, e)
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


def _apply_specialist_corrections(
    positions: List[Dict[str, Any]], decision: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Применяет корректировки специалиста к позициям."""
    if not decision.get("corrections"):
        return positions
    corrected = [dict(p) for p in positions]
    for corr in decision["corrections"]:
        for p in corrected:
            if p["number"] == corr["position_number"]:
                p.update(corr["values"])
    return corrected


def _to_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


async def approver_sign(state: DocumentState) -> DocumentState:
    """Ф5 (часть 2): HITL — утверждение акта ЗГД с подписью."""
    run_id = state["run_id"]
    thread_id = state["thread_id"]
    node_name = "approver_sign"
    log = logger.bind(run_id=run_id, node=node_name)

    await event_bus.publish_node_started(thread_id, run_id, node_name)
    _start_span(state, node_name, input_data={"output_artifact": state.get("output_artifact_key")})

    try:
        form_data = {
            "type": "hitl_form",
            "form_id": "approver_sign",
            "title": "Утверждение акта",
            "output_artifact": state["output_artifact_key"],
            "summary": _build_summary(state),
            "allowed_actions": ["sign", "reject"],
        }
        interrupt_id = f"approver_{run_id}"

        await event_bus.publish_hitl_interrupt(thread_id, run_id, interrupt_id, form_data)
        langfuse_client.create_event(
            trace=_active_traces.get(run_id),
            name="hitl_interrupt",
            input_data={"form_id": "approver_sign"},
        )

        decision = interrupt(form_data)

        await event_bus.publish_hitl_resumed(thread_id, run_id, interrupt_id, decision)
        langfuse_client.create_event(
            trace=_active_traces.get(run_id),
            name="hitl_resumed",
            output_data={"decision": decision},
        )

        log.info("Approver decision", signed=decision.get("signed"))

        _end_span(state, node_name, output_data={"signed": decision.get("signed")})
        await event_bus.publish_node_finished(thread_id, run_id, node_name, output={"signed": decision.get("signed")})

        return {
            "status": "approved" if decision.get("signed") else "rejected",
            "approver_decision": decision,
        }
    except Exception as e:
        log.error("Approver sign failed", error=str(e))
        _record_error(state, node_name, e)
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


def _build_summary(state: DocumentState) -> Dict[str, Any]:
    return {
        "total_positions": len(state["positions"]),
        "matched": sum(1 for v in state["verification"] if v["status"] == "matched"),
        "contract_id": state.get("contract_id"),
    }


async def finalize(state: DocumentState) -> DocumentState:
    """Финализация: публикация RUN_FINISHED/RUN_FAILED, завершение trace."""
    run_id = state["run_id"]
    thread_id = state["thread_id"]
    node_name = "finalize"
    log = logger.bind(run_id=run_id, node=node_name)

    await event_bus.publish_node_started(thread_id, run_id, node_name)
    _start_span(state, node_name)

    try:
        status = state["status"]
        log.info("Finalizing run", status=status)

        if status in ("approved", "auto_approved"):
            await event_bus.publish_run_finished(thread_id, run_id, state)
        elif status in ("failed", "rejected"):
            await event_bus.publish_run_failed(
                thread_id, run_id,
                error_message=f"Run finished with status: {status}",
            )

        _end_span(state, node_name, output_data={"status": status})
        _finalize_trace(state)

        await event_bus.publish_node_finished(thread_id, run_id, node_name)

        return {
            "messages": [{"role": "system", "content": f"Ран завершён: {status}"}],
        }
    except Exception as e:
        log.error("Finalization failed", error=str(e))
        _record_error(state, node_name, e)
        await event_bus.publish_error(thread_id, run_id, e, node_name=node_name)
        raise


# ==============================================================================
# УСЛОВНЫЕ РЁБРА
# ==============================================================================


def route_after_review(state: DocumentState) -> Literal["generate_output", "finalize"]:
    """После проверки специалиста: продолжать или отклонить."""
    if state.get("specialist_decision", {}).get("approved"):
        return "generate_output"
    return "finalize"


def route_after_approve(state: DocumentState) -> Literal["finalize"]:
    """После утверждения ЗГД — всегда финализация."""
    return "finalize"


# ==============================================================================
# СБОРКА И РЕГИСТРАЦИЯ ГРАФА
# ==============================================================================


def build_document_processing_graph() -> StateGraph:
    """Сборка графа обработки ПЗД."""
    graph = StateGraph(DocumentState)

    # Узлы
    graph.add_node("load_and_ocr", load_and_ocr)
    graph.add_node("extract_positions", extract_positions)
    graph.add_node("match_rates", match_rates)
    graph.add_node("specialist_review", specialist_review)
    graph.add_node("generate_output", generate_output)
    graph.add_node("approver_sign", approver_sign)
    graph.add_node("finalize", finalize)

    # Рёбра
    graph.add_edge(START, "load_and_ocr")
    graph.add_edge("load_and_ocr", "extract_positions")
    graph.add_edge("extract_positions", "match_rates")
    graph.add_edge("match_rates", "specialist_review")
    graph.add_conditional_edges("specialist_review", route_after_review)
    graph.add_edge("generate_output", "approver_sign")
    graph.add_conditional_edges("approver_sign", route_after_approve)
    graph.add_edge("finalize", END)

    return graph


# Регистрация графа в реестре при импорте модуля
from worker.app.graphs.registry import graph_registry  # noqa: E402

graph_registry.register(
    "document_processing",
    build_document_processing_graph(),
)