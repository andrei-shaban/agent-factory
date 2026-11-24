# ИНТЕРФЕЙСЫ
## Операционная панель для управления агентами · Blueprint v0.1

---

### 1. Назначение

Единое рабочее место оператора фабрики агентов: запуск сценариев, наблюдение за живыми ранами, человеческие подтверждения, просмотр результатов и история. Панель предметно-независима: подходит для документов, согласований, отчётности и любых сценариев фабрики.

### 2. Роль: рабочее место оператора

Оператор не «пользователь чата», а диспетчер производства:

- выбирает и запускает сценарий из каталога;
- наблюдает стрим выполнения: текст, размышления, инструменты, шаги;
- принимает решения в точках HITL: сравнение, расхождения, утверждение/отклонение;
- видит последствия: что исполнено, какой эффект, кто и когда утвердил;
- управляет долгими ранами: паузы, реконнект, возврат к ожиданиям;
- работает с историей и настройками.

### 3. Принципы

1. **Состояния важнее декора.** Каждый блок всегда показывает, что с ним происходит.
2. **Живой процесс, а не страница.** Стрим, шаги, реконнект, паузы — первоклассные объекты.
3. **Доказательность решений.** В HITL видны источник, извлечённое, расхождение и последствия.
4. **Один эффект на действие.** Повторное утверждение визуально и функционально блокируется.
5. **Совместимость со стеком.** Стили читаются shadcn и ассистент-компонентами без ручного «прикручивания».
6. **Операторская плотность.** Клавиатура, компактный режим, табличные числа, никакого лишнего движения.

### 4. Состав панели

| Зона | Назначение |
|---|---|
| Вход и доступ | Загрузка, 401-редирект, «нет доступа», пустой каталог |
| Каталог сценариев | Карточки/строки, статусы, фильтры, запуск, история |
| Ран / чат | Лента, стрим, размышления, инструменты, шаги, композер |
| HITL-формы | Diff-сравнение, гейт, утверждение/отклонение, аудит |
| История ранов | Список, статусы, фильтры, возврат к паузам |
| Боковая панель | Текущий ран, шаги, источники, аудит, действия |
| Настройки | Тема, плотность, размышления, автопрокрутка, формат |
| Служебные состояния | Скелетоны, ошибки, пусто, реконнект, офлайн-но-жив |

### 5. Словарь состояний

- **Ран:** `queued`, `running`, `waiting-hitl`, `paused`, `done`, `failed`, `cancelled`, `offline-alive`
- **Стрим:** `streaming`, `thinking`, `tool-running`, `tool-done`, `tool-error`
- **HITL:** `pending`, `locked`, `approved`, `rejected`, `danger`, `audit`
- **Служебные:** `loading`, `empty`, `error`, `reconnecting`, `no-access`, `deprecated`

### 6. Стилевой слой: роль `globals.css`

`globals.css` — не набор кнопок, а стилевое приложение панели:

1. **Бренд-токены** — шоколад и золото из исходной темы.
2. **Роли** — `background / foreground / card / primary / muted / border / ring` и семантика `success / warning / danger / info`; читаются shadcn.
3. **Компоненты** — кнопки, карточки, бейджи, таблицы, формы, композер, лента.
4. **Состояния** — всё из §5 через `data-state`/`data-tone`.
5. **Режимы** — светлая/тёмная тема, компактная плотность, `reduced-motion`, клавиатурный фокус.

Ограничения: без внешних шрифтов и CDN (Air-Gap), пол браузеров — актуальные Edge/Chrome/Firefox, числа — `tabular-nums`, фокус видим всегда.

### 7. Границы

Панель не управляет доступами (только AD/Keycloak), не обучает модели, не является админкой инфраструктуры.

---

## Приложение A. `globals.css`

