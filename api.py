"""
REST API for the expense-bot dashboard.

Mounted into the same asyncio event loop as the bot (see bot.main() →
run_bot_and_api). Serves the static frontend at / and all JSON endpoints
under /api/*.

Currently unauthenticated — auth (PIN + signed cookie) is a pending task.
Keep the Railway URL private until that lands.
"""
from __future__ import annotations

import csv
import io
import re
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Importing bot gives us access to DB_PATH and the live config dicts
# (BUDGET, PAYMENT_METHODS, TRM, BOB_RATE, AED_RATE, BUDGET_LIMIT_USD) without
# re-reading the config table on every request. It also means bot.py is the
# single source of truth for schema and config — api.py is a thin wrapper.
import bot

STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))
if not STATIC_DIR.exists():
    # Local dev fallback — api.py is in the repo root, static/ sibling.
    STATIC_DIR = Path(__file__).parent / "static"


# ──────────────── Pydantic models (module level) ────────────────
# IMPORTANT: these MUST live at module level, not nested inside
# make_api_app(). Otherwise FastAPI's type resolution — which goes
# through typing.get_type_hints() and only looks at module globals —
# can't find them, and falls back to treating the parameter as a
# primitive query param. That's why an earlier refactor where these
# were nested caused POST/PUT bodies to be rejected with
# `loc: ["query", "<param>"]` errors.

class ExpenseUpdate(BaseModel):
    monto_cop: Optional[float] = None
    monto_usd: Optional[float] = None
    fecha: Optional[str] = None
    categoria: Optional[str] = None
    nota: Optional[str] = None
    metodo_pago: Optional[str] = None
    # Accounting class: 'gasto' (default OPEX), 'mobiliario', 'equipos',
    # 'vehiculo' (CAPEX subtypes). Validated below in the endpoint
    # against CLASES_CONTABLES_PERMITIDAS so a malformed PUT can't insert
    # garbage values.
    clase_contable: Optional[str] = None


class ExpenseCreate(BaseModel):
    """Create a new expense from the dashboard or restore one from a backup.

    `id` is optional: if provided and free, it's used (handy for restoring
    a deleted row by its original id); if omitted, SQLite assigns the
    next autoincrement.
    """
    user_name: str
    fecha: str  # YYYY-MM-DD
    monto_cop: float
    monto_usd: Optional[float] = None
    categoria: str
    nota: Optional[str] = ""
    metodo_pago: Optional[str] = "Sin especificar"
    clase_contable: Optional[str] = "gasto"
    id: Optional[int] = None  # only for restore-by-id; usually leave blank


# ──────────────── Accounting metadata ────────────────
# Single source of truth for the OPEX/CAPEX split. Used by the
# /api/summary aggregation, the /api/expenses validation, and exposed
# to the frontend so it doesn't have to hardcode the same labels.
CLASES_CONTABLES_PERMITIDAS = {"gasto", "mobiliario", "equipos", "vehiculo"}

CLASES_CONTABLES_META = [
    {"clase": "gasto",      "label": "Gasto corriente",     "icon": "💸"},
    {"clase": "mobiliario", "label": "Mobiliario / Enseres", "icon": "🛋️"},
    {"clase": "equipos",    "label": "Equipos",              "icon": "💻"},
    {"clase": "vehiculo",   "label": "Vehículo",             "icon": "🚗"},
]


# ──────────────── Diferimiento (Approach A: filas hijas ligadas) ────────────────
# Cuando Daniel difiere un gasto en N meses, el sistema modifica el gasto
# original para que su monto pase a 1/N, y crea N-1 hijos con la misma
# metadata (categoria, user, método, nota, clase) en cada mes futuro.
# Todos comparten un deferred_group_id (= id del padre original) para que
# se puedan borrar como grupo.
DEFERRAL_MODES_PERMITIDOS = {"upfront", "credito"}
DEFERRAL_MODES_META = [
    {"mode": "upfront", "label": "Pagado completo este mes", "icon": "💳",
     "description": "La tarjeta cobró todo de una. Solo distribuyo contablemente."},
    {"mode": "credito", "label": "Difiriendo con tarjeta",   "icon": "📅",
     "description": "El banco cobra 1/N por mes durante N meses."},
]


class DeferralRequest(BaseModel):
    months: int  # 2..12
    mode: str    # 'upfront' or 'credito'


# ──────────────── Reconciliación de extractos bancarios ────────────────
# Daniel sube un JSON extraído con Claude chat de uno o varios extractos
# bancarios consolidados. El backend clasifica cada movimiento, lo matchea
# contra los expenses del bot, y devuelve el diff. Daniel reconcilia en
# la UI: confirma matches, agrega los faltantes, marca duplicados.

class ReconcileTransaction(BaseModel):
    fecha: str  # YYYY-MM-DD
    monto_cop: float
    descripcion: str
    moneda_original: Optional[str] = None
    monto_original: Optional[float] = None


class ReconcileImportRequest(BaseModel):
    label: Optional[str] = None  # ej. "Cierre BdB 26 abr 2026"
    notes: Optional[str] = None
    transactions: list[ReconcileTransaction]


class ReconcileItemUpdate(BaseModel):
    status: Optional[str] = None  # 'reviewed' | 'ignored' | 'pending'
    notes: Optional[str] = None
    matched_expense_id: Optional[int] = None


class ReconcileBulkCreateRequest(BaseModel):
    """Para crear N expenses de cargos del banco con una sola llamada."""
    item_ids: list[int]
    categoria: Optional[str] = "comisiones"
    user_name: Optional[str] = "Daniel"


# Patrones de clasificación. ORDEN IMPORTA (primer match gana).
RECONCILE_PATTERNS = [
    ("gmf",                    r"gravamen|4\s*x\s*1[\.,]?000|impuesto.*4x1"),
    ("conversion_int",         r"conversion\s+compra\s+internacional"),
    ("comision",               r"comision\s+(transferencia|ach|interbancaria|por\s+internet)|iva\s+comision"),
    ("cuota_manejo",           r"cuota\s+de\s+manejo"),
    ("interes_tc",             r"intereses\s+corrientes|intereses\s+del\s+periodo"),
    ("seguro_deudor",          r"seg\s+deud|seguro\s+deud"),
    ("avance_cajero",          r"avance\s+cajero|avance\s+en\s+cajero|comision\s+avance"),
    ("foreign_exchange_fee",   r"foreign\s+exchange\s+fee"),
    ("transferencia_saliente", r"envio\s+por\s+bre|envio\s+a\s+|cargo\s+transferencia\s+ach|cargo\s+transferencia\s+canal"),
    ("pago_pse",               r"pago\s+por\s+pse|pago\s+con\s+bre|car\s+domi\s+tra"),
    # 'compra_tc' es el catch-all para items con prefijo de TC
]

# Prefijo del extracto → método de pago en el bot
RECONCILE_PREFIX_TO_METHOD = {
    "BdB Mastercard":         "BDB MC Dani",
    "BdB LATAM Visa Daniel":  "BDB Visa Latam Dani",
    "BdB LATAM Visa Maria":   "BDB Visa Latam Mado",
    "BdB LATAM Visa Maria DOlores": "BDB Visa Latam Mado",
    "BBVA TCBLACK":           "BBVA MC Dani",
    "Wio":                    "WIO MC Dani",
    "BdB Ahorros":            "Transferencia BDB",
    "BBVA Ahorros":           "Transferencia BBVA",
}

# Tipos que se considera que son "cargos del banco" (auto-categorizables
# como comisiones o impuestos bancarios, no compras del usuario).
RECONCILE_BANK_CHARGE_TYPES = {
    "gmf", "conversion_int", "comision", "cuota_manejo",
    "interes_tc", "seguro_deudor", "avance_cajero", "foreign_exchange_fee",
}


class RatesUpdate(BaseModel):
    TRM: float
    BOB_RATE: float
    AED_RATE: float


class IncomeSourceCreate(BaseModel):
    key: str
    label: str
    icon: Optional[str] = "💰"
    currency: Optional[str] = "USD"
    expected_usd: Optional[float] = 0.0
    active: Optional[int] = 1


class IncomeEntryCreate(BaseModel):
    source_key: str
    period: str  # YYYY-MM
    fecha: Optional[str] = None  # YYYY-MM-DD
    monto: float
    currency: str
    nota: Optional[str] = ""


class RatesHistoryUpdate(BaseModel):
    TRM: float
    BOB_RATE: float
    AED_RATE: float


def _query_all(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT against the expenses DB and return list of dicts."""
    conn = sqlite3.connect(bot.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _month_prefix(month: Optional[str]) -> str:
    """Normalize a month param. `month=YYYY-MM` or None → current month."""
    if month:
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(400, f"invalid month: {month!r}, expected YYYY-MM")
        return month
    now = datetime.now()
    return f"{now.year}-{now.month:02d}"


# Bank → {country, native currency, flag emoji}. Used to enrich the
# by_payment_method breakdown so the frontend can show 🇨🇴 / 🇦🇪 / 🇺🇸 flags
# alongside the native currency (AED for UAE cards, COP for CO cards, etc.).
# The primary display is still USD + COP by default — native currency is
# a tooltip/badge.
BANK_METADATA = {
    "BDB":          {"country": "CO", "currency": "COP", "flag": "🇨🇴"},
    "BBVA":         {"country": "CO", "currency": "COP", "flag": "🇨🇴"},
    "Bancolombia":  {"country": "CO", "currency": "COP", "flag": "🇨🇴"},
    "BANCOLOMBIA":  {"country": "CO", "currency": "COP", "flag": "🇨🇴"},
    "Falabella":    {"country": "CO", "currency": "COP", "flag": "🇨🇴"},
    "WIO":          {"country": "AE", "currency": "AED", "flag": "🇦🇪"},
    "ENBD":         {"country": "AE", "currency": "AED", "flag": "🇦🇪"},
    "Chase":        {"country": "US", "currency": "USD", "flag": "🇺🇸"},
    "CHASE":        {"country": "US", "currency": "USD", "flag": "🇺🇸"},
    "Transferencia": {"country": "—",  "currency": "MIXED", "flag": "🔄"},
    "Efectivo":     {"country": "—",  "currency": "MIXED", "flag": "💵"},
}


def _bank_from_method(metodo: str) -> str:
    """Extract the bank key from a free-form metodo_pago string.

    Examples:
      'BDB Visa Latam Dani' → 'BDB'
      'Bancolombia MC Dani' → 'Bancolombia'
      'Efectivo'            → 'Efectivo'
      'Sin especificar'     → '' (unknown)
      None / ''             → '' (unknown)
    """
    if not metodo:
        return ""
    m = metodo.strip()
    if m == "Sin especificar":
        return ""
    if m == "Efectivo":
        return "Efectivo"
    # Bank is the first whitespace-delimited token.
    first = m.split(None, 1)[0] if m else ""
    return first


def _expected_income_monthly(conn) -> float:
    """Sum of expected_usd across all ACTIVE income sources. Represents the
    user's projected monthly income baseline — the 'plan de ingresos'.

    Independent of how much actually came in (income_entries). This is the
    plan, not the reality. The 'tentativo del mes' gauge compares spending
    vs this number, not vs a hardcoded lifestyle cap.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(expected_usd), 0) FROM income_sources WHERE active = 1"
    ).fetchone()
    return float(row[0] or 0)


