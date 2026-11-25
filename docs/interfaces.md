# ИНТЕРФЕЙСЫ
## Операционная панель для управления агентами · Blueprint v0.2

---

## 1. Назначение

Единое рабочее место оператора фабрики агентов: запуск сценариев, наблюдение за живыми ранами, человеческие подтверждения, просмотр результатов и история. Панель предметно-независима: подходит для документов, согласований, отчётности и любых сценариев фабрики.

---

## 2. Роль: рабочее место оператора

Оператор не «пользователь чата», а **диспетчер производства**:

- выбирает и запускает сценарий из каталога;
- наблюдает стрим выполнения: текст, размышления, инструменты, шаги;
- принимает решения в точках HITL: сравнение, расхождения, утверждение/отклонение;
- видит последствия: что исполнено, какой эффект, кто и когда утвердил;
- управляет долгими ранами: паузы, реконнект, возврат к ожиданиям;
- работает с историей и настройками;
- контролирует здоровье контуров через сводку.

---

## 3. Принципы

1. **Состояния важнее декора.** Каждый блок всегда показывает, что с ним происходит.
2. **Живой процесс, а не страница.** Стрим, шаги, реконнект, паузы — первоклассные объекты.
3. **Доказательность решений.** В HITL видны источник, извлечённое, расхождение и последствия.
4. **Один эффект на действие.** Повторное утверждение визуально и функционально блокируется.
5. **Совместимость со стеком.** Стили читаются shadcn и ассистент-компонентами без ручного «прикручивания».
6. **Операторская плотность.** Клавиатура, компактный режим, табличные числа, никакого лишнего движения.
7. **Доказательность через трейс.** Из любого рана — прямой переход в Langfuse по `trace_id`; аудит HITL и доступ к корпоративным данным видны в UI без переключения в отдельные системы.
8. **Живучесть состояния.** Реконнект, офлайн-вкладка, рестарт воркера — UI показывает, что происходит, и не теряет контекст оператора.
9. **i18n-ready.** Все строки — через словарь локализации; формат чисел, дат и валют — из локали, не из хардкода (согласовано с `io.locale` манифестов).
10. **Бюджеты производительности.** LCP/INP/CLS/размер бандла — зафиксированы как DoD для каждого релиза; измеряются в CI.

---

## 4. Состав панели

| Зона | Назначение |
|---|---|
| **Вход и доступ** | Загрузка, 401-редирект, «нет доступа», пустой каталог |
| **Каталог сценариев** | Карточки/строки, статусы, фильтры, запуск, история |
| **Ран / чат** | Лента, стрим, размышления, инструменты, шаги, композер |
| **HITL-формы** | Diff-сравнение, гейт, утверждение/отклонение, аудит |
| **История ранов** | Список, статусы, фильтры, возврат к паузам |
| **Боковая панель** | Текущий ран, шаги, источники, аудит, действия |
| **Настройки** | Тема, плотность, размышления, автопрокрутка, формат |
| **Служебные состояния** | Скелетоны, ошибки, пусто, реконнект, офлайн-но-жив |
| **Dashboard / сводка** | Агрегированная картина: активные раны, HITL-очередь, здоровье контуров, метрики за смену |
| **Command palette (Cmd+K)** | Быстрый переход между сценариями/ранами/экранами без мыши |
| **Toast-область** | Уведомления о реконнекте, ошибках, завершении ранов, HITL-событиях |
| **Confirmation dialogs** | Модальные подтверждения деструктивных действий (отмена рана, отклонение HITL, удаление) |
| **Trace panel** | Встроенный просмотр Langfuse-трейда из рана (ссылка + ключевые спаны) |
| **Dry-run panel** | Запуск сценария без побочных эффектов с визуализацией плана исполнения |

---

## 5. Словарь состояний

### Ран
- `queued` — задача в шине, воркер ещё не взял.
- `running` — граф исполняется, события идут в стрим.
- `waiting-hitl` — граф остановлен на interrupt, ждёт решения оператора.
- `paused` — оператор или система приостановили (не HITL).
- `done` — граф завершился успешно.
- `failed` — граф завершился с ошибкой.
- `cancelled` — оператор отменил ран.
- `offline-alive` — вкладка закрыта/свернута, но ран идёт на бэкенде (чекпоинты в PG, события буферизуются в Streams); при возврате — реконнект по Last-Event-ID + снапшот.

### Стрим
- `streaming` — модель генерирует токены.
- `thinking` — агент в фазе размышления (reasoning).
- `tool-running` — инструмент исполняется (песочница/воркер).
- `tool-done` — инструмент завершился успешно.
- `tool-error` — инструмент завершился с ошибкой.

### HITL
- `pending` — форма показана, решение не принято.
- `locked` — утверждение в процессе (`idempotency-key` зафиксирован).
- `approved` — оператор утвердил, эффект применён.
- `rejected` — оператор отклонил.
- `danger` — форма содержит расхождения, требующие внимания.
- `audit` — показан аудит: кто, когда, что утвердил.

### Служебные
- `loading` — первичная загрузка данных.
- `empty` — нет данных (каталог, история).
- `error` — некритичная ошибка UI (error boundary).
- `reconnecting` — SSE-соединение потеряно, идёт реконнект (Last-Event-ID + снапшот из чекпоинта).
- `no-access` — у пользователя нет доступа ни к одному сценарию.
- `deprecated` — сценарий/алгоритм помечен `lifecycle: deprecated`.

