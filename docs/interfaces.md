# ИНТЕРФЕЙСЫ
## Операционная панель для управления агентами

**Документ интерфейсов v1.0** · производная от Production Blueprint v5.2.1 (§2 SPA, §3.5 AG-UI, §3.7 манифесты, §5.6 приёмка) · статус: к реализации

---

## 1. Назначение и роль

Интерфейс — рабочее место оператора фабрики агентов. Это не «чат с ботом», а операционная панель: оператор запускает сценарии, наблюдает живое выполнение, принимает решения в точках человеческого контроля и работает с историей.

Типовой путь оператора:

1. Заходит в платформу (единый вход через корпоративную аутентификацию).
2. Видит каталог сценариев, разрешённых его группе.
3. Запускает сценарий.
4. Общается с агентом в рабочей зоне.
5. Видит, как агент думает, вызывает инструменты, считает результаты.
6. Получает промежуточные результаты и доказательства.
7. Подтверждает или отклоняет действия в формах контроля.
8. Смотрит историю запусков.
9. Возвращается к паузам и ожидающим подтверждениям (паузы живут до 30 дней).
10. Настраивает интерфейс под себя.

Ограничения среды: полностью изолированный контур (без внешних шрифтов и CDN), браузеры оператора — актуальные Edge / Chrome / Firefox.

---

## 2. Принципы

1. **Состояния важнее декора.** Каждый блок всегда показывает, что с ним происходит.
2. **Живой процесс, а не страница.** Стрим, шаги, паузы, реконнект — первоклассные объекты.
3. **Доказательность решений.** В точках контроля видны источник, извлечённое, расхождение и последствия.
4. **Один эффект на действие.** Повторное утверждение блокируется визуально и функционально.
5. **Совместимость со стеком.** Токены читаются shadcn-компонентами и компонентами ленты без ручного «прикручивания».
6. **Операторская плотность.** Клавиатура, компактный режим, ровные цифры, минимум движения.

---

## 3. Экраны и блоки

### 3.1 Экран входа и доступа

Первое, что видит человек до готовности платформы.

| Элемент | Состояния |
|---|---|
| Загрузка | спиннер, скелетон, «Проверяем доступ» |
| Ошибка авторизации | красный баннер, кнопка «Повторить вход» |
| Редирект на единый вход | информационный баннер |
| Нет доступа к сценарию | приглушённая карточка + причина |
| Пусто | «Вам пока не разрешён ни один сценарий» |

### 3.2 Каталог сценариев

Список разрешённых сценариев (источник — генерируемый индекс, фильтрация по группам — только UX; принуждение — на бэкенде).

**Элементы карточки:** название, описание, версия, статус, кнопки «Запустить» / «История» / «Подробнее», бейджи: «Новая версия», «Нет доступа», «Устарел», «Активный ран», «Ждёт подтверждения».

**Состояния карточки:** обычная, наведение, фокус, выбрана, недоступна, устарела, загрузка, ошибка, пустой список.

**Стили покрывают:** сетку карточек и строчный вид, фильтры, поиск, пустое состояние, ошибку загрузки, адаптив.

### 3.3 Рабочая зона сценария (ран / чат)

Центральный экран. Показывает живой процесс и связан с событиями протокола напрямую:

| Событие протокола | Элемент интерфейса |
|---|---|
| `RUN_STARTED` / `RUN_FINISHED` / `RUN_ERROR` | статус рана, шаги, баннеры |
| `TEXT_MESSAGE_*` | сообщение агента, каретка стрима |
| `TOOL_CALL_START/ARGS/END/RESULT` | карточка инструмента |
| `STATE_SNAPSHOT` / `STATE_DELTA` | шаги сценария, боковая панель |
| `CUSTOM` (interrupt) | форма контроля, встроенная в ленту |
| реконнект (клиентская прослойка) | баннеры связи, скелетон снапшота |

**Блоки ленты:** сообщения (пользователь / агент / система / предупреждение / ошибка / результат / служебное / черновик / отменённое), время, роль, действия (копировать, повторить, остановить), прокрутка вниз.

**Стриминг:** каретка, частичный текст и незавершённый markdown, таблицы по частям, перенос длинных строк, код, цитаты.

**Размышление агента:** тихий приглушённый блок с индикатором активности и сворачиванием; визуально отделён от финального ответа.

**Карточки инструментов:** имя, статус, аргументы, прогресс, результат, ошибка, время, повтор, детали. Состояния: вызывается → аргументы → исполняется → результат / ошибка / таймаут / отмена. Поддержка: спиннер, скелетон аргументов, моноширинный текст, перенос длинных строк, сворачивание.