def _iter_months(pfrom: str, pto: str):
    """Yield 'YYYY-MM' strings from pfrom through pto inclusive."""
    fy, fm = int(pfrom[:4]), int(pfrom[5:7])
    ty, tm = int(pto[:4]), int(pto[5:7])
    y, m = fy, fm
    while (y, m) <= (ty, tm):
        yield f"{y}-{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def _effective_budget_for_period(conn, period: str) -> dict:
    """Return the effective per-category budget for a specific YYYY-MM.

    Semantics:
      - Start from the baseline (bot.BUDGET, which mirrors config.budget)
      - Overlay any rows from budget_history WHERE period=? (period overrides)
      - Metadata (label, icon, parent, tipo) always comes from the baseline —
        overrides only carry monetary amounts (usd, annual_usd)
      - Parents-with-children have their own monetary amounts force-zeroed:
        the parent budget is semantically the sum of its children, and any
        stored value at the parent level is treated as a vestigial duplicate.

    Returns: { category_key: {usd, annual_usd, tipo, icon, label, parent,
                              is_override, baseline_usd, baseline_annual_usd,
                              has_children} }
    """
    # First pass: raw baseline + metadata
    out: dict[str, dict] = {}
    for k, v in bot.BUDGET.items():
        out[k] = {
            "usd": float(v.get("usd", 0) or 0),
            "annual_usd": float(v.get("annual_usd", (v.get("usd", 0) or 0) * 12) or 0),
            "tipo": v.get("tipo", "variable"),
            "icon": v.get("icon", "📦"),
            "label": v.get("label", k),
            "parent": v.get("parent"),
            "is_override": False,
            "baseline_usd": float(v.get("usd", 0) or 0),
            "baseline_annual_usd": float(v.get("annual_usd", (v.get("usd", 0) or 0) * 12) or 0),
            "has_children": False,
        }

    # Identify parents-with-children (top-level categories that have at
    # least one sub pointing at them)
    parents_with_children: set[str] = set()
    for k, v in out.items():
        if v.get("parent"):
            parents_with_children.add(v["parent"])
    for pk in parents_with_children:
        if pk in out:
            out[pk]["has_children"] = True

    # Overlay overrides from budget_history for the requested period
    for cat, usd, annual in conn.execute(
        "SELECT category, usd, annual_usd FROM budget_history WHERE period = ?",
        (period,),
    ).fetchall():
        if cat in out:
            out[cat]["usd"] = float(usd or 0)
            if annual is not None:
                out[cat]["annual_usd"] = float(annual)
            out[cat]["is_override"] = True

    # Belt-and-suspenders: parents-with-children have their direct amounts
    # forced to zero, regardless of what's stored in baseline or overrides.
    # The parent budget == sum of its children (computed elsewhere on read).
    for pk in parents_with_children:
        if pk in out:
            out[pk]["usd"] = 0.0
            out[pk]["annual_usd"] = 0.0
            out[pk]["baseline_usd"] = 0.0
            out[pk]["baseline_annual_usd"] = 0.0

    return out


def _sum_effective_budget_over_range(conn, pfrom: str, pto: str) -> tuple[dict, bool]:
    """Sum effective per-category budgets across [pfrom..pto] inclusive.

    Returns (totals, had_overrides) where totals is:
      { category_key: {usd_sum, annual_usd_sum, n_months, icon, label, parent} }
    and had_overrides is True if any month in the range used at least one
    budget_history row.
    """
    totals: dict[str, dict] = {}
    had_overrides = False
    for period in _iter_months(pfrom, pto):
        eff = _effective_budget_for_period(conn, period)
        for cat, info in eff.items():
            if info.get("is_override"):
                had_overrides = True
            slot = totals.setdefault(
                cat,
                {
                    "usd_sum": 0.0,
                    "annual_usd_sum": 0.0,
                    "n_months": 0,
                    "icon": info["icon"],
                    "label": info["label"],
                    "parent": info["parent"],
                },
            )
            slot["usd_sum"] += info["usd"]
            slot["annual_usd_sum"] += info["annual_usd"]
            slot["n_months"] += 1
    return totals, had_overrides


def _resolve_range(
    month: Optional[str],
    frm: Optional[str],
    to: Optional[str],
) -> tuple[str, str, int]:
    """Resolve a range specifier to (from_period, to_period, n_months).

    Precedence:
      1. If both `frm` and `to` are supplied → that's the range (inclusive).
      2. Else if only `month` is supplied → single-month range (from == to).
      3. Else → current month as a single-month range.

    Returns a 3-tuple: (from YYYY-MM, to YYYY-MM, months_in_range_inclusive).
    Raises HTTPException on bad input or inverted ranges.
    """
    import re as _re_local
    PAT = _re_local.compile(r"^\d{4}-\d{2}$")

    if frm or to:
        if not (frm and to):
            raise HTTPException(400, "'from' y 'to' deben ir los dos o ninguno")
        if not PAT.match(frm) or not PAT.match(to):
            raise HTTPException(400, f"rango inválido: {frm}..{to}")
        if frm > to:
            raise HTTPException(400, f"rango inválido: from ({frm}) > to ({to})")
        pfrom, pto = frm, to
    else:
        pfrom = pto = _month_prefix(month)

    fy, fm = int(pfrom[:4]), int(pfrom[5:7])
    ty, tm = int(pto[:4]), int(pto[5:7])
    n_months = (ty - fy) * 12 + (tm - fm) + 1
    if n_months <= 0 or n_months > 36:
        raise HTTPException(400, f"rango de {n_months} meses fuera de límites (1-36)")
    return pfrom, pto, n_months