---

## 6. Стилевой слой: роль `globals.css`

`globals.css` — **стилевое приложение панели, не библиотека контролов**.

Содержит:
- бренд-токены (шоколад/золото);
- роли shadcn (`background / foreground / card / primary / muted / border / ring` + семантика);
- системные токены (z-index, shadows, easing, duration, типографика);
- специфичные для проекта компоненты (лента, HITL, diff, шаги, стрим, сценарии, задачи, композер, markdown, dashboard, toast, dialog, command-palette, empty-state, error-boundary);
- состояния из §5 через `data-state` / `data-tone`;
- режимы: светлая/тёмная тема, компактная плотность, reduced-motion, клавиатурный фокус, print, forced-colors.

**Стандартные контролы** (кнопки, карточки, поля ввода, таблицы, переключатели, модалки, дропдауны, тултипы, вкладки) — **ТОЛЬКО через shadcn/ui**. `globals.css` не дублирует их классы.

**Ограничения:**
- без внешних шрифтов и CDN (Air-Gap);
- браузерная матрица (§9);
- числа — `tabular-nums`;
- фокус видим всегда;
- WCAG 2.2 AA (§12).

---

## 7. Маппинг AG-UI events → UI states

События протокола AG-UI 1.0.0 (`ag-ui-langgraph==0.0.47`) маппятся на состояния панели детерминированно. Маппинг — единственный источник истины для UI-слоя.

| AG-UI event | UI state | Действие UI |
|---|---|---|
| `RUN_STARTED` | `running` | показать стрим, скрыть HITL-форму |
| `TEXT_MESSAGE_START` | `streaming` | открыть bubble агента, показать stream-caret |
| `TEXT_MESSAGE_CONTENT` | `streaming` | дописать токены в bubble |
| `TEXT_MESSAGE_END` | `streaming → idle` | убрать caret, зафиксировать bubble |
| `REASONING_START` / `REASONING_END` | `thinking` | раскрыть/свернуть блок `.thought` |
| `TOOL_CALL_START` | `tool-running` | открыть `.tool-card`, показать spinner |
| `TOOL_CALL_ARGS` | `tool-running` | дописать аргументы в `.tool-body` |
| `TOOL_CALL_END` | `tool-done` | закрыть карточку, показать результат |
| `TOOL_CALL_ERROR` | `tool-error` | показать `.banner[tone=danger]` в карточке |
| `STATE_SNAPSHOT` | (восстановление) | перерисовать ленту из снапшота |
| `STATE_DELTA` | (восстановление) | применить дельту к состоянию |
| `STEP_STARTED` | `step.active` | подсветить текущий шаг в `.steps` |
| `STEP_FINISHED` | `step.done` | погасить шаг |
| `RUN_FINISHED` | `done` | показать итог, разблокировать композер |
| `RUN_ERROR` | `failed` | показать `.banner[tone=danger]` + trace-link |
| `RUN_FINISHED` (hitl) | `waiting-hitl` | раскрыть `.hitl`-форму с A2UI-контрактом (§8) |

### Реконнект
1. Потеря SSE → UI state = `reconnecting`, показать `.banner[tone=warning]`.
2. Повторная подписка с `Last-Event-ID`.
3. Если буфер Streams обрезан — запрос снапшота из чекпоинта PG.
4. Дедупликация по Stream ID, перерисовка ленты.
5. Возврат к живому хвосту → UI state восстанавливается.

### Experimental interrupts
AG-UI-адаптер assistant-ui имеет experimental-статус для interrupts. Приёмка — на эталонном графе (§5.6 SSOT). Fallback: если interrupt не поддерживается, показать `.banner[tone=warning]` «HITL недоступен в этой версии адаптера» и заблокировать запуск сценариев с hitl-узлами.

---

## 8. Контракт A2UI-форм

A2UI-формы (`a2ui/diff` и расширения через `ag-ui-a2ui-toolkit==0.0.6`) — способ рендеринга HITL-форм в ленте.

### Контракт

1. **Бэкенд отправляет форму** как часть `RUN_FINISHED` с типом `hitl` + payload:
   ```json
   {
     "form_type": "a2ui/diff",
     "schema": { ... JSON Schema ... },
     "source": { ... данные из источника ... },
     "extracted": { ... данные от агента ... },
     "diff": [ ... массив расхождений ... ],
     "idempotency_key": "uuid-v4"
   }
   ```

2. **UI рендерит форму** через компонент `<A2UIForm />`:
   - валидирует `schema` (ajv / zod);
   - рендерит `.diff-table` с `.diff-add` / `.diff-del`;
   - показывает `.gate` для подтверждения;
   - блокирует повторное утверждение после `locked` / `approved` / `rejected`.

3. **Утверждение** отправляется как `RESUME` с `idempotency_key`. Повторный вызов с тем же ключом — детерминированный отказ на бэкенде (дедуп-таблица в реестре ранов, §3.1 SSOT).

4. **Аудит**: кто, когда, что утвердил — извлекается из чекпоинтов PG и показывается в `.audit`-бейдже.

### Расширения
Новые типы форм (`a2ui/table`, `a2ui/chart` и т.д.) регистрируются в компонентном каталоге (Storybook, §15) и маппятся по `form_type`.

---

## 9. Browser matrix и поддержка

Минимальные версии браузеров (корпоративная среда, Air-Gap):