**Пошаговый прогресс:** этапы сценария (например: распознавание → извлечение → итоги → подтверждение → проведение → завершение). Состояния шага: будущий, текущий, пройденный, ошибка, ждёт человека. Горизонтальный и вертикальный вид, компактный режим.

**Формы контроля (самый важный блок):**

- заголовок и описание формы;
- сравнение «было / стало», таблица расхождений, подсветка изменений и ошибок;
- обязательный чекбокс-гейт, комментарий;
- кнопки «Утвердить» / «Отклонить» / «На доработку»;
- состояния: выполняется → утверждено / отклонено / уже выполнено другим пользователем / повтор заблокирован;
- аудит: кто, когда, какой эффект;
- маркеры опасного и необратимого действия, низкой уверенности;
- источник данных: файл, страница, фрагмент, предпросмотр результата.

**Вложения:** иконка, имя, размер, статус и прогресс загрузки, ошибка, предпросмотр, страница документа, удаление, скачивание, запрет по типу/размеру.

**Источники и доказательства:** чип источника (файл, страница, фрагмент), уверенность, ссылка на исходник и на трейс; компактный и полный вид.

**Композер:** текстовое поле с авто-ростом, отправка, остановка, вложение, подсказки, черновик, счётчик символов. Состояния: обычный, фокус, ошибка, отправка, остановка, заблокирован, пустой, с вложением, с черновиком, «ран ждёт подтверждения», режим выбранного сценария.

**Реконнект и связь:** баннеры «Нет связи, но ран продолжается», «Переподключаемся», «Восстанавливаем историю», «События догружаются», «Связь восстановлена», «Ошибка подписки». Тона: информационный, предупреждающий, опасный; с прогрессом, кнопкой повтора, автоскрытием.

### 3.4 История ранов

**Элементы строки:** сценарий, дата/время, пользователь, статус, текущий шаг, длительность, «требует действия», ошибка, результат, действия (открыть, продолжить, остановить, архивировать).

**Состояния строки:** обычный, активный, ждёт человека, выполняется, ошибка, завершён, недоступен, выбран, наведение, фокус.

**Стили:** список и табличный вид, фильтры, поиск, сортировка, пагинация или бесконечная прокрутка, группировка по датам, пустое состояние, загрузка, ошибка.

### 3.5 Боковая панель

Детали текущего рана: сценарий и версия, статус, этапы, время старта и последнего события, сколько ждёт, пользователь, действия, источники, вложения, ошибки, аудит, комментарии, история шагов, сворачивание.

**Состояния:** активный ран, пауза, ожидание человека, завершено, ошибка, недоступно, загрузка, пусто, свёрнута, широкая/узкая.

**Стили:** панели, аккордеоны, списки, бейджи, кнопки, липкое позиционирование, адаптив, сворачивание.

### 3.6 Настройки пользователя

Тема, плотность, показ размышлений, автопрокрутка, формат времени, часовой пояс, язык, уведомления, поведение при реконнекте, вид форм подтверждения. Управление доступами в настройки не входит — только корпоративные системы.

**Стили:** формы, группы и секции, переключатели, селекты, чекбоксы, подсказки, ошибки валидации, сохранение, «есть несохранённые изменения».

### 3.7 Служебные компоненты

Кнопки, иконки, бейджи, чипы, алерты, тосты, модальные окна, поповеры, тултипы, спиннеры, скелетоны, прогресс-бары, пустые состояния, состояния ошибки, диалоги подтверждения, списки, таблицы, пагинация, поиск, фильтры, вкладки, аккордеоны, меню.

---

## 4. Словарь состояний

| Состояние | Где встречается | Визуальный маркер |
|---|---|---|
| `queued` | история, каталог | серый бейдж |
| `running` | лента, шаги, история | инфо-бейдж, пульс |
| `waiting-hitl` | шаги, история, форма | янтарный бейдж, рамка формы |
| `paused` | история, боковая | янтарный бейдж |
| `done` / `success` | везде | зелёный |
| `failed` / `error` | везде | красный + текст причины |
| `cancelled` | история | серый |
| `offline-alive` | баннер связи | красный баннер «ран жив» |
| `streaming` | лента | каретка |
| `thinking` | лента | тихий блок |
| `tool-*` | карточка инструмента | статус-чип |
| `loading` | списки, панели | скелетон |
| `empty` | списки | пунктирная заглушка |
| `no-access` | каталог, вход | приглушение + причина |
| `deprecated` | каталог | серый бейдж |
| `locked` | кнопки | приглушение, «эффект 1» |

