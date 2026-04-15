# Gastos GG — System Architecture & Reference

> **Living document.** Updated whenever we change semantics, schema, API,
> or add features. This is the single source of truth for "how the system
> works today". Every section says what state things are in as of the last
> update timestamp at the bottom.

## Contents

1. [Overview](#overview)
2. [Stack & deployment](#stack--deployment)
3. [Payment methods](#payment-methods)
4. [Income strategy — plan vs actual](#income-strategy)
5. [Budget strategy — baseline, annual, per-month](#budget-strategy)
6. [Parent / sub categories](#categories)
7. [Multi-currency](#multi-currency)
8. [Database schema](#database-schema)
9. [Migrations applied](#migrations-applied)
10. [REST API endpoints](#rest-api-endpoints)
11. [Telegram bot — commands and aliases](#telegram-bot)
12. [Dashboard UI — zones and references](#dashboard-ui)
13. [Open issues & future work](#open-issues)

---

## Overview

**Gastos GG** is the González-Guevara family's personal P&L tracker. Two
surfaces that share one SQLite volume on Railway:

- **Telegram bot** (`@gastos_gg_bot`) — Daniel and Madeline register expenses
  in seconds from their phones. Multi-currency, alias vocabulary, inline
  payment method selector.
- **Dashboard web** (`https://expense-bot-production-04c4.up.railway.app`) —
  monthly P&L view, budget tracking against three references (Ideal, Plan
  Ingresos, Configured), category rollup, payment method aggregation, income
  source editor, category/budget editor, per-month budget overrides, user
  guide.

Bitácora mental: **"budget familiar mensual"**. Target lifestyle cap $5,000
USD/mo. Multi-currency supported for the cross-border reality (Bogotá + UAE).

---

## Stack & deployment

| Layer | Tech |
|---|---|
| Bot | Python 3.11 + python-telegram-bot 21.7 (polling) |
| API | FastAPI 0.115 + uvicorn (same process as bot) |
| Frontend | Single `static/index.html` with Alpine.js 3.14 (CDN) + Tailwind (CDN) |
| Database | SQLite at `/data/expenses.db` (Railway persistent volume) |
| Hosting | Railway, project `angelic-magic`, service `expense-bot` |
| Repo | `github.com/danielgonzalez1237/expense-bot`, auto-deploy on push to `main` |
| Local clone | `~/dev/expense-bot` |

Key files:

```
bot.py            # Telegram bot + data layer + DB init/migrations
api.py            # FastAPI REST endpoints (mounted inside bot's event loop)
static/index.html # Single-page dashboard (Alpine + Tailwind)
dev_server.py     # Local-only FastAPI runner without Telegram polling
Dockerfile        # Railway build (python:3.11-slim + EXPOSE 8080)
docs/             # This file + reconciliation format
scripts/          # One-off maintenance scripts (recovery, etc.)
```

**Deploy flow:** push to `main` → Railway autodeploys (build ~20s + startup ~5s)
→ init_db() runs all pending migrations → API and bot come up.

---

## Payment methods

The authoritative list lives in `config.payment_methods` (SQLite JSON). The
defaults below are seeded on first run via `_DEFAULT_PAYMENT_METHODS` in
`bot.py`; subsequent edits happen via migration or the future config editor.

### Current inventory (post-migration 007)

| Bank key | Country | Currency | Flag | Cards |
|---|---|---|---|---|
| `BDB` | 🇨🇴 Colombia | COP | 🇨🇴 | Visa Latam Dani · Visa Latam Mado · MC Dani |
| `BBVA` | 🇨🇴 Colombia | COP | 🇨🇴 | MC Dani · MC Mado |
| `Bancolombia` | 🇨🇴 Colombia | COP | 🇨🇴 | MC Dani |
| `Falabella` | 🇨🇴 Colombia | COP | 🇨🇴 | Mastercard Dani |
| `WIO` | 🇦🇪 UAE | AED | 🇦🇪 | MC Dani · MC Mado |
| `ENBD` | 🇦🇪 UAE | AED | 🇦🇪 | Visa AED · Visa USD |
| `Transferencia` | — | MIXED | 🔄 | BBVA · BDB · BANCOLOMBIA · WIO · ENBD · CHASE (destinations) |
| `Efectivo` | — | MIXED | 💵 | Efectivo |

The flag/currency mapping lives in `api.py::BANK_METADATA` and is used by
`/api/summary.by_payment_method` to enrich each row with country/currency/flag
for the dashboard display.

### How a gasto gets tagged with a method

1. User sends `50000 restaurante cena` to the bot
2. Bot registers the expense in SQLite (no method yet)
3. Bot replies with an inline keyboard of all payment methods
4. User taps one (e.g. "BDB Visa Latam Dani")
5. Callback handler updates `expenses.metodo_pago` to `"BDB Visa Latam Dani"`

If the user skips the method selector, `metodo_pago` stays as `"Sin especificar"`.
The dashboard's "Por método de pago" bucket shows those separately so Daniel
can reconcile them later.

### How to add a new payment method

Two paths:

1. **Quick (dashboard)** — not yet implemented. Planned: payment methods
   editor in the settings modal.
2. **Code + migration** — add to `_DEFAULT_PAYMENT_METHODS` in bot.py AND add
   a new migration that walks `config.payment_methods` and inserts the key if
   missing. See migration 007 as reference.

---

## Income strategy

Two independent concepts:

### 1. `income_sources.expected_usd` — THE PLAN

Per source, the amount Daniel EXPECTS to receive monthly. Sum of all active
sources = monthly income plan. This drives the **💰 Plan** gauge on the
dashboard.

Edit from: 💼 Editar ingresos → tab "Fuentes" → row 2 has the
`Esperado mensual $___` input.

Independent of what actually came in.

### 2. `income_entries` — THE ACTUAL

Each materialized income event: period, source, monto, currency, fecha,
USD-equivalent. Multi-currency supported; conversion uses the period's
rate from `exchange_rates_history` (or current globals as fallback).

Edit from: 💼 Editar ingresos → tab "Entradas del mes" → add/delete entries.

**Critical rule:** `period` is DERIVED from `fecha`, not the modal's active
month. If you pick fecha = March 7 while the modal is on April, the entry
is filed under March. This was migration 003's fix.

### Per-month exchange rates

`exchange_rates_history` table holds per-period TRM / BOB / AED. Editing a
period's rates recomputes `monto_usd` for all income_entries in that period
automatically (PUT `/api/rates/history/{period}`).

### Current default sources seeded by migration 002

- `alquiler_quito` (🏠, USD)
- `alquiler_dubai` (🏖️, AED)
- `clp` (🌊, USD)
- `ib_dividends` (📈, USD)

Plus any custom sources Daniel has created (fuentenueva, nuevafuente, fuente,
intereses, nexo, deltayield, etc. per recent edits).

---

## Budget strategy

Three reference amounts visible on the dashboard gauges:

### 🎯 Ideal — $5,000 USD/month hardcoded

The **lifestyle cap**. Aspirational target. Fixed in `bot.py` as
`BUDGET_LIMIT_USD = 5000`. Multiplied by `n_months` for multi-month windows.
**Motivational** — when spending is under this, the dashboard shows a
prominent "Sobrante vs ideal +$X" panel in emerald.

### 💰 Plan Ingresos — sum of `expected_usd`

The **income plan**. Sum of `expected_usd` across active `income_sources`,
prorated for the active window. Drives the middle gauge.

If the user hasn't set any `expected_usd` yet, this shows `—` / "sin plan"
and prompts the user to configure.

### ⚙️ Configurado — sum of per-category budgets

The **spending plan**. Sum of `usd` across all category entries in
`config.budget`, respecting:

- Per-month overrides from `budget_history` (sparse storage, PATCH semantics)
- **Parent-zeroed rule**: categories that have children (subs) contribute $0
  from their own row — their budget is entirely the sum of their children

### Per-month overrides (budget_history)

Daniel can set different budgets for different months:

- Open ⚙️ → period dropdown at top → pick a specific month
- Edit any sub row → save → only the edited categories get written to
  `budget_history` for that period
- Non-edited categories keep inheriting the baseline
- Other months are unaffected
- Categories NOT in the payload are NOT deleted — PATCH semantics

Multi-month ranges in the dashboard sum the effective budgets per month
(respecting overrides), not a single global multiplied by N.

### Annual budget column

Every category has an `annual_usd` field (migration 004). Defaults to
`monthly_usd × 12`; editable independently for categories with non-linear
annual patterns (e.g. property tax is once a year, not 12× monthly). Live
total shown in the editor next to the monthly total.

---

## Categories

### Hierarchy (1 level max)

Categories can be top-level (parent = null) or subs (parent = some top-level
key). Nesting beyond 1 level is rejected by the API.

### Parent rollup rule

**A parent category with at least one sub has its own `usd` / `annual_usd`
force-zeroed** (migration 006 + PUT /api/budget enforcement + frontend editor
logic). Reason: the parent's total budget should come from its subs only, so
the number doesn't duplicate or confuse.

The dashboard:
- Shows the parent as a group header with the rolled-up total
- Lists subs indented underneath with their individual numbers
- Gastos registered directly to the parent key still count in the parent's
  spent total but don't contribute to the parent's budget number

### Daniel's current taxonomy (as of last sync)

```
TOP-LEVEL (leaves, have their own budget directly):
  supermercado $350   restaurante $300   gasolina $270
  segurosalud $250    viaje $200         rappi $200
  telecom $181        salud $150         cafe $60
  uber $50            entretenimiento $50 comisiones $15
  tecnologia $0       ropa $0            obsequio $0       otro $0

PARENTS WITH SUBS (own usd = 0, budget = sum of children):
  🏠 hogar
    ├─ hipoteca $1500     ├─ empleada $660     ├─ admin $446
    ├─ mado $400          ├─ servicios $212    ├─ muebles $0
    ├─ reparaciones $0    └─ prediales $0

  🚗 carro
    ├─ peajes $40         ├─ parqueadero $20    ├─ mantenimiento $15
    ├─ multas $0          ├─ mecanica $0        └─ accesorios $0

  🛡️ seguros
    ├─ seguro_carro_land $95  ├─ seguro_tesla $90  └─ seguro_bubba $12

  💊 salud (top-level also a leaf)
    └─ trainer $130 (nested under salud — small hierarchy)

  🐾 mascotas
    └─ bubba $80

  📺 suscripciones
    └─ claude $100
```

(Subject to Daniel's edits in the ⚙️ editor.)

### ALIASES vocabulary

`bot.py::ALIASES` is a ~300-entry dict mapping natural Spanish/Colombian words
to category keys. Accent-stripped and case-insensitive. `smart_resolve_from_words`
scans ALL words in a message (not just the first) to find the best match.

Examples: `mobiliario` → `muebles`, `plomero` → `reparaciones`, `fotomulta`
→ `multas`, `taller` → `mecanica`, `gym` → `trainer`, `vet` → `bubba`,
`cruzverde` → `salud`, `claudepro` → `claude`, etc.

To add a new alias: edit the dict in `bot.py`, push, deploy. Takes effect
immediately (no migration needed).

---

## Multi-currency

Supported currencies: **COP, USD, BOB, AED**.

Bot parser accepts:
- `50000` (default COP)
- `100usd` / `usd 100`
- `45bob` / `bob 45`
- `200aed` / `aed 200`

Conversion table (editable per-month via `exchange_rates_history`):

| From | To USD | Default rate |
|---|---|---|
| COP | ÷ TRM | 3700 COP/USD |
| BOB | ÷ BOB_RATE | 9.20 BOB/USD |
| AED | ÷ AED_RATE | 3.67 AED/USD |

Every expense stores both `monto_cop` and `monto_usd`. Income entries store
`monto` (native) + `currency` + `monto_usd` computed at entry time using that
period's rates.

---

## Database schema

```sql
-- Core expense table
CREATE TABLE expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    user_name TEXT,
    fecha TEXT,                -- YYYY-MM-DD
    monto_cop REAL,
    monto_usd REAL,
    categoria TEXT,
    nota TEXT,
    created_at TEXT,
    metodo_pago TEXT DEFAULT 'Sin especificar'
);

-- Legacy custom categories (v7 — still honored, merged into BUDGET at load)
CREATE TABLE custom_categories (
    name TEXT PRIMARY KEY, icon TEXT, label TEXT, created_at TEXT
);

-- Config blobs: budget, payment_methods, rates, migrations_applied
CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,       -- JSON
    updated_at TEXT NOT NULL
);

-- Per-month budget overrides (migration 005)
CREATE TABLE budget_history (
    period TEXT NOT NULL,      -- YYYY-MM
    category TEXT NOT NULL,
    usd REAL NOT NULL,
    annual_usd REAL,
    note TEXT,
    created_at TEXT, updated_at TEXT,
    PRIMARY KEY (period, category)
);

-- Income tracking (migration 002)
CREATE TABLE income_sources (
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    icon TEXT DEFAULT '💰',
    currency TEXT DEFAULT 'USD',
    expected_usd REAL DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TEXT, updated_at TEXT
);

CREATE TABLE income_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    period TEXT NOT NULL,      -- YYYY-MM — derived from fecha
    fecha TEXT,                -- YYYY-MM-DD
    monto REAL NOT NULL,
    currency TEXT NOT NULL,
    monto_usd REAL NOT NULL,
    rate_used REAL,
    nota TEXT,
    created_at TEXT, updated_at TEXT
);

CREATE TABLE exchange_rates_history (
    period TEXT PRIMARY KEY,   -- YYYY-MM
    trm REAL, bob_rate REAL, aed_rate REAL,
    updated_at TEXT
);
```

---

## Migrations applied

All tracked in `config.migrations_applied` as a JSON array. Each runs exactly
once then is skipped.

| # | Name | Purpose |
|---|---|---|
| 001 | `001_add_hogar_carro_subcategories` | Seed reparaciones/prediales/multas/mecanica as subs, set hogar/carro parent=null explicitly |
| 002 | `002_create_income_tables` | Create income_sources, income_entries, exchange_rates_history. Seed 4 default sources. |
| 003 | `003_fix_income_period_from_fecha` | Backfill: `UPDATE period = substr(fecha, 1, 7)` where they disagreed (fixed a pre-launch bug) |
| 004 | `004_annual_budget` | Add `annual_usd` to every category in config.budget, default = `usd × 12` |
| 005 | `005_budget_history` | Create `budget_history` table for per-month overrides |
| 006 | `006_clear_parent_amounts` | Force `usd = 0` and `annual_usd = 0` for any parent-with-children in config.budget and budget_history |
| 007 | `007_add_falabella_and_bancolombia` | Add Falabella (Mastercard Dani) and Bancolombia (MC Dani) to config.payment_methods if missing |

---

## REST API endpoints

All JSON, all unauthenticated (auth is a pending item — see [Open issues](#open-issues)).

### Read

```
GET /                                  static frontend (index.html)
GET /api/health                        sanity check
GET /api/summary?month=YYYY-MM         single-month summary
GET /api/summary?from=&to=             multi-month range
GET /api/pnl?month=YYYY-MM             single-month P&L (income + expenses + net)
GET /api/pnl?from=&to=                 multi-month P&L
GET /api/budget                        baseline config.budget
GET /api/budget/effective/{period}     baseline + overrides for a specific month
GET /api/budget/history                list of periods with overrides
GET /api/payment-methods               config.payment_methods
GET /api/rates                         current globals (TRM / BOB / AED)
GET /api/rates/history                 list all per-month rate snapshots
GET /api/rates/history/{period}        rates for a specific period
GET /api/categories                    baseline budget + per-month actuals (current month)
GET /api/expenses?month=&limit=        list expenses for a month
GET /api/expenses/recent?limit=        latest N across all months
GET /api/export/csv?month=             download CSV
GET /api/income/sources                list income sources
GET /api/income/entries?month=         list income entries for a month
```

### Write

```
PUT    /api/expenses/{id}              partial update (monto, fecha, categoria, metodo, nota)
DELETE /api/expenses/{id}              delete one expense
PUT    /api/budget                     replace baseline budget (parent amounts zeroed)
PUT    /api/budget/effective/{period}  upsert per-month overrides (PATCH semantics)
DELETE /api/budget/effective/{period}/{category}  revert one override to baseline
DELETE /api/budget/effective/{period}  revert whole period to baseline
PUT    /api/payment-methods            replace payment_methods config
PUT    /api/rates                      replace global rates (current defaults)
PUT    /api/rates/history/{period}     set per-month rates + recompute income_entries
POST   /api/income/sources             create source
PUT    /api/income/sources/{key}       edit source (label, icon, currency, expected_usd)
DELETE /api/income/sources/{key}       delete (refuses if entries exist)
POST   /api/income/entries             create entry (period derived from fecha)
PUT    /api/income/entries/{id}        edit
DELETE /api/income/entries/{id}        delete
```

### Key response shapes

- `/api/summary` includes: `total_usd`, `total_cop`, `budget_limit_usd` (ideal ×
  n_months), `effective_total_budget_usd` (configured sum), `expected_income_monthly_usd`,
  `expected_income_total_usd`, `n_months`, `budget_was_dynamic`, `categories`,
  `by_user`, `by_payment_method`
- `/api/pnl` extends summary with `income` and `net` sections

---

## Telegram bot

### Commands

- `/start` — welcome + main menu
- `/menu` — main menu keyboard
- `/gasto` — register expense (interactive)
- `/status` or `/resumen` — text summary of current month
- `/semana` — last 7 days
- `/presupuesto` — budget vs actual breakdown
- `/historial` — recent expenses
- `/exportar` — CSV of current month
- `/borrar {id}` — delete expense by ID
- `/nuevacat {name} [icon] [label]` — create custom category
- `/dashboard` — generate HTML dashboard (legacy before the web dashboard existed)
- `/ayuda` or `/help` — help

### Free-text registration

The main usage is free-text: `<monto> <categoria> <nota>`. The bot:

1. Parses the amount + optional currency prefix/suffix (COP/USD/BOB/AED)
2. Scans all subsequent words against `ALIASES` + `BUDGET` (not just the first word)
3. If a category matches, registers the expense directly and shows the
   payment method selector
4. If no word matches, shows the category picker menu with a "create new" option

---

## Dashboard UI

Mobile-first single page at `/`. Alpine.js + Tailwind (both CDN).

### Layout zones (post-redesign)

1. **Header** — title + ? gear ☀ reload + month selector
2. **Month bar** — sticky, 18 months as pills + window selector (1m / 3m / 6m / 12m / YTD)
3. **P&G card / Ingresos zone** — income total, actual vs plan, per source breakdown
4. **Objetivos zone** — spent total + 3 reference gauges (Ideal / Plan / Config) +
   sobrante panel (motivational savings marker)
5. **Gastos zone** (desktop: 3-col grid):
   - Por usuario (Daniel / Mado)
   - Por método de pago (clickable filters for Movimientos)
   - Por categoría (rollup of parents + subs)
6. **Movimientos** — filterable list (user + method), tap to edit modal
7. **Footer** — bot username, current rates

### Modals

- Edit expense (tap a movement row)
- Settings / category editor (⚙️ button)
- Income editor — 3 tabs (entradas / fuentes / tasas del mes)
- User guide (? button) — 7 sections with intro flow for first-time users

### Three reference gauges

Each has:
- Title + icon
- Subtitle explaining what it is
- Big % comparing spent vs reference
- Thin progress bar
- Reference amount label
- "libre $X" or "sobre $X" delta

The gauges are side by side in a `grid-cols-3` on all breakpoints.

---

## Open issues

See `~/.claude/.../memory/expense_bot_open_issues.md` for the current list of
known bugs, TODOs, and deferred items. Key ones as of the last sync:

1. **No auth on the dashboard** — the URL is still public. PIN + cookie
   auth is the next major addition.
2. **`ALLOWED_USER_IDS` env var empty** — anyone who finds the bot can talk
   to it. To be configured from the dashboard settings once auth exists.
3. **send_monthly_csv bug** in bot.py — will fail if the 1st of any month
   actually triggers it (not yet fired). Has wrong table/column names.
4. **Reconciliation feature** (PDF upload + matching) documented but not
   implemented. See `docs/reconciliation_format.md`.

---

**Last updated:** 2026-04-15 (post commit `3061134` — Part A dashboard
redesign). If something in this document contradicts the code, the code is
right and this doc is stale — please update.