| Браузер | Минимальная версия | Обоснование |
|---|---|---|
| Chrome | 111+ | `color-mix()`, `@container`, `@layer` |
| Edge | 111+ | Chromium-based |
| Firefox | 113+ | `color-mix()`, `@container` |
| Safari | 16.2+ | `color-mix()` |
| Safari iOS | 16.2+ | аналогично |

### Запрещено
- внешние CDN (шрифты, иконки, скрипты);
- телеметрия браузерных вендоров;
- экспериментальные флаги (если не зафиксировано в DoD).

### Feature detection
- `color-mix()` — обязательный (CSS-переменные с fallback на hardcoded `rgba`);
- `@layer` — обязательный (порядок слоёв);
- `@container` — опциональный (fallback на media queries);
- `:has()` — опциональный (fallback на JS-классы).

### Проверка в CI
- Playwright с матрицей браузеров;
- Lighthouse CI с budget-ами (§13).

---

## 10. Технический стек фронтенда

| Слой | Решение | Обоснование |
|---|---|---|
| Фреймворк | React 19.3.1 | зафиксировано в SSOT §2 |
| Язык | TypeScript 5.x | зафиксировано в SSOT §2 |
| UI-библиотека | shadcn/ui | совместимость с ролями `globals.css` |
| AG-UI клиент | `@ag-ui/client` ≥1.0.3 | зафиксировано в SSOT §2 |
| AG-UI React | `@assistant-ui/react-ag-ui` | зафиксировано в SSOT §2 |
| State management | Zustand | лёгкий, предсказуемый, без boilerplate |
| Routing | TanStack Router | type-safe, встроенный data loading |
| Data fetching | TanStack Query | кэширование, ревалидация, optimistic |
| Forms | React Hook Form + Zod | валидация HITL-форм |
| i18n | `react-i18next` (или `next-intl`) | словарь локализации, форматирование |
| Error tracking | Sentry (self-hosted) | Air-Gap, интеграция с Langfuse |
| Testing | Vitest + Playwright | unit + e2e |
| Visual regression | Chromatic (или Percy) | CSS-регрессии |
| Storybook | 8.x | компонентный каталог (§15) |

### Запрещено
- CopilotKit / Copilot Runtime (SSOT §2);
- Redux (избыточен для текущего скоупа);
- MobX (непредсказуем при стриминге);
- любые vendor-рантаймы UI.

---

## 11. Наблюдаемость в UI

### Langfuse-трейс
- из любого рана — кнопка «Открыть в Langfuse»;
- URL: `https://langfuse.internal/trace/{trace_id}`;
- `trace_id` передаётся в AG-UI events (`STATE_SNAPSHOT` / `RUN_STARTED`);
- UI показывает ключевые спаны: длительность, токены, ошибки.

### Error tracking (Sentry)
- self-hosted Sentry в Air-Gap (ВМ №1);
- интеграция с Langfuse: `error_id` → `trace_id`;
- UI: кнопка «Сообщить о проблеме» в error-boundary;
- контекст: `user_id`, `run_id`, `trace_id`, last AG-UI event.

### Метрики UI
- Web Vitals (LCP/INP/CLS) — отправка в Langfuse через OTel;
- ошибки реконнекта — счётчик в dashboard;
- время до первого HITL-решения — метрика пилота.

---

## 12. Accessibility

**Целевой уровень:** WCAG 2.2 AA (обязательно для корпоративной среды).

### Требования
- контраст текста: ≥4.5:1 (обычный), ≥3:1 (крупный);
- контраст UI-компонентов: ≥3:1;
- клавиатурная навигация: все интерактивные элементы достижимы;
- фокус видим всегда (`:focus-visible`, §6);
- skip-links: переход к основному контенту;
- aria-атрибуты: роли, состояния, свойства — для всех кастомных компонентов;
- reduced-motion: уважение `prefers-reduced-motion` (§6);
- forced-colors: поддержка Windows High Contrast Mode;
- screen readers: тестирование на NVDA + JAWS.

### Проверка в CI
- `axe-core` в Playwright;
- Lighthouse accessibility audit ≥90.

---

## 13. Performance budgets

Бюджеты фиксируются как DoD для каждого релиза:

| Метрика | Budget | Обоснование |
|---|---|---|
| LCP | ≤2.5s | операторская панель, не маркетинг |
| INP | ≤200ms | интерактивность критична |
| CLS | ≤0.1 | стабильность layout |
| TTFB | ≤800ms | nginx + статика |
| Bundle size | ≤500KB (gzip) | без vendor-рантаймов |
| First paint | ≤1.5s | критичный CSS inline |

### Измерение
- Lighthouse CI в pipeline;
- Web Vitals в Sentry;
- ручное тестирование на корпоративных машинах (i5-8th gen, 8GB RAM).

### Деградация
- если budget превышен — блокировка merge;
- исключение — только с утверждением архитектора.

---

## 14. i18n

### Локализация
- все строки — через словарь (`ru-RU`, `en-US`);
- формат чисел, дат, валют — из локали (`Intl.NumberFormat`, `Intl.DateTimeFormat`);
- согласовано с `io.locale` манифестов (§3.7.3 SSOT).

### Словарь
- файлы: `locales/ru.json`, `locales/en.json`;
- ключи: `snake_case`, группировка по экранам;
- запрет хардкода строк в компонентах.

### Переключение
- из настроек UI (§4);
- сохранение в `localStorage` + проброс в API (`Accept-Language`).

### RTL
- не поддерживается в текущем скоупе (корпоративная среда РФ/СНГ).