Каждое состояние выражается не только цветом, но и текстом/иконкой (читаемость без цвета).

---

## 5. Требования к визуальному языку

| Слой | Что задаёт |
|---|---|
| Цветовые роли | фон приложения/панелей/карточек/всплывающих; текст основной/вторичный/приглушённый; границы; фокус; акцент; успех/ошибка/предупреждение/ожидание/информация/опасное действие/блокировка |
| Поверхности | страница, панель, карточка, всплывающий слой, модальное окно, тост, тултип, баннер, форма, таблица, код, цитата, вложение |
| Типографика | заголовки, текст, подписи, моноширинный; деньги и даты — ровные цифры (`tabular-nums`) |
| Отступы и сетки | базовый шаг, карточки, списки, чат, формы, таблицы, адаптивные колонки, липкие панели, области прокрутки |
| Формы | поля, селекты, чекбоксы, радиокнопки, переключатели, кнопки, группы, ошибки, подсказки, обязательные и заблокированные поля |
| Обратная связь | спиннер, скелетон, прогресс, тост, алерт, баннер, модальное подтверждение, сообщения об ошибке/успехе, пустое состояние |
| Анимации | появление тостов и сообщений, индикатор стрима, пульс ожидания, скелетон, сворачивание, переходы вкладок, открытие модальных окон, смена темы; обязательное уважение к уменьшенному движению |
| Доступность | видимый фокус, контраст, крупные зоны нажатия, клавиатура, статусы не только цветом, масштабирование текста |

---

## 6. Приоритизация

| Слой | Состав |
|---|---|
| **MVP (обязательно)** | Экран входа/доступа; каталог сценариев; лента со стримом; карточки инструментов; шаги; формы контроля с аудитом; история ранов; баннеры реконнекта; служебный набор (кнопки, бейджи, спиннер, скелетон, тост, пусто/ошибка); настройки темы; светлая/тёмная тема |
| **Слой 2** | Сворачивание размышлений; прогресс вложений; источники с уверенностью; компактная плотность; пагинация и группировка истории; тултипы и поповеры; боковая панель целиком |
| **Слой 3** | Предпросмотр результата действия; комментарии в боковой; горячие клавиши; экспорт истории; расширенные фильтры |

---

## 7. Роль `globals.css`

`globals.css` — не набор кнопок, а стилевое приложение панели:

1. **Бренд-токены** — шоколад и золото исходной темы.
2. **Роли** — `background / foreground / card / primary / muted / border / ring` и семантика `success / warning / danger / info`; читаются shadcn и компонентами ленты.
3. **Компоненты** — кнопки, карточки, бейджи, таблицы, формы, лента, композер, формы контроля.
4. **Состояния** — весь словарь из раздела 4 через `data-state` / `data-tone`.
5. **Режимы** — светлая/тёмная тема, компактная плотность, уменьшенное движение, видимый клавиатурный фокус.

---

## Приложение А. `globals.css`