```css
/* ============================================================
   globals.css — «Операционная панель для управления агентами»
   Производная исходной темы (шоколад/золото). Оптимизирована:
   - роли, совместимые с shadcn;
   - состояния ранов, стрима, HITL и служебные (по §5);
   - светлая/тёмная тема, компактная плотность;
   - клавиатура, reduced-motion, Air-Gap (без внешних ресурсов).
   ============================================================ */

@import "tailwindcss";

/* Тёмная тема — по классу .dark (переключатель в настройках) */
@custom-variant dark (&:where(.dark, .dark *));

/* ---------- 1. БРЕНД-ТОКЕНЫ (база из исходной темы) ---------- */
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
  --color-accent: #7a6748;        /* золото: 5+:1 на светлом фоне */
  --color-accent-hover: #6b5a40;
  --color-gold-soft: #c9a674;

  /* Семантика */
  --color-success: #4a6b4a;
  --color-warning: #96551d;       /* янтарь: отделён от золота */
  --color-danger: #8a3a3a;
  --color-info: #35688a;

  /* Геометрия */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
}

/* ---------- 2. РОЛИ (совместимость с shadcn) ---------- */
:root {
  color-scheme: light;

  /* Плотность: комфорт (переопределяется в compact) */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;

  --background: var(--color-bg-primary);
  --foreground: var(--color-text-primary);
  --card: var(--color-bg-secondary);
  --card-foreground: var(--color-text-primary);
  --muted: var(--color-bg-tertiary);
  --muted-foreground: var(--color-text-secondary);
  --border: var(--color-border);
  --input: var(--color-border);

  --primary: var(--color-accent);
  --primary-foreground: #ffffff;
  --secondary: var(--color-bg-tertiary);
  --secondary-foreground: var(--color-text-primary);

  /* Hover-поверхность: НЕ бренд-золото, а тёплый тинт */
  --accent-surface: color-mix(in srgb, var(--color-accent) 12%, var(--color-bg-secondary));
  --accent-surface-foreground: var(--color-text-primary);

  --destructive: var(--color-danger);
  --destructive-foreground: #ffffff;
  --success: var(--color-success);
  --warning: var(--color-warning);
  --info: var(--color-info);
  --ring: var(--color-accent);
}

.dark {
  color-scheme: dark;

  /* Шоколадная сторона бренд-токенов */
  --color-bg-primary: #0f0e0d;
  --color-bg-secondary: #1a1816;
  --color-bg-tertiary: #252220;
  --color-text-primary: #f0ece6;
  --color-text-secondary: #a09688;
  --color-border: #3d352d;
  --color-accent: #b08d57;
  --color-accent-hover: #c9a674;
  --color-gold-soft: #8a6d3d;

  /* Семантика в тёмной теме — читаемые статусы */
  --color-success: #86c07a;
  --color-warning: #e0a33e;
  --color-danger: #e07a6a;
  --color-info: #7cc0e8;

  /* Роли, зависящие от стороны темы */
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

/* Привязка ролей к утилитам: bg-background, text-primary и т.д. */
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

/* ---------- 3. БАЗА ---------- */
@layer base {
  body {
    @apply bg-background text-foreground antialiased;
    font-family: ui-sans-serif, system-ui, sans-serif;
    line-height: 1.5;
  }

  /* Клавиатурный фокус видим всегда */
  :focus-visible {
    outline: 2px solid var(--ring);
    outline-offset: 2px;
    border-radius: 6px;
  }

  ::selection {
    background: color-mix(in srgb, var(--color-accent) 30%, transparent);
  }

  /* Денежные и табличные числа */
  .num {
    font-variant-numeric: tabular-nums;
    text-align: right;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .92em;
  }
}

/* ---------- 4. КОМПОНЕНТЫ ---------- */
@layer components {

  /* --- Кнопки --- */
  .btn {
    @apply inline-flex items-center justify-center gap-2 font-semibold
           border border-transparent select-none transition-colors duration-150;
    min-height: 38px;
    padding: 9px calc(var(--space-4));
    border-radius: var(--radius-md);
    font-size: 14px;
  }
  .btn-sm {
    min-height: 32px;
    padding: 6px 10px;
    font-size: 12.5px;
    border-radius: var(--radius-sm);
  }
  .btn:disabled,
  .btn.is-locked {
    opacity: .6;
    pointer-events: none;
  }
  .btn[data-busy="true"] {
    pointer-events: none;
    opacity: .85;
  }

  .btn-primary {
    background: linear-gradient(180deg,
      color-mix(in srgb, var(--primary) 88%, white 12%), var(--primary));
    color: var(--primary-foreground);
    border-color: color-mix(in srgb, var(--primary) 70%, black 30%);
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .18),
                0 6px 14px color-mix(in srgb, var(--primary) 22%, transparent);
  }
  .btn-secondary { @apply bg-secondary text-secondary-foreground border-border; }
  .btn-outline   { @apply bg-transparent text-foreground border-border hover:bg-accent-surface; }
  .btn-ghost     { @apply bg-transparent text-muted-foreground hover:bg-accent-surface hover:text-foreground; }
  .btn-danger {
    color: var(--destructive);
    background: color-mix(in srgb, var(--destructive) 12%, transparent);
    border-color: color-mix(in srgb, var(--destructive) 35%, transparent);
  }

  /* --- Карточки --- */
  .card {
    @apply bg-card text-card-foreground border border-border shadow-sm;
    border-radius: var(--radius-lg);
    padding: var(--space-4);
  }
  .card-header {
    @apply flex items-start justify-between gap-3;
    margin-bottom: var(--space-3);
  }

  /* --- Бейджи состояний (§5) --- */
  .badge {
    @apply inline-flex items-center gap-1.5 rounded-full border font-bold;
    padding: 3px 10px;
    font-size: 11px;
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
  .badge[data-state="paused"] {
    color: var(--warning);
    background: color-mix(in srgb, var(--warning) 11%, transparent);
    border-color: color-mix(in srgb, var(--warning) 36%, transparent);
  }
  .badge[data-state="done"],
  .badge[data-state="success"] {
    color: var(--success);
    background: color-mix(in srgb, var(--success) 11%, transparent);
    border-color: color-mix(in srgb, var(--success) 34%, transparent);
  }
  .badge[data-state="failed"],
  .badge[data-state="error"] {
    color: var(--destructive);
    background: color-mix(in srgb, var(--destructive) 11%, transparent);
    border-color: color-mix(in srgb, var(--destructive) 36%, transparent);
  }
  .badge[data-state="muted"] {
    color: var(--muted-foreground);
    background: var(--muted);
    border-color: var(--border);
  }
  .badge[data-state="gold"] {
    color: var(--color-accent);
    background: color-mix(in srgb, var(--color-accent) 13%, transparent);
    border-color: color-mix(in srgb, var(--color-accent) 35%, transparent);
  }

  /* --- Баннеры --- */
  .banner {
    @apply flex items-center gap-2 border;
    border-radius: var(--radius-md);
    padding: 10px var(--space-3);
    font-size: 13px;
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

  /* --- Поля ввода --- */
  .input,
  .select,
  textarea.input {
    @apply w-full bg-card text-foreground border border-input;
    border-radius: var(--radius-md);
    padding: 9px var(--space-3);
    min-height: 38px;
  }
  .input::placeholder { color: color-mix(in srgb, var(--muted-foreground) 75%, transparent); }
  .input:focus-visible,
  .select:focus-visible,
  textarea.input:focus-visible {
    outline: none;
    border-color: var(--ring);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ring) 22%, transparent);
  }

  /* --- Переключатель --- */
  .switch { position: relative; display: inline-block; width: 42px; height: 24px; flex: none; }
  .switch input { position: absolute; inset: 0; opacity: 0; margin: 0; cursor: pointer; }
  .switch .track {
    position: absolute; inset: 0; border-radius: 999px;
    background: var(--border);
    transition: background .18s ease;
    pointer-events: none;
  }
  .switch .thumb {
    position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
    border-radius: 50%; background: var(--card);
    box-shadow: 0 2px 8px rgb(0 0 0 / .22);
    transition: transform .18s ease;
    pointer-events: none;
  }
  .switch input:checked ~ .track { background: var(--primary); }
  .switch input:checked ~ .track .thumb { transform: translateX(18px); }
  .switch input:focus-visible ~ .track {
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ring) 25%, transparent);
  }

  /* --- Таблицы --- */
  .table-wrap {
    @apply border border-border bg-card overflow-x-auto;
    border-radius: var(--radius-lg);
  }
  .table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  .table th {
    @apply text-left border-b border-border bg-muted text-muted-foreground;
    padding: 10px var(--space-3);
    font-size: 11.5px;
    text-transform: uppercase;
    letter-spacing: .05em;
    white-space: nowrap;
  }
  .table td {
    padding: 10px var(--space-3);
    border-bottom: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
  }
  .table tbody tr:hover { background: var(--accent-surface); }
  .table tbody tr:last-child td { border-bottom: 0; }

  /* --- Шаги рана --- */
  .steps { display: grid; gap: 10px; }
  .step {
    display: grid;
    grid-template-columns: 14px 1fr auto;
    gap: 10px;
    align-items: center;
    font-size: 13px;
  }
  .step .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--border); }
  .step[data-state="done"] .dot { background: var(--success); }
  .step[data-state="active"] .dot {
    background: var(--info);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--info) 18%, transparent);
  }
  .step[data-state="waiting"] .dot {
    background: var(--warning);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--warning) 18%, transparent);
  }
  .step[data-state="future"] { color: var(--muted-foreground); }

  /* --- Лента: сообщения --- */
  .msg { display: grid; gap: 6px; max-width: min(92%, 820px); }
  .msg.user { align-self: flex-end; margin-left: auto; }
  .msg.agent { align-self: flex-start; }
  .msg-bubble {
    @apply bg-card border border-border;
    border-radius: var(--radius-lg);
    padding: var(--space-3);
  }
  .msg.user .msg-bubble {
    background: color-mix(in srgb, var(--primary) 12%, var(--card));
  }
  .msg-meta {
    display: flex; gap: 8px; flex-wrap: wrap;
    font-size: 11px; color: var(--muted-foreground);
  }

  /* --- Размышление агента --- */
  .thought {
    @apply text-muted-foreground border border-dashed border-border bg-muted;
    border-radius: var(--radius-md);
    padding: 10px var(--space-3);
    font-size: 13px;
  }

  /* --- Карточка инструмента --- */
  .tool-card { @apply border border-border bg-card overflow-hidden; border-radius: var(--radius-md); }
  .tool-head {
    @apply flex items-center justify-between gap-2 bg-muted border-b border-border;
    padding: 9px var(--space-3);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
  }
  .tool-body {
    padding: 10px var(--space-3);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
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
  .diff-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .diff-table th,
  .diff-table td {
    border: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
    padding: 8px 10px;
  }
  .diff-table th {
    @apply bg-muted text-muted-foreground text-left;
    font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em;
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
    font-size: 13.5px;
    cursor: pointer;
    user-select: none;
  }
  .gate input { margin-top: 2px; accent-color: var(--primary); }
  .gate.error { border-color: var(--destructive); animation: shake .35s ease; }
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
    font-size: 11px;
  }

  /* --- Стрим и загрузка --- */
  .stream-caret {
    display: inline-block;
    width: 8px; height: 1em;
    margin-left: 3px; vertical-align: -2px;
    background: var(--color-accent);
    animation: blink 1s steps(2) infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }

  .skeleton {
    height: 12px;
    border-radius: 8px;
    background: linear-gradient(90deg,
      var(--muted),
      color-mix(in srgb, var(--border) 55%, var(--muted)),
      var(--muted));
    background-size: 200% 100%;
    animation: shimmer 1.3s linear infinite;
  }
  @keyframes shimmer { to { background-position: -200% 0; } }

  .spinner {
    width: 14px; height: 14px;
    border-radius: 50%;
    border: 2px solid currentColor;
    border-top-color: transparent;
    animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* --- Каталог: карточка сценария --- */
  .scenario-card { @apply bg-card border border-border; border-radius: var(--radius-lg); }
  .scenario-button {
    @apply w-full flex items-center gap-3 text-left hover:bg-accent-surface;
    padding: var(--space-3);
    border-radius: var(--radius-md);
    transition: background-color .15s ease;
  }
  .scenario-button[aria-current="true"] {
    background: color-mix(in srgb, var(--color-accent) 12%, var(--card));
    box-shadow: inset 2px 0 0 var(--color-accent);
  }
  .scenario-button.is-disabled { opacity: .6; pointer-events: none; }

  /* --- Реестр: строка рана --- */
  .task-item {
    @apply w-full flex items-center gap-3 border-l-2 border-transparent bg-card hover:bg-accent-surface;
    border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3);
    margin-bottom: var(--space-1);
    font-size: 13px;
    cursor: pointer;
    transition: background-color .15s ease, border-color .15s ease;
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
    font-size: 14px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }
  .markdown-body-clean :where(h1, h2, h3, h4, h5, h6) {
    @apply font-bold text-foreground;
    margin: var(--space-4) 0 var(--space-2);
  }
  .markdown-body-clean h2 { font-size: 18px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
  .markdown-body-clean h3 { font-size: 16px; color: var(--color-accent); }
  .markdown-body-clean p { margin-bottom: var(--space-3); }
  .markdown-body-clean ul { list-style: disc; padding-left: 22px; margin-bottom: var(--space-3); }
  .markdown-body-clean ol { list-style: decimal; padding-left: 22px; margin-bottom: var(--space-3); }
  .markdown-body-clean li { margin-top: 4px; }
  .markdown-body-clean a { color: var(--color-accent); text-decoration: underline; text-underline-offset: 3px; }
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
    font-size: 12.5px;
  }
  .markdown-body-clean pre code { padding: 0; background: transparent; }
  .markdown-body-clean blockquote {
    @apply text-muted-foreground border-l-2 italic;
    border-left-color: var(--color-accent);
    padding-left: var(--space-3);
    margin: var(--space-3) 0;
  }
  /* Таблицы: скролл вместо обрезки */
  .markdown-body-clean table {
    display: block;
    width: max-content;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    margin: var(--space-2) 0 var(--space-3);
    font-size: 13px;
  }
  .markdown-body-clean th {
    @apply bg-muted font-bold text-left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  .markdown-body-clean td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
  .markdown-body-clean tr:last-child td { border-bottom: 0; }
  .markdown-body-clean li:has(> input[type="checkbox"]) { list-style: none; margin-left: -1.25rem; }
  .markdown-body-clean input[type="checkbox"] { accent-color: var(--primary); margin-right: 8px; }
}

/* ---------- 5. СКРОЛЛБАР ---------- */
* {
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 999px;
  border: 2px solid transparent;
  background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background: var(--color-accent); background-clip: content-box; }
::-webkit-scrollbar-track { background: transparent; }

/* ---------- 6. УВАЖЕНИЕ К ДВИЖЕНИЮ ---------- */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
  }
}
```