---

## 15. Storybook и visual regression

### Storybook (8.x)
- компонентный каталог для всех UI-компонентов;
- stories для всех состояний из §5;
- интеграция с `globals.css` (токены, темы);
- документация: props, примеры, accessibility notes.

### Visual regression
- Chromatic (или self-hosted Percy в Air-Gap);
- скриншоты всех stories в обоих темах (light/dark);
- порог различия: 0.1%;
- approve — только после ревью дизайнером/архитектором.

### Интеграция с CI
- build storybook в pipeline;
- visual regression — отдельный job;
- блокировка merge при различиях > порога.

---

## 16. Dry-run режим

**Dry-run** — запуск сценария без побочных эффектов:
- граф исполняется, но side-effect узлы пропускаются;
- HITL-формы показываются, но утверждение не применяет эффект;
- в конце — отчёт: что было бы исполнено, какие данные изменены.

### Использование
- отладка сценариев;
- обучение операторов;
- проверка манифестов перед публикацией.

### UI
- переключатель «Dry-run» в композере (§4);
- визуальная метка: `.banner[tone=info]` «Режим dry-run активен»;
- в истории ранов — бейдж «dry-run».

### Ограничения
- dry-run не доступен для сценариев с `requires.corp` (чтение корп. данных);
- dry-run не доступен для сценариев с `requires.gpu` (инференс).

---

## 17. Границы

### Панель не управляет
- доступами (только AD/Keycloak, §3.7.2 SSOT);
- обучением моделей;
- инфраструктурой (ВМ, контейнеры, сети);
- манифестами (редактирование — в репозитории, §3.7 SSOT).

### Панель НЕ является
- админкой инфраструктуры;
- дизайнером сценариев (no-code);
- чатом с ИИ (это побочный эффект, не цель).

---

## Приложение A. `globals.css`