```css
/* ============================================================
   globals.css — «Операционная панель для управления агентами»
   Документ интерфейсов, Приложение А.

   Производная исходной темы (шоколад/золото). Оптимизирована:
   - роли, совместимые с shadcn;
   - словарь состояний (бейджи, баннеры, шаги, формы контроля);
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
  --color-accent: #7a6748;        /* золото: контраст 5+:1 на светлом фоне */
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

  --shadow-sm: 0 6px 16px rgb(32 24 12 / .07);
  --shadow-md: 0 16px 40px rgb(32 24 12 / .14);

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

  /* Hover-поверхность: тёплый тинт, не бренд-золото */
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

  --shadow-sm: 0 8px 20px rgb(0 0 0 / .30);
  --shadow-md: 0 18px 44px rgb(0 0 0 / .42);

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

  input[type="checkbox"],
  input[type="radio"] {
    accent-color: var(--primary);
    width: 16px;
    height: 16px;
    flex: none;
  }

  /* Денежные и табличные числа */
  .num {
    font-variant-numeric: tabular-nums;
    text-align: right;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .92em;
  }

  .kbd {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    padding: 1px 6px;
    border: 1px solid var(--border);
    border-bottom-width: 2px;
    border-radius: 6px;
    background: var(--muted);
    color: var(--muted-foreground);
  }

  .divider {
    height: 1px;
    background: color-mix(in srgb, var(--border) 75%, transparent);
    margin: var(--space-4) 0;
  }
}

/* ---------- 4. КОМПОНЕНТЫ ---------- */
@layer components {

  /* --- Кнопки --- */
  .btn {
    @apply inline-flex items-center justify-center gap-2 font-semibold
           border border-transparent select-none transition-colors duration-150;
    min-height: 38px;
    padding: 9px var(--space-4);
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
    @apply bg-card text-card-foreground border border-border;
    border-radius: var(--radius-lg);
    padding: var(--space-4);
    box-shadow: var(--shadow-sm);
  }
  .card-header {
    @apply flex items-start justify-between gap-3;
    margin-bottom: var(--space-3);
  }

  /* --- Бейджи состояний (словарь, раздел 4) --- */
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
  .badge[data-state="muted"],
  .badge[data-state="queued"],
  .badge[data-state="cancelled"],
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
  /* Пульс для активных процессов */
  .badge[data-state="running"]::before,
  .badge[data-state="waiting"]::before {
    animation: pulse-dot 1.6s ease-in-out infinite;
  }
  @keyframes pulse-dot {
    0%, 100% { opacity: 1; }
    50% { opacity: .35; }
  }

  /* --- Баннеры (реконнект, доступ, служебные) --- */
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
  .input[aria-invalid="true"],
  textarea.input[aria-invalid="true"] {
    border-color: var(--destructive);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--destructive) 18%, transparent);
  }
  .field-hint { font-size: 12px; color: var(--muted-foreground); }
  .field-error { font-size: 12px; color: var(--destructive); }

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
  .step[data-state="error"] .dot {
    background: var(--destructive);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--destructive) 18%, transparent);
  }
  .step[data-state="future"] { color: var(--muted-foreground); }

  /* --- Лента: сообщения --- */
  .msg { display: grid; gap: 6px; max-width: min(92%, 820px); }
  .msg.user { align-self: flex-end; margin-left: auto; }
  .msg.agent,
  .msg.system { align-self: flex-start; }
  .msg-bubble {
    @apply bg-card border border-border;
    border-radius: var(--radius-lg);
    padding: var(--space-3);
  }
  .msg.user .msg-bubble { background: color-mix(in srgb, var(--primary) 12%, var(--card)); }
  .msg.system .msg-bubble {
    @apply bg-muted text-muted-foreground border-dashed;
    font-size: 12.5px;
  }
  .msg.error .msg-bubble {
    background: color-mix(in srgb, var(--destructive) 9%, var(--card));
    border-color: color-mix(in srgb, var(--destructive) 35%, transparent);
  }
  .msg.result .msg-bubble {
    background: color-mix(in srgb, var(--success) 9%, var(--card));
    border-color: color-mix(in srgb, var(--success) 32%, transparent);
  }
  .msg-meta {
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
    font-size: 11px; color: var(--muted-foreground);
  }

  /* Аватар / роль */
  .avatar {
    width: 30px; height: 30px;
    border-radius: 50%;
    display: grid; place-items: center;
    font-size: 12px; font-weight: 800;
    background: var(--muted);
    color: var(--muted-foreground);
    flex: none;
  }
  .avatar[data-role="agent"] {
    background: color-mix(in srgb, var(--color-accent) 18%, transparent);
    color: var(--color-accent);
  }
  .avatar[data-role="system"] {
    background: color-mix(in srgb, var(--info) 15%, transparent);
    color: var(--info);
  }

  /* --- Размышление агента --- */
  .thought {
    @apply text-muted-foreground border border-dashed border-border bg-muted;
    border-radius: var(--radius-md);
    padding: 10px var(--space-3);
    font-size: 13px;
  }
  .thought .head {
    @apply flex items-center gap-2 font-semibold;
    margin-bottom: 4px;
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

  /* --- Формы контроля (HITL) --- */
  .hitl {
    @apply bg-card border;
    display: grid;
    gap: var(--space-3);
    border-radius: var(--radius-lg);
    padding: var(--space-3);
    border-color: color-mix(in srgb, var(--warning) 42%, var(--border));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--warning) 9%, transparent);
  }
  .hitl[data-tone="danger"] {
    border-color: color-mix(in srgb, var(--destructive) 45%, var(--border));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--destructive) 9%, transparent);
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
    @apply flex items-start gap-2.5 border border-dashed border-border;
    border-radius: var(--radius-md);
    padding: 10px var(--space-3);
    font-size: 13.5px;
    cursor: pointer;
    user-select: none;
  }
  .gate input { margin-top: 2px; }
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
    flex: none;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .progress {
    height: 6px;
    border-radius: 999px;
    background: var(--muted);
    overflow: hidden;
  }
  .progress .bar {
    height: 100%;
    border-radius: 999px;
    background: var(--primary);
    transition: width .3s ease;
  }
  .progress[data-state="indeterminate"] .bar {
    width: 40%;
    animation: progress-slide 1.2s ease-in-out infinite;
  }
  @keyframes progress-slide {
    from { transform: translateX(-100%); }
    to { transform: translateX(260%); }
  }

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

  /* --- История: строка рана --- */
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
  .task-item[data-state="waiting"] { border-left-color: var(--warning); }
  .task-item[data-state="failed"] { border-left-color: var(--destructive); }

  /* --- Композер --- */
  .composer {
    @apply bg-card border-t border-border;
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: var(--space-2);
    align-items: center;
    padding: var(--space-3);
  }
  .composer[data-state="disabled"] { opacity: .6; pointer-events: none; }

  /* --- Вложения и источники --- */
  .attachment {
    @apply inline-flex items-center gap-2 border border-border bg-card;
    border-radius: var(--radius-md);
    padding: 6px 10px;
    font-size: 12.5px;
  }
  .attachment .meta { color: var(--muted-foreground); font-size: 11px; }

  .source-chip {
    @apply inline-flex items-center gap-1.5 border border-border bg-muted text-muted-foreground;
    border-radius: 999px;
    padding: 4px 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
  }
  .source-chip[data-confidence="low"] { color: var(--warning); border-color: color-mix(in srgb, var(--warning) 40%, transparent); }
  .source-chip[data-confidence="high"] { color: var(--success); border-color: color-mix(in srgb, var(--success) 40%, transparent); }

  /* --- Вкладки, аккордеоны, пагинация --- */
  .tabs { @apply flex gap-0.5 border-b border-border; }
  .tab {
    @apply text-muted-foreground bg-transparent;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 700;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
  }
  .tab:hover { color: var(--foreground); }
  .tab[data-state="active"] {
    color: var(--foreground);
    border-bottom-color: var(--color-accent);
  }

  .accordion-trigger {
    @apply flex w-full items-center justify-between gap-2 text-left;
    padding: var(--space-2) 0;
    font-weight: 700;
    font-size: 13.5px;
  }
  .accordion-trigger .chev { transition: transform .18s ease; flex: none; }
  .accordion-item[data-state="open"] .accordion-trigger .chev { transform: rotate(90deg); }

  .pagination { display: flex; gap: 4px; align-items: center; }
  .page-btn {
    @apply bg-transparent border border-border text-foreground hover:bg-accent-surface;
    min-width: 32px; height: 32px;
    display: grid; place-items: center;
    border-radius: var(--radius-sm);
    font-size: 13px;
  }
  .page-btn[data-state="active"] {
    background: var(--primary);
    color: var(--primary-foreground);
    border-color: var(--primary);
  }

  /* --- Тосты --- */
  .toast-viewport {
    position: fixed;
    bottom: var(--space-4);
    left: 50%;
    transform: translateX(-50%);
    display: grid;
    gap: var(--space-2);
    z-index: 70;
    width: max-content;
    max-width: 92vw;
  }
  .toast {
    @apply bg-card text-card-foreground border border-border;
    border-left: 4px solid var(--info);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-md);
    padding: 10px 14px;
    font-size: 13px;
  }
  .toast[data-tone="success"] { border-left-color: var(--success); }
  .toast[data-tone="warning"] { border-left-color: var(--warning); }
  .toast[data-tone="danger"] { border-left-color: var(--destructive); }

  /* --- Модальные окна --- */
  .modal-overlay {
    position: fixed; inset: 0;
    display: grid; place-items: center;
    background: rgb(0 0 0 / .5);
    z-index: 60;
  }
  .modal {
    @apply bg-card text-card-foreground border border-border;
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-md);
    width: min(92vw, 480px);
    padding: var(--space-4);
  }

  /* --- Пустые состояния и ошибки --- */
  .empty {
    @apply text-muted-foreground border border-dashed border-border;
    display: grid;
    place-items: center;
    gap: var(--space-2);
    padding: var(--space-5);
    text-align: center;
    border-radius: var(--radius-lg);
    font-size: 13.5px;
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
  .markdown-body-clean input[type="checkbox"] { margin-right: 8px; }
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