def make_api_app() -> FastAPI:
    api = FastAPI(
        title="Expense Bot API",
        version="0.1.0",
        description="Read-only API powering the expense-bot dashboard.",
    )

    # Defensive: if api.py's view of the `bot` module is a different instance
    # than the one main() populated (can happen with some import/lifespan
    # orderings), load_config() repopulates BUDGET/PAYMENT_METHODS/rates from
    # the same config table the bot reads from. Idempotent, safe to call
    # multiple times.
    @api.on_event("startup")
    def _hydrate_config_on_startup():
        bot.load_config()
        print(
            f"🌐 API startup: BUDGET={len(bot.BUDGET)} cats, "
            f"PAYMENT_METHODS={len(bot.PAYMENT_METHODS)} groups, TRM={bot.TRM}"
        )

    # Also hydrate synchronously at app-creation time, so /api/health on the
    # very first request (before lifespan startup fires on some uvicorn
    # configs) still returns correct numbers.
    try:
        bot.load_config()
    except Exception as e:
        print(f"⚠️  api.make_api_app() initial load_config failed: {e!r}")

    @api.get("/api/health")
    def health():
        return {
            "ok": True,
            "bot": "expense-bot",
            "db_path": bot.DB_PATH,
            "categories": len(bot.BUDGET),
            "payment_method_groups": len(bot.PAYMENT_METHODS),
            "trm": bot.TRM,
            "bob_rate": bot.BOB_RATE,
            "aed_rate": bot.AED_RATE,
        }

    @api.get("/api/budget")
    def get_budget():
        """The full BUDGET dict as currently loaded in memory."""
        total = sum(v.get("usd", 0) for v in bot.BUDGET.values())
        return {
            "budget_limit_usd": bot.BUDGET_LIMIT_USD,
            "total_assigned_usd": total,
            "categories": bot.BUDGET,
        }

    @api.get("/api/payment-methods")
    def get_payment_methods():
        return {"methods": bot.PAYMENT_METHODS}

    @api.get("/api/rates")
    def get_rates():
        return {
            "TRM": bot.TRM,
            "BOB_RATE": bot.BOB_RATE,
            "AED_RATE": bot.AED_RATE,
        }

    @api.get("/api/expenses")
    def get_expenses(
        month: Optional[str] = Query(None, description="YYYY-MM. Defaults to current month."),
        limit: int = Query(1000, ge=1, le=10000),
    ):
        prefix = _month_prefix(month)
        rows = _query_all(
            "SELECT id, user_id, user_name, fecha, monto_cop, monto_usd, "
            "categoria, nota, created_at, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago, "
            "COALESCE(clase_contable, 'gasto') AS clase_contable, "
            "COALESCE(deferred_total, 1) AS deferred_total, "
            "COALESCE(deferred_index, 1) AS deferred_index, "
            "deferred_group_id, deferred_mode "
            "FROM expenses WHERE fecha LIKE ? "
            "ORDER BY fecha DESC, id DESC LIMIT ?",
            (f"{prefix}%", limit),
        )
        return {"month": prefix, "count": len(rows), "expenses": rows}

    @api.get("/api/expenses/recent")
    def get_recent_expenses(limit: int = Query(20, ge=1, le=500)):
        """Latest N expenses across all months."""
        rows = _query_all(
            "SELECT id, user_name, fecha, monto_cop, monto_usd, categoria, nota, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago, "
            "COALESCE(clase_contable, 'gasto') AS clase_contable, "
            "COALESCE(deferred_total, 1) AS deferred_total, "
            "COALESCE(deferred_index, 1) AS deferred_index, "
            "deferred_group_id, deferred_mode "
            "FROM expenses ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return {"count": len(rows), "expenses": rows}

    @api.get("/api/summary")
    def get_summary(
        month: Optional[str] = Query(None),
        frm: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = Query(None),
    ):
        """Summary for a month or a range of months.

        - Single month (backward-compat): pass `month=YYYY-MM`
        - Range: pass `from=YYYY-MM&to=YYYY-MM` (both inclusive)
        - Nothing → current month
        All per-category / global budgets are PRORATED to the window size:
        a 3-month range compares vs `monthly_budget * 3`.
        """
        pfrom, pto, n_months = _resolve_range(month, frm, to)
        rows = _query_all(
            "SELECT user_name, fecha, monto_cop, monto_usd, categoria, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago, "
            "COALESCE(clase_contable, 'gasto') AS clase_contable "
            "FROM expenses WHERE substr(fecha, 1, 7) BETWEEN ? AND ?",
            (pfrom, pto),
        )
        total_usd = sum(r["monto_usd"] for r in rows)
        total_cop = sum(r["monto_cop"] for r in rows)

        # Accounting view: split spending into OPEX (gasto corriente) vs CAPEX
        # (compras de bienes/equipos). Daniel marca cada gasto desde el
        # dashboard. Default 'gasto' garantiza que pre-flag todo cuenta como OPEX.
        # IMPORTANTE: total_usd sigue contando todo — los gauges existentes
        # (IDEAL/PLAN/CONFIG) no cambian. Esto es info adicional.
        by_clase_acc: dict[str, dict] = {
            m["clase"]: {**m, "total_usd": 0.0, "total_cop": 0.0, "count": 0}
            for m in CLASES_CONTABLES_META
        }
        for r in rows:
            c = r["clase_contable"] or "gasto"
            slot = by_clase_acc.setdefault(
                c, {"clase": c, "label": c, "icon": "📦", "total_usd": 0.0, "total_cop": 0.0, "count": 0}
            )
            slot["total_usd"] += float(r["monto_usd"] or 0)
            slot["total_cop"] += float(r["monto_cop"] or 0)
            slot["count"] += 1
        # Round and order: 'gasto' first (always primary), then by total desc
        by_clase_contable = []
        for clase_key in ["gasto", "mobiliario", "equipos", "vehiculo"]:
            if clase_key in by_clase_acc:
                slot = by_clase_acc.pop(clase_key)
                by_clase_contable.append({
                    **slot,
                    "total_usd": round(slot["total_usd"], 2),
                    "total_cop": round(slot["total_cop"], 0),
                })
        # Append any unexpected/legacy classes at the end (defensive)
        for slot in sorted(by_clase_acc.values(), key=lambda s: -s["total_usd"]):
            by_clase_contable.append({
                **slot,
                "total_usd": round(slot["total_usd"], 2),
                "total_cop": round(slot["total_cop"], 0),
            })
        total_opex_usd = round(
            next((s["total_usd"] for s in by_clase_contable if s["clase"] == "gasto"), 0.0),
            2,
        )
        total_capex_usd = round(
            sum(s["total_usd"] for s in by_clase_contable if s["clase"] != "gasto"),
            2,
        )

        # Pull effective budgets for the window — this walks each month and
        # merges baseline with any per-month overrides in budget_history.
        # Also pull the income plan (sum of expected_usd across active sources).
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            budget_totals, budget_was_dynamic = _sum_effective_budget_over_range(
                conn, pfrom, pto,
            )
            expected_income_monthly = _expected_income_monthly(conn)
        finally:
            conn.close()

        by_cat: dict[str, dict] = {}
        for r in rows:
            cat = r["categoria"]
            bt = budget_totals.get(cat) or {}
            cat_info = bot.BUDGET.get(cat, {"usd": 0, "icon": "📦", "label": cat})
            slot = by_cat.setdefault(
                cat,
                {
                    "categoria": cat,
                    "icon": bt.get("icon") or cat_info.get("icon", "📦"),
                    "label": bt.get("label") or cat_info.get("label", cat),
                    "monthly_budget_usd": cat_info.get("usd", 0),
                    "budget_usd": bt.get("usd_sum", 0),
                    "spent_usd": 0,
                    "count": 0,
                },
            )
            slot["spent_usd"] += r["monto_usd"]
            slot["count"] += 1
        categories = sorted(by_cat.values(), key=lambda c: c["spent_usd"], reverse=True)
        for c in categories:
            c["pct_of_budget"] = (c["spent_usd"] / c["budget_usd"]) if c["budget_usd"] > 0 else None

        by_user: dict[str, dict] = {}
        for r in rows:
            u = r["user_name"] or "Desconocido"
            slot = by_user.setdefault(u, {"user": u, "spent_usd": 0, "count": 0})
            slot["spent_usd"] += r["monto_usd"]
            slot["count"] += 1

        # Aggregate by payment method across the range. Shows USD + COP for
        # every card so Daniel can see at a glance how much he's charged to
        # each instrument. Enriched with bank metadata (flag, currency).
        by_pay: dict[str, dict] = {}
        for r in rows:
            m = r["metodo_pago"] or "Sin especificar"
            slot = by_pay.setdefault(
                m,
                {"method": m, "total_usd": 0.0, "total_cop": 0.0, "count": 0},
            )
            slot["total_usd"] += float(r["monto_usd"] or 0)
            slot["total_cop"] += float(r["monto_cop"] or 0)
            slot["count"] += 1
        by_payment_method = []
        for slot in sorted(by_pay.values(), key=lambda s: s["total_usd"], reverse=True):
            bank = _bank_from_method(slot["method"])
            meta = BANK_METADATA.get(bank, {"country": "—", "currency": "USD", "flag": "❓"})
            by_payment_method.append({
                **slot,
                "total_usd": round(slot["total_usd"], 2),
                "total_cop": round(slot["total_cop"], 0),
                "bank": bank,
                "flag": meta["flag"],
                "bank_currency": meta["currency"],
                "bank_country": meta["country"],
            })

        # Global budget limit: sum of all effective category budgets (what
        # was actually allocated across all cats for the window), so dynamic
        # month-to-month changes are reflected.
        effective_total_budget = sum(bt.get("usd_sum", 0) for bt in budget_totals.values())
        # Lifestyle cap (the $5000/mo reference), also reported for context.
        lifestyle_cap = bot.BUDGET_LIMIT_USD * n_months
        # Use the lifestyle cap as the gauge reference for continuity with
        # existing UI expectations.
        budget_limit = lifestyle_cap

        expected_income_total = expected_income_monthly * n_months

        return {
            "month": pto,  # legacy field — points at the end of the window
            "from": pfrom,
            "to": pto,
            "n_months": n_months,
            "budget_was_dynamic": budget_was_dynamic,
            "monthly_budget_limit_usd": bot.BUDGET_LIMIT_USD,
            "budget_limit_usd": budget_limit,
            "effective_total_budget_usd": round(effective_total_budget, 2),
            # Plan de ingresos — replaces the hardcoded lifestyle cap as the
            # primary reference for the 'tentativo del mes' gauge.
            "expected_income_monthly_usd": round(expected_income_monthly, 2),
            "expected_income_total_usd": round(expected_income_total, 2),
            "total_usd": round(total_usd, 2),
            "total_cop": round(total_cop, 0),
            "pct_of_budget": round(total_usd / budget_limit, 4) if budget_limit else 0,
            "available_usd": round(budget_limit - total_usd, 2),
            "count": len(rows),
            "categories": categories,
            "by_user": list(by_user.values()),
            "by_payment_method": by_payment_method,
            # Vista contable (OPEX vs CAPEX). Estos campos son INFORMATIVOS
            # — no cambian el comportamiento de los gauges existentes que
            # siguen usando total_usd. Daniel marca cada gasto desde el
            # dashboard (PUT /api/expenses/{id} con clase_contable).
            "total_opex_usd": total_opex_usd,
            "total_capex_usd": total_capex_usd,
            "by_clase_contable": by_clase_contable,
        }

    @api.get("/api/categories")
    def get_categories():
        """Every known category with its DEFINED budget AND its actual spend this month."""
        now = datetime.now()
        prefix = f"{now.year}-{now.month:02d}"
        actuals = _query_all(
            "SELECT categoria, SUM(monto_usd) AS spent, COUNT(*) AS n "
            "FROM expenses WHERE fecha LIKE ? GROUP BY categoria",
            (f"{prefix}%",),
        )
        by_cat = {a["categoria"]: a for a in actuals}
        out = []
        for cat, info in bot.BUDGET.items():
            row = {
                "categoria": cat,
                "icon": info.get("icon", "📦"),
                "label": info.get("label", cat),
                "tipo": info.get("tipo", "variable"),
                "budget_usd": info.get("usd", 0),
                "spent_usd": round(by_cat.get(cat, {}).get("spent") or 0, 2),
                "count_this_month": by_cat.get(cat, {}).get("n", 0),
            }
            out.append(row)
        out.sort(key=lambda r: r["budget_usd"], reverse=True)
        return {"categories": out}

    # ──────────────── Helpers shared by income + P&L ────────────────

    import re as _re
    _VALID_KEY = _re.compile(r"^[a-z_][a-z0-9_]*$")
    _VALID_PERIOD = _re.compile(r"^\d{4}-\d{2}$")
    _SUPPORTED_CURRENCIES = ("COP", "USD", "BOB", "AED")

    def _rates_for_period(conn, period: str) -> dict:
        """Return the rates applicable to a given YYYY-MM period.

        Prefers a row in exchange_rates_history; falls back to the current
        globals (bot.TRM/BOB_RATE/AED_RATE) if no historical row exists.
        Read-only — does NOT write a history row automatically.
        """
        row = conn.execute(
            "SELECT trm, bob_rate, aed_rate FROM exchange_rates_history WHERE period = ?",
            (period,),
        ).fetchone()
        if row:
            return {"TRM": row[0], "BOB_RATE": row[1], "AED_RATE": row[2]}
        return {"TRM": bot.TRM, "BOB_RATE": bot.BOB_RATE, "AED_RATE": bot.AED_RATE}

    def _to_usd(monto: float, currency: str, rates: dict) -> tuple[float, float]:
        """Convert an amount from its currency to USD using the given rates.
        Returns (monto_usd, rate_used)."""
        if currency == "USD":
            return round(float(monto), 2), 1.0
        if currency == "COP":
            rate = float(rates.get("TRM") or 1)
            return round(float(monto) / rate, 2), rate
        if currency == "BOB":
            rate = float(rates.get("BOB_RATE") or 1)
            return round(float(monto) / rate, 2), rate
        if currency == "AED":
            rate = float(rates.get("AED_RATE") or 1)
            return round(float(monto) / rate, 2), rate
        raise HTTPException(400, f"unsupported currency: {currency!r}")

    # ──────────────── Income sources (master list) ────────────────

    @api.get("/api/income/sources")
    def list_income_sources():
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT key, label, icon, currency, expected_usd, active, created_at, updated_at "
                "FROM income_sources ORDER BY label"
            ).fetchall()
            return {"sources": [dict(r) for r in rows]}
        finally:
            conn.close()

    @api.post("/api/income/sources")
    def create_income_source(src: IncomeSourceCreate):
        if not _VALID_KEY.match(src.key):
            raise HTTPException(400, f"clave inválida: {src.key!r}")
        if src.currency not in _SUPPORTED_CURRENCIES:
            raise HTTPException(400, f"moneda inválida: {src.currency}")
        now = datetime.now().isoformat()
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            try:
                conn.execute(
                    "INSERT INTO income_sources "
                    "(key, label, icon, currency, expected_usd, active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (src.key, src.label, src.icon or "💰", src.currency,
                     float(src.expected_usd or 0), int(src.active or 1), now, now),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                raise HTTPException(409, f"source {src.key!r} ya existe")
        finally:
            conn.close()
        return {"ok": True, "key": src.key}

    @api.put("/api/income/sources/{key}")
    def update_income_source(key: str, updates: dict = Body(...)):
        allowed = {"label", "icon", "currency", "expected_usd", "active"}
        bad = set(updates.keys()) - allowed
        if bad:
            raise HTTPException(400, f"campos no editables: {bad}")
        if "currency" in updates and updates["currency"] not in _SUPPORTED_CURRENCIES:
            raise HTTPException(400, f"moneda inválida: {updates['currency']!r}")
        if not updates:
            raise HTTPException(400, "nada que actualizar")
        now = datetime.now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [now, key]
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute(
                f"UPDATE income_sources SET {sets}, updated_at = ? WHERE key = ?",
                params,
            )
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"source {key!r} not found")
        finally:
            conn.close()
        return {"ok": True, "key": key}

    @api.delete("/api/income/sources/{key}")
    def delete_income_source(key: str):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM income_entries WHERE source_key = ?", (key,)
            ).fetchone()[0]
            if n > 0:
                raise HTTPException(
                    409,
                    f"{key!r} tiene {n} entradas. Bórralas o reasígnalas antes.",
                )
            r = conn.execute("DELETE FROM income_sources WHERE key = ?", (key,))
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"source {key!r} not found")
        finally:
            conn.close()
        return {"ok": True, "deleted": key}

    # ──────────────── Income entries ────────────────

    @api.get("/api/income/entries")
    def list_income_entries(month: Optional[str] = Query(None)):
        period = _month_prefix(month)
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT e.id, e.source_key, e.period, e.fecha, e.monto, e.currency, "
                "e.monto_usd, e.rate_used, e.nota, e.created_at, e.updated_at, "
                "s.label AS source_label, s.icon AS source_icon "
                "FROM income_entries e "
                "LEFT JOIN income_sources s ON e.source_key = s.key "
                "WHERE e.period = ? "
                "ORDER BY e.fecha DESC NULLS LAST, e.id DESC",
                (period,),
            ).fetchall()
            return {"month": period, "entries": [dict(r) for r in rows]}
        finally:
            conn.close()

    @api.post("/api/income/entries")
    def create_income_entry(entry: IncomeEntryCreate):
        if entry.currency not in _SUPPORTED_CURRENCIES:
            raise HTTPException(400, f"moneda inválida: {entry.currency!r}")
        if entry.monto is None or entry.monto <= 0:
            raise HTTPException(400, "monto debe ser > 0")
        # period is INFORMATIONAL — the authoritative assignment comes from fecha.
        # If fecha is missing, we fall back to `period`'s first day.
        if not _VALID_PERIOD.match(entry.period or ""):
            raise HTTPException(400, f"period inválido: {entry.period!r} (usa YYYY-MM)")
        fecha = entry.fecha or (entry.period + "-01")
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"fecha inválida: {fecha!r}")

        # Derive the canonical period from the fecha. The user's intent when
        # picking a date is "count this in that date's month" — regardless of
        # which month they had open in the modal.
        effective_period = fecha[:7]

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            src = conn.execute(
                "SELECT 1 FROM income_sources WHERE key = ?", (entry.source_key,)
            ).fetchone()
            if not src:
                raise HTTPException(404, f"source {entry.source_key!r} no existe")
            rates = _rates_for_period(conn, effective_period)
            monto_usd, rate_used = _to_usd(entry.monto, entry.currency, rates)
            now = datetime.now().isoformat()
            cur = conn.execute(
                "INSERT INTO income_entries "
                "(source_key, period, fecha, monto, currency, monto_usd, rate_used, nota, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry.source_key, effective_period, fecha, float(entry.monto),
                 entry.currency, monto_usd, rate_used, entry.nota or "", now, now),
            )
            conn.commit()
            return {
                "ok": True, "id": cur.lastrowid,
                "period": effective_period,
                "monto_usd": monto_usd, "rate_used": rate_used,
            }
        finally:
            conn.close()

    @api.put("/api/income/entries/{entry_id}")
    def update_income_entry(entry_id: int, updates: dict = Body(...)):
        # period is NOT directly editable — it is derived from fecha. Edit
        # fecha instead if you want to move an entry to a different month.
        allowed = {"monto", "currency", "fecha", "nota", "source_key"}
        bad = set(updates.keys()) - allowed
        if bad:
            raise HTTPException(400, f"campos no editables: {bad}")
        if "currency" in updates and updates["currency"] not in _SUPPORTED_CURRENCIES:
            raise HTTPException(400, f"moneda inválida")
        if "fecha" in updates and updates["fecha"]:
            try:
                datetime.strptime(updates["fecha"], "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"fecha inválida")

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            existing = conn.execute(
                "SELECT period, fecha, monto, currency FROM income_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if not existing:
                raise HTTPException(404, f"entry {entry_id} no existe")
            new_fecha = updates.get("fecha", existing[1])
            new_monto = float(updates.get("monto", existing[2]))
            new_currency = updates.get("currency", existing[3])
            if new_monto <= 0:
                raise HTTPException(400, "monto debe ser > 0")
            # period follows fecha
            new_period = (new_fecha or "")[:7] if new_fecha else existing[0]
            if not _VALID_PERIOD.match(new_period):
                raise HTTPException(400, f"fecha inválida derivó period {new_period!r}")
            rates = _rates_for_period(conn, new_period)
            monto_usd, rate_used = _to_usd(new_monto, new_currency, rates)
            updates["period"] = new_period
            updates["monto_usd"] = monto_usd
            updates["rate_used"] = rate_used
            now = datetime.now().isoformat()
            sets = ", ".join(f"{k} = ?" for k in updates)
            params = list(updates.values()) + [now, entry_id]
            conn.execute(
                f"UPDATE income_entries SET {sets}, updated_at = ? WHERE id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "id": entry_id, "period": new_period, "monto_usd": monto_usd}

    @api.delete("/api/income/entries/{entry_id}")
    def delete_income_entry(entry_id: int):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute("DELETE FROM income_entries WHERE id = ?", (entry_id,))
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"entry {entry_id} not found")
        finally:
            conn.close()
        return {"ok": True, "deleted": entry_id}

    # ──────────────── Exchange rates history (per-month) ────────────────

    @api.get("/api/rates/history")
    def list_rates_history():
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT period, trm, bob_rate, aed_rate, updated_at "
                "FROM exchange_rates_history ORDER BY period DESC"
            ).fetchall()
            return {
                "history": [
                    {"period": r[0], "TRM": r[1], "BOB_RATE": r[2],
                     "AED_RATE": r[3], "updated_at": r[4]}
                    for r in rows
                ],
                "current_globals": {
                    "TRM": bot.TRM, "BOB_RATE": bot.BOB_RATE, "AED_RATE": bot.AED_RATE,
                },
            }
        finally:
            conn.close()

    @api.get("/api/rates/history/{period}")
    def get_rates_for_period(period: str):
        if not _VALID_PERIOD.match(period):
            raise HTTPException(400, f"period inválido: {period!r}")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            rates = _rates_for_period(conn, period)
            has_row = conn.execute(
                "SELECT 1 FROM exchange_rates_history WHERE period = ?", (period,)
            ).fetchone() is not None
            return {"period": period, "rates": rates, "is_historical": has_row}
        finally:
            conn.close()

    @api.put("/api/rates/history/{period}")
    def update_rates_history(period: str, rates: RatesHistoryUpdate):
        if not _VALID_PERIOD.match(period):
            raise HTTPException(400, f"period inválido: {period!r}")
        if rates.TRM <= 0 or rates.BOB_RATE <= 0 or rates.AED_RATE <= 0:
            raise HTTPException(400, "las tasas deben ser positivas")
        now = datetime.now().isoformat()
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO exchange_rates_history (period, trm, bob_rate, aed_rate, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(period) DO UPDATE SET "
                "  trm=excluded.trm, bob_rate=excluded.bob_rate, "
                "  aed_rate=excluded.aed_rate, updated_at=excluded.updated_at",
                (period, float(rates.TRM), float(rates.BOB_RATE),
                 float(rates.AED_RATE), now),
            )
            conn.commit()

            # Recompute monto_usd for all income entries in this period so the
            # P&L for the month reflects the updated rates.
            new_rates = {"TRM": rates.TRM, "BOB_RATE": rates.BOB_RATE, "AED_RATE": rates.AED_RATE}
            entries = conn.execute(
                "SELECT id, monto, currency FROM income_entries WHERE period = ?",
                (period,),
            ).fetchall()
            for eid, monto, currency in entries:
                monto_usd, rate_used = _to_usd(monto, currency, new_rates)
                conn.execute(
                    "UPDATE income_entries SET monto_usd = ?, rate_used = ?, updated_at = ? WHERE id = ?",
                    (monto_usd, rate_used, now, eid),
                )
            conn.commit()
        finally:
            conn.close()
        return {
            "ok": True, "period": period,
            "rates": new_rates,
            "recomputed_income_entries": len(entries),
        }

    # ──────────────── P&L — THE main view ────────────────

    @api.get("/api/pnl")
    def get_pnl(
        month: Optional[str] = Query(None),
        frm: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = Query(None),
    ):
        """Consolidated P&L for a month or a range of months.

        - Single month: pass `month=YYYY-MM` (backward compatible)
        - Range:        pass `from=YYYY-MM&to=YYYY-MM` (inclusive on both ends)
        - Expense budget is PRORATED to the window (monthly × n_months).
        """
        pfrom, pto, n_months = _resolve_range(month, frm, to)
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # Income side — range query on period
            income_rows = conn.execute(
                "SELECT e.source_key, "
                "  COALESCE(s.label, e.source_key) AS label, "
                "  COALESCE(s.icon, '💰') AS icon, "
                "  COALESCE(SUM(e.monto_usd), 0) AS total_usd, "
                "  COUNT(*) AS n "
                "FROM income_entries e "
                "LEFT JOIN income_sources s ON e.source_key = s.key "
                "WHERE e.period BETWEEN ? AND ? "
                "GROUP BY e.source_key, s.label, s.icon "
                "ORDER BY total_usd DESC",
                (pfrom, pto),
            ).fetchall()
            income_by_source = [
                {
                    "source_key": r["source_key"],
                    "label": r["label"],
                    "icon": r["icon"],
                    "total_usd": round(float(r["total_usd"] or 0), 2),
                    "count": r["n"],
                }
                for r in income_rows
            ]
            income_total = round(sum(r["total_usd"] for r in income_by_source), 2)

            # Expenses — range query on fecha's month substring
            exp_rows = conn.execute(
                "SELECT categoria, SUM(monto_usd) AS total_usd, COUNT(*) AS n "
                "FROM expenses WHERE substr(fecha, 1, 7) BETWEEN ? AND ? "
                "GROUP BY categoria ORDER BY total_usd DESC",
                (pfrom, pto),
            ).fetchall()

            # Effective per-category budgets summed over the range — uses
            # budget_history overrides where present, baseline elsewhere.
            budget_totals, budget_was_dynamic = _sum_effective_budget_over_range(
                conn, pfrom, pto,
            )

            expense_by_cat = []
            for r in exp_rows:
                cat = r["categoria"]
                info = bot.BUDGET.get(cat, {})
                bt = budget_totals.get(cat) or {}
                expense_by_cat.append({
                    "categoria": cat,
                    "label": info.get("label", cat),
                    "icon": info.get("icon", "📦"),
                    "monthly_budget_usd": info.get("usd", 0),
                    "budget_usd": bt.get("usd_sum", 0),
                    "total_usd": round(float(r["total_usd"] or 0), 2),
                    "count": r["n"],
                })
            expense_total = round(sum(c["total_usd"] for c in expense_by_cat), 2)

            # Rates: use the TO-period's historical rates as the reference
            rates = _rates_for_period(conn, pto)

            net = round(income_total - expense_total, 2)
            pct_of_income = round(net / income_total, 4) if income_total > 0 else None

            budget_limit = bot.BUDGET_LIMIT_USD * n_months
            effective_total_budget = sum(bt.get("usd_sum", 0) for bt in budget_totals.values())
            expected_income_monthly = _expected_income_monthly(conn)
            expected_income_total = expected_income_monthly * n_months

            return {
                "month": pto,  # legacy: points at the end of the window
                "from": pfrom,
                "to": pto,
                "n_months": n_months,
                "budget_was_dynamic": budget_was_dynamic,
                "rates": rates,
                "income": {
                    "total_usd": income_total,
                    "by_source": income_by_source,
                    "expected_monthly_usd": round(expected_income_monthly, 2),
                    "expected_total_usd": round(expected_income_total, 2),
                },
                "expenses": {
                    "total_usd": expense_total,
                    "by_category": expense_by_cat,
                    "budget_limit_usd": budget_limit,
                    "monthly_budget_limit_usd": bot.BUDGET_LIMIT_USD,
                    "effective_total_budget_usd": round(effective_total_budget, 2),
                    "expected_income_monthly_usd": round(expected_income_monthly, 2),
                    "expected_income_total_usd": round(expected_income_total, 2),
                },
                "net": {
                    "usd": net,
                    "pct_of_income": pct_of_income,
                    "status": ("positive" if net > 0 else "negative" if net < 0 else "neutral"),
                },
            }
        finally:
            conn.close()

    # ──────────────── Write endpoints: config ────────────────

    @api.get("/api/budget/effective/{period}")
    def get_effective_budget(period: str):
        """Effective per-category budget for a specific YYYY-MM.

        Returns the baseline with any per-month overrides from budget_history
        merged in. Each category includes:
          - usd, annual_usd  (effective values for this period)
          - is_override      (True if budget_history had a row)
          - baseline_usd, baseline_annual_usd (the fallback values)
        """
        if not _VALID_PERIOD.match(period):
            raise HTTPException(400, f"period inválido: {period!r} (usa YYYY-MM)")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            eff = _effective_budget_for_period(conn, period)
        finally:
            conn.close()
        total_usd = sum(v["usd"] for v in eff.values())
        total_annual = sum(v["annual_usd"] for v in eff.values())
        overrides = sum(1 for v in eff.values() if v["is_override"])
        return {
            "period": period,
            "categories": eff,
            "total_usd": round(total_usd, 2),
            "total_annual_usd": round(total_annual, 2),
            "overrides_count": overrides,
            "baseline_monthly_limit_usd": bot.BUDGET_LIMIT_USD,
        }

    @api.put("/api/budget/effective/{period}")
    def put_effective_budget(period: str, payload: dict = Body(...)):
        """Upsert per-month budget overrides for (period, *).

        Payload shape: { "restaurante": {"usd": 350, "annual_usd": 4200},
                         "viaje":       {"usd": 500} }

        Semantics (PATCH, not PUT-replace):
          - Each category in the payload is UPSERTed into budget_history for
            that period.
          - Categories NOT in the payload are NOT touched — their existing
            overrides (if any) remain. If you want to REMOVE an override,
            call DELETE /api/budget/effective/{period}/{category}.
          - If (usd, annual_usd) match the baseline for a category, we still
            write an explicit row (so the user's intent is recorded — even
            if the numbers coincidentally match baseline today, baseline
            changes later won't affect this period).
        """
        if not _VALID_PERIOD.match(period):
            raise HTTPException(400, f"period inválido: {period!r}")
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(400, "payload debe ser un objeto no vacío")

        now = datetime.now().isoformat()
        cleaned = []
        for cat, info in payload.items():
            if not isinstance(cat, str) or not _VALID_KEY.match(cat):
                raise HTTPException(400, f"clave inválida: {cat!r}")
            if cat not in bot.BUDGET:
                raise HTTPException(
                    400,
                    f"categoría {cat!r} no existe en el baseline. "
                    "Créala primero desde el editor base.",
                )
            if not isinstance(info, dict):
                raise HTTPException(400, f"categoría {cat}: debe ser objeto")
            try:
                usd = float(info.get("usd", 0) or 0)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{cat}: usd debe ser numérico")
            if usd < 0:
                raise HTTPException(400, f"{cat}: usd ≥ 0")
            raw_annual = info.get("annual_usd")
            if raw_annual is None or raw_annual == "":
                annual = round(usd * 12, 2)
            else:
                try:
                    annual = float(raw_annual)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{cat}: annual_usd numérico")
                if annual < 0:
                    raise HTTPException(400, f"{cat}: annual_usd ≥ 0")
            note = info.get("note") or None
            cleaned.append((period, cat, usd, annual, note, now, now))

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            for row in cleaned:
                conn.execute(
                    "INSERT INTO budget_history "
                    "(period, category, usd, annual_usd, note, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(period, category) DO UPDATE SET "
                    "  usd=excluded.usd, annual_usd=excluded.annual_usd, "
                    "  note=excluded.note, updated_at=excluded.updated_at",
                    row,
                )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "period": period, "categories_updated": len(cleaned)}

    @api.delete("/api/budget/effective/{period}/{category}")
    def delete_effective_budget_entry(period: str, category: str):
        """Remove a single per-month override. The category reverts to the
        baseline for that period."""
        if not _VALID_PERIOD.match(period):
            raise HTTPException(400, f"period inválido: {period!r}")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute(
                "DELETE FROM budget_history WHERE period = ? AND category = ?",
                (period, category),
            )
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"no hay override en {period}/{category}")
        finally:
            conn.close()
        return {"ok": True, "period": period, "category": category, "reverted_to_baseline": True}

    @api.delete("/api/budget/effective/{period}")
    def delete_all_effective_budget_entries(period: str):
        """Remove ALL overrides for a period. That entire month reverts to baseline."""
        if not _VALID_PERIOD.match(period):
            raise HTTPException(400, f"period inválido: {period!r}")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute(
                "DELETE FROM budget_history WHERE period = ?",
                (period,),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "period": period, "overrides_removed": r.rowcount}

    @api.get("/api/budget/history")
    def list_budget_history():
        """Summary of per-month overrides: which periods have overrides and
        how many categories per period."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT period, COUNT(*) AS n, "
                "       ROUND(SUM(usd), 2) AS total_usd, "
                "       MAX(updated_at) AS last_update "
                "FROM budget_history GROUP BY period ORDER BY period DESC"
            ).fetchall()
            return {
                "periods": [
                    {
                        "period": r[0],
                        "overrides_count": r[1],
                        "total_usd": r[2],
                        "last_update": r[3],
                    }
                    for r in rows
                ],
            }
        finally:
            conn.close()

    @api.put("/api/budget")
    def update_budget(new_budget: dict = Body(...)):
        """Replace the full BUDGET config with the provided dict.

        Validates keys (lowercase + underscore + digits), coerces values to the
        expected shape, and checks that every `parent` reference points to an
        existing key. Returns the cleaned dict + a total.
        """
        if not isinstance(new_budget, dict) or not new_budget:
            raise HTTPException(400, "budget must be a non-empty object")

        cleaned: dict[str, dict] = {}
        for key, info in new_budget.items():
            if not isinstance(key, str) or not _VALID_KEY.match(key):
                raise HTTPException(
                    400,
                    f"clave de categoría inválida: {key!r}. Usa solo letras minúsculas, números y guiones bajos.",
                )
            if not isinstance(info, dict):
                raise HTTPException(400, f"categoría {key}: el valor debe ser un objeto")
            try:
                usd = float(info.get("usd", 0) or 0)
            except (TypeError, ValueError):
                raise HTTPException(400, f"categoría {key}: 'usd' debe ser numérico")
            if usd < 0:
                raise HTTPException(400, f"categoría {key}: 'usd' debe ser ≥ 0")
            # Annual budget: default to 12× monthly if not explicitly provided
            raw_annual = info.get("annual_usd")
            if raw_annual is None or raw_annual == "":
                annual = round(usd * 12, 2)
            else:
                try:
                    annual = float(raw_annual)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"categoría {key}: 'annual_usd' debe ser numérico")
                if annual < 0:
                    raise HTTPException(400, f"categoría {key}: 'annual_usd' debe ser ≥ 0")
            parent = info.get("parent")
            if parent == "":
                parent = None
            cleaned[key] = {
                "usd": usd,
                "annual_usd": annual,
                "tipo": (info.get("tipo") or "variable"),
                "icon": (info.get("icon") or "📦"),
                "label": (info.get("label") or key.title()),
                "parent": parent,
            }

        # Validate parent references and prevent circular references
        for key, info in cleaned.items():
            parent = info["parent"]
            if parent is None:
                continue
            if parent == key:
                raise HTTPException(400, f"categoría {key}: no puede ser padre de sí misma")
            if parent not in cleaned:
                raise HTTPException(400, f"categoría {key}: parent {parent!r} no existe")
            # Only 1 level of nesting for now — parent must itself be top-level
            if cleaned[parent]["parent"] is not None:
                raise HTTPException(
                    400,
                    f"categoría {key}: parent {parent!r} ya es una subcategoría (no se permite anidamiento > 1 nivel)",
                )

        # Defense-in-depth: parents-with-children should never carry their own
        # budget amount — the parent budget is implicit in the sum of its subs.
        # If the frontend accidentally sends non-zero values for a parent that
        # now has children, zero them out here so the stored data stays
        # consistent with the 'rolled-up from subs' rule.
        parents_with_children = set()
        for key, info in cleaned.items():
            if info["parent"]:
                parents_with_children.add(info["parent"])
        for pk in parents_with_children:
            if pk in cleaned:
                cleaned[pk]["usd"] = 0.0
                cleaned[pk]["annual_usd"] = 0.0

        # Preserve existing expense categorías that aren't in the new budget —
        # refuse the save so the user is forced to either keep the category or
        # explicitly re-assign the expenses first.
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            existing_cats = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT categoria FROM expenses"
                ).fetchall() if r[0]
            ]
            missing = [c for c in existing_cats if c not in cleaned]
            if missing:
                raise HTTPException(
                    409,
                    f"No puedes eliminar categorías que tienen gastos asociados: {', '.join(sorted(missing))}. "
                    "Primero reasigna esos gastos a otra categoría desde el historial.",
                )
            conn.execute(
                "UPDATE config SET value = ?, updated_at = ? WHERE key = 'budget'",
                (json.dumps(cleaned, ensure_ascii=False), datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        # Reload into the shared module state so the bot reflects changes immediately.
        bot.load_config()

        return {
            "ok": True,
            "categories": len(cleaned),
            "total_usd": round(sum(v["usd"] for v in cleaned.values()), 2),
        }

    @api.put("/api/rates")
    def update_rates(rates: RatesUpdate):
        if rates.TRM <= 0 or rates.BOB_RATE <= 0 or rates.AED_RATE <= 0:
            raise HTTPException(400, "las tasas deben ser positivas")
        new_rates = {
            "TRM": int(rates.TRM),
            "BOB_RATE": float(rates.BOB_RATE),
            "AED_RATE": float(rates.AED_RATE),
        }
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            conn.execute(
                "UPDATE config SET value = ?, updated_at = ? WHERE key = 'rates'",
                (json.dumps(new_rates), datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        bot.load_config()
        return {"ok": True, "rates": new_rates}

    @api.put("/api/payment-methods")
    def update_payment_methods(methods: dict = Body(...)):
        """Replace the PAYMENT_METHODS config. Each top-level key is a 'bank'
        or grouping label, value is a list of card/account labels.
        """
        if not isinstance(methods, dict):
            raise HTTPException(400, "payment_methods must be an object")
        cleaned: dict[str, list[str]] = {}
        for bank, cards in methods.items():
            if not isinstance(bank, str) or not bank.strip():
                raise HTTPException(400, f"clave de banco inválida: {bank!r}")
            if not isinstance(cards, list):
                raise HTTPException(400, f"banco {bank}: cards debe ser lista")
            cleaned[bank.strip()] = [str(c).strip() for c in cards if str(c).strip()]
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            conn.execute(
                "UPDATE config SET value = ?, updated_at = ? WHERE key = 'payment_methods'",
                (json.dumps(cleaned, ensure_ascii=False), datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        bot.load_config()
        return {"ok": True, "groups": len(cleaned)}

    # ──────────────── Write endpoints: expenses ────────────────

    @api.post("/api/expenses")
    def create_expense(body: ExpenseCreate):
        """Create a new expense from the dashboard.

        Útil para:
          - Restaurar un gasto borrado por accidente (pasando `id`).
          - Crear gastos desde el dashboard sin pasar por Telegram.
        """
        if body.clase_contable and body.clase_contable not in CLASES_CONTABLES_PERMITIDAS:
            raise HTTPException(
                400,
                f"invalid clase_contable {body.clase_contable!r}, "
                f"expected one of {sorted(CLASES_CONTABLES_PERMITIDAS)}",
            )
        try:
            datetime.strptime(body.fecha, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"invalid fecha {body.fecha!r}, expected YYYY-MM-DD")
        # Compute monto_usd if not provided
        monto_usd = body.monto_usd
        if monto_usd is None:
            try:
                monto_usd = round(float(body.monto_cop) / bot.TRM, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                raise HTTPException(400, f"invalid monto_cop {body.monto_cop!r}")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            # If id is requested and free, use it; if taken, return 409.
            if body.id is not None:
                taken = conn.execute("SELECT 1 FROM expenses WHERE id = ?", (body.id,)).fetchone()
                if taken:
                    raise HTTPException(
                        409,
                        f"id {body.id} ya existe — usa otro o omite el campo para autoincrement",
                    )
                cur = conn.execute(
                    "INSERT INTO expenses "
                    "(id, user_id, user_name, fecha, monto_cop, monto_usd, categoria, "
                    " nota, created_at, metodo_pago, clase_contable, deferred_total, deferred_index) "
                    "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)",
                    (body.id, body.user_name, body.fecha, body.monto_cop, monto_usd,
                     body.categoria, body.nota or "", datetime.now().isoformat(),
                     body.metodo_pago or "Sin especificar", body.clase_contable or "gasto"),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO expenses "
                    "(user_id, user_name, fecha, monto_cop, monto_usd, categoria, "
                    " nota, created_at, metodo_pago, clase_contable, deferred_total, deferred_index) "
                    "VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)",
                    (body.user_name, body.fecha, body.monto_cop, monto_usd,
                     body.categoria, body.nota or "", datetime.now().isoformat(),
                     body.metodo_pago or "Sin especificar", body.clase_contable or "gasto"),
                )
            new_id = cur.lastrowid
            conn.commit()
            return {
                "ok": True,
                "id": new_id,
                "user_name": body.user_name,
                "fecha": body.fecha,
                "monto_cop": body.monto_cop,
                "monto_usd": monto_usd,
                "categoria": body.categoria,
                "nota": body.nota or "",
                "metodo_pago": body.metodo_pago or "Sin especificar",
                "clase_contable": body.clase_contable or "gasto",
            }
        finally:
            conn.close()

    @api.put("/api/expenses/{expense_id}")
    def update_expense(expense_id: int, update: ExpenseUpdate):
        """Update one or more fields of an expense. Fields not supplied are left alone.

        If monto_cop is updated without monto_usd, monto_usd is recomputed from
        the current TRM so both columns stay consistent.
        """
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            existing = conn.execute(
                "SELECT id FROM expenses WHERE id = ?", (expense_id,)
            ).fetchone()
            if not existing:
                raise HTTPException(404, f"expense {expense_id} not found")

            data = update.dict(exclude_unset=True)
            if not data:
                raise HTTPException(400, "no fields to update")

            # Validate fecha format if present (YYYY-MM-DD)
            if "fecha" in data and data["fecha"]:
                try:
                    datetime.strptime(data["fecha"], "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(400, f"invalid fecha {data['fecha']!r}, expected YYYY-MM-DD")

            # Validate clase_contable: only the canonical 4 values are
            # accepted. A malformed PUT shouldn't be able to insert
            # arbitrary strings into this column — it's used to drive
            # the dashboard's accounting view, garbage values would
            # silently disappear from the by_clase_contable rollup.
            if "clase_contable" in data and data["clase_contable"] is not None:
                if data["clase_contable"] not in CLASES_CONTABLES_PERMITIDAS:
                    raise HTTPException(
                        400,
                        f"invalid clase_contable {data['clase_contable']!r}, "
                        f"expected one of {sorted(CLASES_CONTABLES_PERMITIDAS)}",
                    )

            # Keep monto_usd in sync if monto_cop was edited alone
            if "monto_cop" in data and "monto_usd" not in data:
                try:
                    data["monto_usd"] = round(float(data["monto_cop"]) / bot.TRM, 2)
                except (TypeError, ValueError, ZeroDivisionError):
                    raise HTTPException(400, f"invalid monto_cop {data['monto_cop']!r}")

            sets = ", ".join(f"{k} = ?" for k in data)
            params = list(data.values()) + [expense_id]
            conn.execute(f"UPDATE expenses SET {sets} WHERE id = ?", params)
            conn.commit()

            row = conn.execute(
                "SELECT id, user_name, fecha, monto_cop, monto_usd, categoria, nota, "
                "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago, "
                "COALESCE(clase_contable, 'gasto') AS clase_contable "
                "FROM expenses WHERE id = ?",
                (expense_id,),
            ).fetchone()
            return {
                "id": row[0], "user_name": row[1], "fecha": row[2],
                "monto_cop": row[3], "monto_usd": row[4],
                "categoria": row[5], "nota": row[6], "metodo_pago": row[7],
                "clase_contable": row[8],
            }
        finally:
            conn.close()

    @api.delete("/api/expenses/{expense_id}")
    def delete_expense(expense_id: int):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"expense {expense_id} not found")
            return {"ok": True, "deleted": expense_id}
        finally:
            conn.close()

    @api.post("/api/expenses/{expense_id}/defer")
    def defer_expense(expense_id: int, body: DeferralRequest):
        """Difiere un gasto en N meses (Approach A: filas hijas).

        El gasto original se modifica:
          - monto_cop, monto_usd → divididos entre N
          - deferred_total = N
          - deferred_index = 1
          - deferred_group_id = id del original
          - deferred_mode = body.mode

        Y se crean N-1 hijos (cuotas 2..N), uno por cada mes futuro.
        Hereda toda la metadata del original (categoría, user, método,
        nota, clase_contable). La fecha se incrementa mes a mes
        manteniendo el día (ajustando si el mes destino es más corto).

        Retorna la lista de los N expenses (original + hijos).

        Errores:
          - 404: expense no encontrado
          - 400: months fuera de [2,12], mode inválido, o gasto ya está diferido
        """
        if body.months < 2 or body.months > 12:
            raise HTTPException(400, "months must be between 2 and 12")
        if body.mode not in DEFERRAL_MODES_PERMITIDOS:
            raise HTTPException(
                400,
                f"invalid mode {body.mode!r}, expected one of {sorted(DEFERRAL_MODES_PERMITIDOS)}",
            )
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, user_id, user_name, fecha, monto_cop, monto_usd, categoria, "
                "nota, metodo_pago, clase_contable, deferred_total, deferred_group_id "
                "FROM expenses WHERE id = ?",
                (expense_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"expense {expense_id} not found")
            (orig_id, user_id, user_name, fecha, monto_cop, monto_usd,
             categoria, nota, metodo_pago, clase_contable,
             current_total, current_group_id) = row

            # Block if already deferred (would corrupt accounting)
            if (current_total and current_total > 1) or current_group_id:
                raise HTTPException(
                    400,
                    f"expense {expense_id} ya está en un grupo diferido. "
                    "Borra el grupo entero (DELETE /api/expenses/group/{group_id}) "
                    "antes de re-diferirlo.",
                )

            n = body.months
            unit_cop = round(float(monto_cop or 0) / n, 2)
            unit_usd = round(float(monto_usd or 0) / n, 2)
            now = datetime.now().isoformat()

            # Compute monthly fechas. Fecha base = original (YYYY-MM-DD or
            # YYYY-MM-DD HH:MM). Mantenemos el día y solo movemos el mes.
            # Si el día origen es 31 y el mes destino tiene 30, ajustamos
            # al último día del mes destino.
            base = fecha or datetime.now().strftime("%Y-%m-%d")
            base_date = base[:10]
            try:
                y0, m0, d0 = (int(x) for x in base_date.split("-"))
            except (ValueError, TypeError):
                raise HTTPException(400, f"original expense has invalid fecha {base!r}")

            def month_offset(y, m, d, k):
                """Return YYYY-MM-DD shifted k months forward, clamping day."""
                tot = m - 1 + k
                ny, nm = y + tot // 12, tot % 12 + 1
                # Clamp day to last day of target month
                import calendar
                last = calendar.monthrange(ny, nm)[1]
                nd = min(d, last)
                return f"{ny:04d}-{nm:02d}-{nd:02d}"

            try:
                conn.execute("BEGIN")
                # Update original (cuota 1/N)
                conn.execute(
                    "UPDATE expenses SET monto_cop = ?, monto_usd = ?, "
                    "deferred_total = ?, deferred_index = 1, "
                    "deferred_group_id = ?, deferred_mode = ? "
                    "WHERE id = ?",
                    (unit_cop, unit_usd, n, orig_id, body.mode, orig_id),
                )
                # Insert N-1 hijos (cuotas 2..N)
                children_ids = []
                for k in range(2, n + 1):
                    new_fecha = month_offset(y0, m0, d0, k - 1)
                    cur = conn.execute(
                        "INSERT INTO expenses "
                        "(user_id, user_name, fecha, monto_cop, monto_usd, "
                        " categoria, nota, created_at, metodo_pago, clase_contable, "
                        " deferred_total, deferred_index, deferred_group_id, deferred_mode) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (user_id, user_name, new_fecha, unit_cop, unit_usd,
                         categoria, nota, now, metodo_pago, clase_contable,
                         n, k, orig_id, body.mode),
                    )
                    children_ids.append(cur.lastrowid)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise

            # Return all N rows of the deferred group
            all_rows = conn.execute(
                "SELECT id, fecha, monto_usd, monto_cop, deferred_index, deferred_total "
                "FROM expenses WHERE deferred_group_id = ? ORDER BY deferred_index",
                (orig_id,),
            ).fetchall()
            return {
                "ok": True,
                "group_id": orig_id,
                "months": n,
                "mode": body.mode,
                "unit_usd": unit_usd,
                "unit_cop": unit_cop,
                "expenses": [
                    {"id": r[0], "fecha": r[1], "monto_usd": r[2],
                     "monto_cop": r[3], "index": r[4], "total": r[5]}
                    for r in all_rows
                ],
            }
        finally:
            conn.close()

    @api.delete("/api/expenses/group/{group_id}")
    def delete_deferred_group(group_id: int):
        """Borra TODOS los expenses asociados a un deferred_group_id.

        Útil cuando Daniel se arrepiente del diferimiento o quiere
        re-diferir con otro N. Después puede re-registrar el gasto
        original a mano y volver a diferir.
        """
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE deferred_group_id = ?",
                (group_id,),
            ).fetchone()[0]
            if count == 0:
                raise HTTPException(404, f"no expenses found for group {group_id}")
            conn.execute("DELETE FROM expenses WHERE deferred_group_id = ?", (group_id,))
            conn.commit()
            return {"ok": True, "group_id": group_id, "deleted_count": count}
        finally:
            conn.close()

    # ──────────────── Reconciliación de extractos ────────────────
    # Daniel sube un JSON extraído con Claude chat de uno o varios
    # extractos. Cada movimiento se clasifica, se matchea contra los
    # expenses del bot, y queda en reconciliation_items con un status.
    # Daniel resuelve cada uno desde el dashboard.

    def _classify_item(descripcion: str, bank_prefix: str) -> str:
        """Clasifica un item de extracto en un tipo. Primer match gana."""
        d = (descripcion or "").lower()
        for tipo, pat in RECONCILE_PATTERNS:
            if re.search(pat, d):
                return tipo
        # Si tiene prefijo de TC, asume compra; si es ahorros sin patrón, "otro"
        if bank_prefix in ("BdB Mastercard", "BdB LATAM Visa Daniel",
                           "BdB LATAM Visa Maria", "BdB LATAM Visa Maria DOlores",
                           "BBVA TCBLACK", "Wio", "Falabella Mastercard"):
            return "compra_tc"
        return "otro"

    def _derive_bank_prefix(descripcion: str) -> str:
        """Extrae el prefijo del banco/cuenta del campo descripcion.

        Las descripciones del extracto vienen con formato
        '<Banco/Cuenta> - <Detalle del comercio>'. El prefijo es lo que
        está antes del primer ' - '. Si no hay separador, retorna ''.
        """
        if not descripcion:
            return ""
        if " - " in descripcion:
            return descripcion.split(" - ", 1)[0].strip()
        return ""

    def _normalize_desc_for_fingerprint(s: str) -> str:
        s = (s or "").lower().strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^a-z0-9 ]", "", s)
        return s[:100]

    def _make_fingerprint(bank: str, fecha: str, monto: float, desc: str) -> str:
        import hashlib
        key = f"{bank}|{fecha}|{round(float(monto), 2)}|{_normalize_desc_for_fingerprint(desc)}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    def _try_match_expense(conn, item_fecha: str, item_monto: float,
                           method_pago: str, used_expense_ids: set,
                           tol_days: int = 3, tol_cop: int = 2000,
                           tol_pct: float = 0.01) -> Optional[int]:
        """Encuentra el mejor expense del bot que matchea el item.

        Criterios:
          - método_pago == method_pago O 'Sin especificar' (recoge unbranded)
          - fecha dentro de ±tol_days
          - monto_cop dentro de ±max(tol_cop, monto*tol_pct)
        Retorna expense_id del mejor match o None.
        """
        from datetime import datetime, timedelta
        try:
            d_item = datetime.strptime(item_fecha[:10], "%Y-%m-%d")
        except Exception:
            return None
        d_from = (d_item - timedelta(days=tol_days)).strftime("%Y-%m-%d")
        d_to = (d_item + timedelta(days=tol_days)).strftime("%Y-%m-%d")
        tol_amount = max(tol_cop, abs(item_monto) * tol_pct)
        rows = conn.execute(
            "SELECT id, fecha, monto_cop, metodo_pago FROM expenses "
            "WHERE fecha >= ? AND fecha <= ? "
            "AND ABS(monto_cop - ?) <= ? "
            "AND (metodo_pago = ? OR metodo_pago = 'Sin especificar' OR metodo_pago IS NULL)",
            (d_from, d_to + " 23:59", item_monto, tol_amount, method_pago),
        ).fetchall()
        if not rows:
            return None
        # Score: preferir match exacto de método > misma fecha > monto exacto
        best = None
        best_score = -1
        for r in rows:
            eid, e_fecha, e_monto, e_method = r
            if eid in used_expense_ids:
                continue
            score = 0
            if e_method == method_pago:
                score += 100
            elif e_method in (None, "Sin especificar"):
                score += 50
            try:
                e_d = datetime.strptime(e_fecha[:10], "%Y-%m-%d")
                day_diff = abs((e_d - d_item).days)
                score += max(0, 30 - day_diff * 5)
            except Exception:
                pass
            amount_diff = abs(e_monto - item_monto)
            score += max(0, 50 - int(amount_diff / 100))
            if score > best_score:
                best_score = score
                best = eid
        return best

    @api.post("/api/reconcile/import")
    def reconcile_import(body: ReconcileImportRequest):
        """Recibe un JSON con N transacciones, las clasifica, intenta match
        contra expenses del bot, y guarda el import + items.

        Retorna el resumen del diff con counts por status y total por tipo.
        """
        if not body.transactions:
            raise HTTPException(400, "transactions vacío")

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            now = datetime.now().isoformat()
            # Compute period bounds from transactions
            fechas = [t.fecha[:10] for t in body.transactions if t.fecha]
            period_from = min(fechas) if fechas else None
            period_to = max(fechas) if fechas else None
            total_cop = sum(float(t.monto_cop or 0) for t in body.transactions)

            cur = conn.execute(
                "INSERT INTO reconciliation_imports "
                "(label, period_from, period_to, total_items, total_cop, status, imported_at, notes) "
                "VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                (body.label or f"Import {now[:10]}", period_from, period_to,
                 len(body.transactions), total_cop, now, body.notes or ""),
            )
            import_id = cur.lastrowid

            # Track which expenses ya matchearon, para no asignar el mismo dos veces
            used_expense_ids: set[int] = set()

            # Existing fingerprints across all imports — para idempotencia
            existing_fps = {row[0] for row in conn.execute(
                "SELECT fingerprint FROM reconciliation_items"
            ).fetchall()}

            stats = {"matched": 0, "unmatched_extract": 0, "bank_charge": 0,
                     "transfer_internal": 0, "duplicate": 0, "other": 0}
            type_totals: dict[str, dict] = {}

            for t in body.transactions:
                bank_prefix = _derive_bank_prefix(t.descripcion)
                item_type = _classify_item(t.descripcion, bank_prefix)
                fp = _make_fingerprint(bank_prefix, t.fecha[:10],
                                       float(t.monto_cop), t.descripcion)
                # Aggregate stats por tipo
                bucket = type_totals.setdefault(
                    item_type, {"count": 0, "total_cop": 0.0}
                )
                bucket["count"] += 1
                bucket["total_cop"] += float(t.monto_cop)

                if fp in existing_fps:
                    status = "duplicate"
                    matched_id = None
                    suggested_method = None
                    suggested_cat = None
                    stats["duplicate"] += 1
                else:
                    existing_fps.add(fp)
                    suggested_method = RECONCILE_PREFIX_TO_METHOD.get(bank_prefix)
                    suggested_cat = None
                    matched_id = None

                    if item_type in RECONCILE_BANK_CHARGE_TYPES:
                        status = "bank_charge"
                        suggested_cat = "comisiones"
                        stats["bank_charge"] += 1
                    elif item_type == "transferencia_saliente":
                        # Intentar match contra expenses con método 'Transferencia <BANK>'
                        if suggested_method:
                            matched_id = _try_match_expense(
                                conn, t.fecha[:10], float(t.monto_cop),
                                suggested_method, used_expense_ids,
                            )
                        if matched_id:
                            used_expense_ids.add(matched_id)
                            status = "matched"
                            stats["matched"] += 1
                        else:
                            status = "transfer_internal"
                            stats["transfer_internal"] += 1
                    elif item_type in ("compra_tc", "pago_pse"):
                        if suggested_method:
                            matched_id = _try_match_expense(
                                conn, t.fecha[:10], float(t.monto_cop),
                                suggested_method, used_expense_ids,
                            )
                        if matched_id:
                            used_expense_ids.add(matched_id)
                            status = "matched"
                            stats["matched"] += 1
                        else:
                            status = "unmatched_extract"
                            stats["unmatched_extract"] += 1
                    else:
                        status = "other"
                        stats["other"] += 1

                conn.execute(
                    "INSERT INTO reconciliation_items "
                    "(import_id, fecha, monto_cop, monto_original, moneda_original, "
                    " descripcion, bank_prefix, item_type, fingerprint, "
                    " matched_expense_id, status, suggested_method_pago, suggested_categoria) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (import_id, t.fecha[:10], float(t.monto_cop),
                     t.monto_original, t.moneda_original,
                     t.descripcion, bank_prefix, item_type, fp,
                     matched_id, status, suggested_method, suggested_cat),
                )
            conn.commit()
            return {
                "ok": True,
                "import_id": import_id,
                "period_from": period_from,
                "period_to": period_to,
                "total_items": len(body.transactions),
                "total_cop": total_cop,
                "stats": stats,
                "by_type": [
                    {"type": k, "count": v["count"],
                     "total_cop": round(v["total_cop"], 2)}
                    for k, v in sorted(type_totals.items(),
                                       key=lambda x: -x[1]["total_cop"])
                ],
            }
        finally:
            conn.close()

    @api.get("/api/reconcile/imports")
    def list_reconcile_imports():
        rows = _query_all(
            "SELECT id, label, period_from, period_to, total_items, total_cop, "
            "status, imported_at, notes FROM reconciliation_imports "
            "ORDER BY id DESC"
        )
        return {"imports": rows}

    @api.get("/api/reconcile/imports/{import_id}")
    def get_reconcile_import(import_id: int, status: Optional[str] = Query(None)):
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            imp = conn.execute(
                "SELECT * FROM reconciliation_imports WHERE id = ?", (import_id,),
            ).fetchone()
            if not imp:
                raise HTTPException(404, f"import {import_id} not found")
            q = "SELECT * FROM reconciliation_items WHERE import_id = ?"
            params: list = [import_id]
            if status:
                q += " AND status = ?"
                params.append(status)
            q += " ORDER BY fecha, id"
            items = [dict(r) for r in conn.execute(q, params).fetchall()]
            # Stats
            stats: dict = {}
            for r in conn.execute(
                "SELECT status, COUNT(*), SUM(monto_cop) FROM reconciliation_items "
                "WHERE import_id = ? GROUP BY status", (import_id,),
            ).fetchall():
                stats[r[0]] = {"count": r[1], "total_cop": round(r[2] or 0, 2)}
            return {"import": dict(imp), "items": items, "stats": stats}
        finally:
            conn.close()

    @api.patch("/api/reconcile/items/{item_id}")
    def update_reconcile_item(item_id: int, body: ReconcileItemUpdate):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            existing = conn.execute(
                "SELECT id FROM reconciliation_items WHERE id = ?", (item_id,),
            ).fetchone()
            if not existing:
                raise HTTPException(404, f"item {item_id} not found")
            data = body.dict(exclude_unset=True)
            if not data:
                raise HTTPException(400, "no fields to update")
            sets = ", ".join(f"{k} = ?" for k in data)
            params = list(data.values()) + [item_id]
            conn.execute(
                f"UPDATE reconciliation_items SET {sets} WHERE id = ?", params,
            )
            conn.commit()
            return {"ok": True, "id": item_id, **data}
        finally:
            conn.close()

    @api.post("/api/reconcile/items/{item_id}/create_expense")
    def reconcile_item_create_expense(item_id: int):
        """Crea un expense desde un item del extracto y lo deja matched."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, fecha, monto_cop, descripcion, bank_prefix, item_type, "
                "suggested_method_pago, suggested_categoria, status "
                "FROM reconciliation_items WHERE id = ?", (item_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"item {item_id} not found")
            (rid, fecha, monto_cop, descripcion, bank_prefix, item_type,
             sug_method, sug_cat, status) = row
            if status == "matched":
                raise HTTPException(409, "item ya está matched a un expense")
            metodo = sug_method or "Sin especificar"
            categoria = sug_cat or ("comisiones" if item_type in
                                    ("gmf", "comision", "cuota_manejo",
                                     "interes_tc", "seguro_deudor",
                                     "conversion_int", "foreign_exchange_fee",
                                     "avance_cajero")
                                    else "otro")
            monto_usd = round(float(monto_cop) / bot.TRM, 2) if bot.TRM else 0
            cur = conn.execute(
                "INSERT INTO expenses "
                "(user_id, user_name, fecha, monto_cop, monto_usd, categoria, "
                " nota, created_at, metodo_pago, clase_contable, "
                " deferred_total, deferred_index) "
                "VALUES (NULL, 'Daniel', ?, ?, ?, ?, ?, ?, ?, 'gasto', 1, 1)",
                (fecha, float(monto_cop), monto_usd, categoria,
                 descripcion[:120], datetime.now().isoformat(), metodo),
            )
            new_expense_id = cur.lastrowid
            conn.execute(
                "UPDATE reconciliation_items SET matched_expense_id = ?, status = 'added_to_bot' "
                "WHERE id = ?", (new_expense_id, item_id),
            )
            conn.commit()
            return {"ok": True, "expense_id": new_expense_id, "categoria": categoria,
                    "metodo_pago": metodo}
        finally:
            conn.close()

    @api.post("/api/reconcile/import/{import_id}/bulk_create_bank_charges")
    def bulk_create_bank_charges(import_id: int, body: ReconcileBulkCreateRequest):
        """Crea expenses de N items de cargo del banco en una sola transacción."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            placeholders = ",".join("?" * len(body.item_ids))
            rows = conn.execute(
                f"SELECT id, fecha, monto_cop, descripcion, bank_prefix, item_type, "
                f"suggested_method_pago, status FROM reconciliation_items "
                f"WHERE import_id = ? AND id IN ({placeholders})",
                [import_id] + list(body.item_ids),
            ).fetchall()
            created = []
            for row in rows:
                rid, fecha, monto_cop, descripcion, bank_prefix, item_type, sug_method, status = row
                if status == "added_to_bot" or status == "matched":
                    continue
                metodo = sug_method or "Sin especificar"
                monto_usd = round(float(monto_cop) / bot.TRM, 2) if bot.TRM else 0
                cur = conn.execute(
                    "INSERT INTO expenses "
                    "(user_id, user_name, fecha, monto_cop, monto_usd, categoria, "
                    " nota, created_at, metodo_pago, clase_contable, "
                    " deferred_total, deferred_index) "
                    "VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'gasto', 1, 1)",
                    (body.user_name or "Daniel", fecha, float(monto_cop), monto_usd,
                     body.categoria or "comisiones", descripcion[:120],
                     datetime.now().isoformat(), metodo),
                )
                new_id = cur.lastrowid
                conn.execute(
                    "UPDATE reconciliation_items SET matched_expense_id = ?, status = 'added_to_bot' "
                    "WHERE id = ?", (new_id, rid),
                )
                created.append({"item_id": rid, "expense_id": new_id})
            conn.commit()
            return {"ok": True, "created_count": len(created), "items": created}
        finally:
            conn.close()

    @api.delete("/api/reconcile/imports/{import_id}")
    def delete_reconcile_import(import_id: int):
        """Borra un import + sus items en cascada. NO toca los expenses
        que se hayan creado desde 'added_to_bot' (esos quedan en el bot)."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute(
                "SELECT COUNT(*) FROM reconciliation_imports WHERE id = ?", (import_id,)
            ).fetchone()
            if not r or r[0] == 0:
                raise HTTPException(404, f"import {import_id} not found")
            conn.execute("DELETE FROM reconciliation_items WHERE import_id = ?", (import_id,))
            conn.execute("DELETE FROM reconciliation_imports WHERE id = ?", (import_id,))
            conn.commit()
            return {"ok": True, "deleted_import_id": import_id}
        finally:
            conn.close()

    @api.get("/api/export/csv")
    def export_csv(month: Optional[str] = Query(None)):
        prefix = _month_prefix(month)
        rows = _query_all(
            "SELECT id, user_name, fecha, monto_cop, monto_usd, categoria, nota, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago, "
            "COALESCE(clase_contable, 'gasto') AS clase_contable "
            "FROM expenses WHERE fecha LIKE ? ORDER BY fecha DESC, id DESC",
            (f"{prefix}%",),
        )
        if not rows:
            raise HTTPException(404, f"no expenses for {prefix}")
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "ID", "Usuario", "Fecha", "Monto_COP", "Monto_USD",
            "Categoría", "Nota", "Método de Pago", "Clase Contable",
        ])
        for r in rows:
            writer.writerow([
                r["id"], r["user_name"], r["fecha"],
                r["monto_cop"], r["monto_usd"], r["categoria"],
                r["nota"] or "", r["metodo_pago"], r["clase_contable"],
            ])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="gastos_{prefix.replace("-", "_")}.csv"'
            },
        )

    # ──────────────── Static frontend ────────────────
    # Serve index.html at / and any other static assets under /static/*.
    # Keeping these AFTER the /api/* routes above ensures they don't shadow
    # the JSON API.
    index_file = STATIC_DIR / "index.html"

    @api.get("/", include_in_schema=False)
    def root():
        if not index_file.exists():
            raise HTTPException(500, f"index.html missing at {index_file}")
        return FileResponse(index_file)

    # /favicon.ico is served inline as a data URI in index.html, but browsers
    # still hit this path — return 204 so we don't spam logs with 404s.
    @api.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return JSONResponse(content=None, status_code=204)

    if STATIC_DIR.exists():
        api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return api