```css
/* ============================================================
   globals.css — «Операционная панель для управления агентами»
   Производная исходной темы (шоколад/золото).

   Содержит:
   - бренд-токены;
   - роли shadcn (background/foreground/card/primary/muted/border/ring);
   - системные токены (z-index, shadows, easing, duration, типографика);
   - специфичные для проекта компоненты (лента, HITL, diff, шаги,
     стрим, сценарии, задачи, композер, markdown, dashboard, toast,
     dialog, command-palette, empty-state, error-boundary);
   - состояния через data-state/data-tone;
   - режимы: light/dark, compact, reduced-motion, print, forced-colors.

   Стандартные контролы (кнопки, карточки, поля, таблицы, переключатели,
   модалки, дропдауны, тултипы, вкладки) — ТОЛЬКО через shadcn/ui.
   ============================================================ */

@import "tailwindcss";

@custom-variant dark (&:where(.dark, .dark *));

/* ============================================================
   1. БРЕНД-ТОКЕНЫ
   ============================================================ */
@theme {
  /* Поверхности */
  --color-bg-primary: #f6f6f6;
  --color-bg-secondary: #ffffff;
  --color-bg-tertiary: #eaeaea;

  /* Текст */
  --color-text-primary: #202020;
  --color-text-secondary: #5b5b66;

  /* Брендовый акцент */
  --color-border: #cccccc;
  --color-accent: #7a6748;
  --color-accent-hover: #6b5a40;
  --color-gold-soft: #c9a674;

  /* Семантика */
  --color-success: #4a6b4a;
  --color-warning: #96551d;
  --color-danger: #8a3a3a;
  --color-info: #35688a;

  /* Геометрия */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
}

/* ============================================================
   2. РОЛИ (совместимость с shadcn)
   ============================================================ */
:root {
  color-scheme: light;

  /* Плотность */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;

  /* Поверхности */
  --background: var(--color-bg-primary);
  --foreground: var(--color-text-primary);
  --card: var(--color-bg-secondary);
  --card-foreground: var(--color-text-primary);
  --muted: var(--color-bg-tertiary);
  --muted-foreground: var(--color-text-secondary);
  --border: var(--color-border);
  --input: var(--color-border);

  /* Акценты */
  --primary: var(--color-accent);
  --primary-foreground: #ffffff;
  --secondary: var(--color-bg-tertiary);
  --secondary-foreground: var(--color-text-primary);
  --accent-surface: color-mix(in srgb, var(--color-accent) 12%, var(--color-bg-secondary));
  --accent-surface-foreground: var(--color-text-primary);

  /* Семантика */
  --destructive: var(--color-danger);
  --destructive-foreground: #ffffff;
  --success: var(--color-success);
  --warning: var(--color-warning);
  --info: var(--color-info);
  --ring: var(--color-accent);

  /* Z-index */
  --z-dropdown: 100;
  --z-sticky: 200;
  --z-fixed: 300;
  --z-modal-backdrop: 400;
  --z-modal: 500;
  --z-popover: 600;
  --z-tooltip: 700;
  --z-toast: 800;

  /* Shadows */
  --shadow-xs: 0 1px 2px rgb(0 0 0 / .05);
  --shadow-sm: 0 2px 4px rgb(0 0 0 / .08);
  --shadow-md: 0 4px 8px rgb(0 0 0 / .12);
  --shadow-lg: 0 8px 16px rgb(0 0 0 / .16);
  --shadow-xl: 0 12px 24px rgb(0 0 0 / .20);

  /* Easing */
  --ease-in: cubic-bezier(0.4, 0, 1, 1);
  --ease-out: cubic-bezier(0, 0, 0.2, 1);
  --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
  --ease-bounce: cubic-bezier(0.68, -0.55, 0.265, 1.55);

  /* Duration */
  --duration-fast: 100ms;
  --duration-normal: 150ms;
  --duration-slow: 250ms;
  --duration-slower: 350ms;

  /* Typography scale */
  --text-xs: 11px;
  --text-sm: 12.5px;
  --text-base: 13.5px;
  --text-md: 14px;
  --text-lg: 16px;
  --text-xl: 18px;
  --text-2xl: 20px;
  --text-3xl: 24px;
}

.dark {
  color-scheme: dark;

  /* Шоколадная сторона */
  --color-bg-primary: #0f0e0d;
  --color-bg-secondary: #1a1816;
  --color-bg-tertiary: #252220;
  --color-text-primary: #f0ece6;
  --color-text-secondary: #a09688;
  --color-border: #3d352d;
  --color-accent: #b08d57;
  --color-accent-hover: #c9a674;
  --color-gold-soft: #8a6d3d;

  /* Семантика в тёмной теме */
  --color-success: #86c07a;
  --color-warning: #e0a33e;
  --color-danger: #e07a6a;
  --color-info: #7cc0e8;

  /* Роли */
  --primary-foreground: #171310;
  --destructive-foreground: #241110;
  --accent-surface: color-mix(in srgb, var(--color-accent) 14%, var(--color-bg-secondary));
}

/* Компактный режим оператора */
[data-density="compact"] {
  --space-1: 3px;
  --space-2: 6px;
  --space-3: 8px;
  --space-4: 10px;
  --space-5: 14px;
  --radius-md: 10px;
  --radius-lg: 12px;
}

/* Привязка ролей к утилитам Tailwind */
@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-accent-surface: var(--accent-surface);
  --color-accent-surface-foreground: var(--accent-surface-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-success: var(--success);
  --color-warning: var(--warning);
  --color-info: var(--info);
  --color-ring: var(--ring);
}

/* ============================================================
   3. БАЗА
   ============================================================ */
@layer base {
  body {
    @apply bg-background text-foreground antialiased;
    font-family: ui-sans-serif, system-ui, sans-serif;
    line-height: 1.5;
  }

  :focus-visible {
    outline: 2px solid var(--ring);
    outline-offset: 2px;
    border-radius: 6px;
  }

  ::selection {
    background: color-mix(in srgb, var(--color-accent) 30%, transparent);
  }

  .num {
    font-variant-numeric: tabular-nums;
    text-align: right;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .92em;
  }
}

/* ============================================================
   4. КОМПОНЕНТЫ
   ============================================================ */
@layer components {

  /* --- Бейджи состояний --- */
  .badge {
    @apply inline-flex items-center gap-1.5 rounded-full border font-bold;
    padding: 3px 10px;
    font-size: var(--text-xs);
    letter-spacing: .02em;
    white-space: nowrap;
  }

  .badge::before {
    content: "";
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
  }

  .badge[data-state="running"] {
    color: var(--info);
    background: color-mix(in srgb, var(--info) 11%, transparent);
    border-color: color-mix(in srgb, var(--info) 34%, transparent);
  }

  .badge[data-state="waiting"],
  .badge[data-state="waiting-hitl"],
  .badge[data-state="paused"] {
    color: var(--warning);
    background: color-mix(in srgb, var(--warning) 11%, transparent);
    border-color: color-mix(in srgb, var(--warning) 36%, transparent);
  }

  .badge[data-state="done"],
  .badge[data-state="approved"],
  .badge[data-state="success"] {
    color: var(--success);
    background: color-mix(in srgb, var(--success) 11%, transparent);
    border-color: color-mix(in srgb, var(--success) 34%, transparent);
  }

  .badge[data-state="failed"],
  .badge[data-state="rejected"],
  .badge[data-state="error"],
  .badge[data-state="danger"] {
    color: var(--destructive);
    background: color-mix(in srgb, var(--destructive) 11%, transparent);
    border-color: color-mix(in srgb, var(--destructive) 36%, transparent);
  }

  .badge[data-state="queued"],
  .badge[data-state="muted"],
  .badge[data-state="deprecated"] {
    color: var(--muted-foreground);
    background: var(--muted);
    border-color: var(--border);
  }

  .badge[data-state="gold"] {
    color: var(--color-accent);
    background: color-mix(in srgb, var(--color-accent) 13%, transparent);
    border-color: color-mix(in srgb, var(--color-accent) 35%, transparent);
  }

  .badge[data-state="offline-alive"] {
    color: var(--color-accent);
    background: color-mix(in srgb, var(--color-accent) 13%, transparent);
    border-color: color-mix(in srgb, var(--color-accent) 35%, transparent);
  }

  /* --- Баннеры --- */
  .banner {
    @apply flex items-start gap-2 border;
    border-radius: var(--radius-md);
    padding: 10px var(--space-3);
    font-size: var(--text-base);
  }

  .banner[data-tone="info"] {
    color: var(--info);
    background: color-mix(in srgb, var(--info) 10%, transparent);
    border-color: color-mix(in srgb, var(--info) 34%, transparent);
  }

  .banner[data-tone="warning"] {
    color: var(--warning);
    background: color-mix(in srgb, var(--warning) 10%, transparent);
    border-color: color-mix(in srgb, var(--warning) 36%, transparent);
  }

  .banner[data-tone="danger"] {
    color: var(--destructive);
    background: color-mix(in srgb, var(--destructive) 10%, transparent);
    border-color: color-mix(in srgb, var(--destructive) 36%, transparent);
  }

  .banner[data-tone="success"] {
    color: var(--success);
    background: color-mix(in srgb, var(--success) 10%, transparent);
    border-color: color-mix(in srgb, var(--success) 34%, transparent);
  }

  /* --- Шаги рана --- */
  .steps {
    display: grid;
    gap: 10px;
  }

  .step {
    display: grid;
    grid-template-columns: 14px 1fr auto;
    gap: 10px;
    align-items: center;
    font-size: var(--text-base);
  }

  .step .dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--border);
  }

  .step[data-state="done"] .dot {
    background: var(--success);
  }

  .step[data-state="active"] .dot {
    background: var(--info);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--info) 18%, transparent);
  }

  .step[data-state="waiting"] .dot {
    background: var(--warning);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--warning) 18%, transparent);
  }

  .step[data-state="future"] {
    color: var(--muted-foreground);
  }

  /* --- Лента: сообщения --- */
  .msg {
    display: grid;
    gap: 6px;
    max-width: min(92%, 820px);
  }

  .msg.user {
    align-self: flex-end;
    margin-left: auto;
  }

  .msg.agent {
    align-self: flex-start;
  }

  .msg-bubble {
    @apply bg-card border border-border;
    border-radius: var(--radius-lg);
    padding: var(--space-3);
  }

  .msg.user .msg-bubble {
    background: color-mix(in srgb, var(--primary) 12%, var(--card));
  }

  .msg-meta {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    font-size: var(--text-xs);
    color: var(--muted-foreground);
  }

  /* --- Размышление агента --- */
  .thought {
    @apply text-muted-foreground border border-dashed border-border bg-muted;
    border-radius: var(--radius-md);
    padding: 10px var(--space-3);
    font-size: var(--text-base);
  }

  /* --- Карточка инструмента --- */
  .tool-card {
    @apply border border-border bg-card overflow-hidden;
    border-radius: var(--radius-md);
  }

  .tool-head {
    @apply flex items-center justify-between gap-2 bg-muted border-b border-border;
    padding: 9px var(--space-3);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: var(--text-sm);
  }

  .tool-body {
    padding: 10px var(--space-3);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: var(--text-sm);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: var(--muted-foreground);
  }

  /* --- HITL: форма подтверждения --- */
  .hitl {
    @apply bg-card border;
    display: grid;
    gap: var(--space-3);
    border-radius: var(--radius-lg);
    padding: var(--space-3);
    border-color: color-mix(in srgb, var(--warning) 42%, var(--border));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--warning) 9%, transparent);
  }

  .hitl[data-state="approved"] {
    border-color: color-mix(in srgb, var(--success) 42%, var(--border));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 9%, transparent);
  }

  .hitl[data-state="rejected"] {
    border-color: color-mix(in srgb, var(--destructive) 42%, var(--border));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--destructive) 9%, transparent);
  }

  .hitl[data-state="locked"] {
    opacity: .92;
  }

  .diff-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-base);
  }

  .diff-table th,
  .diff-table td {
    border: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
    padding: 8px 10px;
    vertical-align: top;
  }

  .diff-table th {
    @apply bg-muted text-muted-foreground text-left;
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: .04em;
  }

  .diff-add {
    background: color-mix(in srgb, var(--success) 14%, transparent);
    color: var(--success);
    font-weight: 700;
  }

  .diff-del {
    background: color-mix(in srgb, var(--destructive) 12%, transparent);
    color: var(--destructive);
    text-decoration: line-through;
  }

  .gate {
    @apply flex items-start gap-2.5 border border-dashed border-border text-foreground;
    border-radius: var(--radius-md);
    padding: 10px var(--space-3);
    font-size: var(--text-base);
    cursor: pointer;
    user-select: none;
  }

  .gate input {
    margin-top: 2px;
    accent-color: var(--primary);
  }

  .gate.error {
    border-color: var(--destructive);
    animation: shake var(--duration-slow) var(--ease-in-out);
  }

  @keyframes shake {
    25% { transform: translateX(-3px); }
    75% { transform: translateX(3px); }
  }

  .audit {
    @apply text-muted-foreground border border-dashed border-border;
    width: max-content;
    max-width: 100%;
    border-radius: 999px;
    padding: 6px 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: var(--text-xs);
  }

  /* --- Стрим и загрузка --- */
  .stream-caret {
    display: inline-block;
    width: 8px;
    height: 1em;
    margin-left: 3px;
    vertical-align: -2px;
    background: var(--color-accent);
    animation: blink 1s steps(2) infinite;
  }

  @keyframes blink {
    50% { opacity: 0; }
  }

  .skeleton {
    height: 12px;
    border-radius: 8px;
    background: linear-gradient(
      90deg,
      var(--muted),
      color-mix(in srgb, var(--border) 55%, var(--muted)),
      var(--muted)
    );
    background-size: 200% 100%;
    animation: shimmer 1.3s linear infinite;
  }

  @keyframes shimmer {
    to { background-position: -200% 0; }
  }

  .spinner {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid currentColor;
    border-top-color: transparent;
    animation: spin .7s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  /* --- Каталог: карточка сценария --- */
  .scenario-card {
    @apply bg-card border border-border;
    border-radius: var(--radius-lg);
  }

  .scenario-button {
    @apply w-full flex items-center gap-3 text-left hover:bg-accent-surface;
    padding: var(--space-3);
    border-radius: var(--radius-md);
    transition: background-color var(--duration-normal) var(--ease-in-out);
  }

  .scenario-button[aria-current="true"] {
    background: color-mix(in srgb, var(--color-accent) 12%, var(--card));
    box-shadow: inset 2px 0 0 var(--color-accent);
  }

  .scenario-button.is-disabled {
    opacity: .6;
    pointer-events: none;
  }

  /* --- Реестр: строка рана --- */
  .task-item {
    @apply w-full flex items-center gap-3 border-l-2 border-transparent bg-card hover:bg-accent-surface;
    border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3);
    margin-bottom: var(--space-1);
    font-size: var(--text-base);
    cursor: pointer;
    transition:
      background-color var(--duration-normal) var(--ease-in-out),
      border-color var(--duration-normal) var(--ease-in-out);
  }

  .task-item[data-active="true"] {
    border-left-color: var(--color-accent);
    background: color-mix(in srgb, var(--color-accent) 10%, var(--muted));
    font-weight: 700;
  }

  /* --- Композер --- */
  .composer {
    @apply bg-card border-t border-border;
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: var(--space-2);
    align-items: center;
    padding: var(--space-3);
  }

  /* --- Markdown ответа агента --- */
  .markdown-body-clean {
    font-size: var(--text-md);
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .markdown-body-clean :where(h1, h2, h3, h4, h5, h6) {
    @apply font-bold text-foreground;
    margin: var(--space-4) 0 var(--space-2);
  }

  .markdown-body-clean h2 {
    font-size: var(--text-xl);
    border-bottom: 1px solid var(--border);
    padding-bottom: 4px;
  }

  .markdown-body-clean h3 {
    font-size: var(--text-lg);
    color: var(--color-accent);
  }

  .markdown-body-clean p {
    margin-bottom: var(--space-3);
  }

  .markdown-body-clean ul {
    list-style: disc;
    padding-left: 22px;
    margin-bottom: var(--space-3);
  }

  .markdown-body-clean ol {
    list-style: decimal;
    padding-left: 22px;
    margin-bottom: var(--space-3);
  }

  .markdown-body-clean li {
    margin-top: 4px;
  }

  .markdown-body-clean a {
    color: var(--color-accent);
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  .markdown-body-clean code {
    @apply bg-muted font-mono;
    padding: 2px 6px;
    border-radius: 6px;
    font-size: .9em;
  }

  .markdown-body-clean pre {
    @apply bg-muted border border-border overflow-x-auto;
    border-radius: var(--radius-md);
    padding: var(--space-3);
    margin: var(--space-2) 0 var(--space-3);
    font-size: var(--text-sm);
  }

  .markdown-body-clean pre code {
    padding: 0;
    background: transparent;
  }

  .markdown-body-clean blockquote {
    @apply text-muted-foreground border-l-2 italic;
    border-left-color: var(--color-accent);
    padding-left: var(--space-3);
    margin: var(--space-3) 0;
  }

  .markdown-body-clean table {
    display: block;
    width: max-content;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    margin: var(--space-2) 0 var(--space-3);
    font-size: var(--text-base);
  }

  .markdown-body-clean th {
    @apply bg-muted font-bold text-left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }

  .markdown-body-clean td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }

  .markdown-body-clean tr:last-child td {
    border-bottom: 0;
  }

  .markdown-body-clean li:has(> input[type="checkbox"]) {
    list-style: none;
    margin-left: -1.25rem;
  }

  .markdown-body-clean input[type="checkbox"] {
    accent-color: var(--primary);
    margin-right: 8px;
  }

  /* --- Empty state --- */
  .empty-state {
    @apply flex flex-col items-center justify-center text-center;
    padding: var(--space-5) var(--space-4);
    gap: var(--space-3);
  }

  .empty-state-icon {
    @apply text-muted-foreground;
    width: 48px;
    height: 48px;
  }

  .empty-state-title {
    @apply font-semibold text-foreground;
    font-size: var(--text-lg);
  }

  .empty-state-description {
    @apply text-muted-foreground;
    font-size: var(--text-base);
    max-width: 400px;
  }

  /* --- Error boundary --- */
  .error-boundary {
    @apply flex flex-col items-center justify-center text-center;
    padding: var(--space-5) var(--space-4);
    gap: var(--space-3);
    background: color-mix(in srgb, var(--destructive) 5%, var(--card));
    border: 1px solid color-mix(in srgb, var(--destructive) 20%, var(--border));
    border-radius: var(--radius-lg);
  }

  .error-boundary-title {
    @apply font-semibold text-destructive;
    font-size: var(--text-lg);
  }

  .error-boundary-message {
    @apply text-muted-foreground;
    font-size: var(--text-base);
    max-width: 500px;
  }

  /* --- Toast --- */
  .toast-container {
    position: fixed;
    bottom: var(--space-4);
    right: var(--space-4);
    z-index: var(--z-toast);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    max-width: 400px;
  }

  .toast {
    @apply bg-card border border-border;
    border-radius: var(--radius-md);
    padding: var(--space-3);
    box-shadow: var(--shadow-lg);
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    animation: slide-in var(--duration-slow) var(--ease-out);
  }

  @keyframes slide-in {
    from {
      transform: translateX(100%);
      opacity: 0;
    }
    to {
      transform: translateX(0);
      opacity: 1;
    }
  }

  .toast[data-tone="success"] {
    border-left: 3px solid var(--success);
  }

  .toast[data-tone="warning"] {
    border-left: 3px solid var(--warning);
  }

  .toast[data-tone="danger"] {
    border-left: 3px solid var(--destructive);
  }

  .toast[data-tone="info"] {
    border-left: 3px solid var(--info);
  }

  /* --- Dialog / Modal --- */
  .dialog-backdrop {
    position: fixed;
    inset: 0;
    background: rgb(0 0 0 / .5);
    z-index: var(--z-modal-backdrop);
    display: flex;
    align-items: center;
    justify-content: center;
    animation: fade-in var(--duration-normal) var(--ease-out);
  }

  @keyframes fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .dialog {
    @apply bg-card border border-border;
    border-radius: var(--radius-lg);
    padding: var(--space-4);
    box-shadow: var(--shadow-xl);
    z-index: var(--z-modal);
    max-width: 500px;
    width: 90%;
    animation: scale-in var(--duration-normal) var(--ease-in-out);
  }

  @keyframes scale-in {
    from {
      transform: scale(0.95);
      opacity: 0;
    }
    to {
      transform: scale(1);
      opacity: 1;
    }
  }

  .dialog-title {
    @apply font-semibold text-foreground;
    font-size: var(--text-xl);
    margin-bottom: var(--space-3);
  }

  .dialog-description {
    @apply text-muted-foreground;
    font-size: var(--text-base);
    margin-bottom: var(--space-4);
  }

  .dialog-actions {
    display: flex;
    gap: var(--space-2);
    justify-content: flex-end;
  }

  /* --- Keyboard shortcut --- */
  .kbd {
    @apply inline-flex items-center justify-center border border-border bg-muted;
    border-radius: 6px;
    padding: 2px 6px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: var(--text-xs);
    font-weight: 600;
    box-shadow: var(--shadow-xs);
  }

  /* --- Progress bar --- */
  .progress {
    width: 100%;
    height: 8px;
    background: var(--muted);
    border-radius: 999px;
    overflow: hidden;
  }

  .progress-bar {
    height: 100%;
    background: var(--primary);
    transition: width var(--duration-slow) var(--ease-in-out);
  }

  /* --- Command palette --- */
  .command-palette {
    position: fixed;
    top: 20%;
    left: 50%;
    transform: translateX(-50%);
    z-index: var(--z-modal);
    @apply bg-card border border-border;
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-xl);
    width: 90%;
    max-width: 600px;
    max-height: 70vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .command-palette-input {
    @apply w-full bg-transparent border-b border-border;
    padding: var(--space-3);
    font-size: var(--text-md);
  }

  .command-palette-list {
    overflow-y: auto;
    padding: var(--space-2);
  }

  .command-palette-item {
    @apply flex items-center gap-3 hover:bg-accent-surface;
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: background-color var(--duration-fast) var(--ease-in-out);
  }

  /* --- Dashboard card --- */
  .dashboard-card {
    @apply bg-card border border-border;
    border-radius: var(--radius-lg);
    padding: var(--space-4);
  }

  .dashboard-card-title {
    @apply font-semibold text-foreground;
    font-size: var(--text-lg);
    margin-bottom: var(--space-2);
  }

  .dashboard-card-value {
    @apply font-bold text-foreground;
    font-size: var(--text-3xl);
    font-variant-numeric: tabular-nums;
  }

  .dashboard-card-description {
    @apply text-muted-foreground;
    font-size: var(--text-sm);
    margin-top: var(--space-1);
  }

  /* --- Trace link --- */
  .trace-link {
    @apply inline-flex items-center gap-1.5 text-info hover:underline;
    font-size: var(--text-sm);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  /* --- Split view --- */
  .split-view {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--space-3);
  }

  @media (max-width: 768px) {
    .split-view {
      grid-template-columns: 1fr;
    }
  }
}

/* ============================================================
   5. СКРОЛЛБАР
   ============================================================ */
* {
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 999px;
  border: 2px solid transparent;
  background-clip: content-box;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--color-accent);
  background-clip: content-box;
}

::-webkit-scrollbar-track {
  background: transparent;
}

/* ============================================================
   6. УВАЖЕНИЕ К ДВИЖЕНИЮ
   ============================================================ */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
  }
}

/* ============================================================
   7. PRINT STYLES
   ============================================================ */
@media print {
  body {
    background: white;
    color: black;
  }

  .no-print,
  .composer,
  .toast-container,
  .command-palette,
  .dialog-backdrop {
    display: none !important;
  }

  .card,
  .dashboard-card,
  .hitl,
  .tool-card {
    break-inside: avoid;
    box-shadow: none;
    border: 1px solid #ccc;
  }

  .diff-add {
    background: #d4edda;
    color: #155724;
  }

  .diff-del {
    background: #f8d7da;
    color: #721c24;
  }

  a {
    text-decoration: underline;
  }

  a[href]::after {
    content: " (" attr(href) ")";
    font-size: 0.8em;
    color: #666;
  }
}

/* ============================================================
   8. FORCED COLORS (Windows High Contrast)
   ============================================================ */
@media (forced-colors: active) {
  .badge::before,
  .step .dot {
    forced-color-adjust: none;
  }

  .btn,
  .badge,
  .banner,
  .card,
  .hitl,
  .dialog {
    border: 1px solid ButtonText;
  }

  :focus-visible {
    outline: 2px solid Highlight;
  }
}
```

---