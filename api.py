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

    Returns: { category_key: {usd, annual_usd, tipo, icon, label, parent,
                              is_override} }
    """
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
        }
    for cat, usd, annual in conn.execute(
        "SELECT category, usd, annual_usd FROM budget_history WHERE period = ?",
        (period,),
    ).fetchall():
        if cat in out:
            out[cat]["usd"] = float(usd or 0)
            if annual is not None:
                out[cat]["annual_usd"] = float(annual)
            out[cat]["is_override"] = True
        # If the override points at a category that no longer exists in baseline,
        # silently skip — user can clean it up via DELETE.
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
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago "
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
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago "
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
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago "
            "FROM expenses WHERE substr(fecha, 1, 7) BETWEEN ? AND ?",
            (pfrom, pto),
        )
        total_usd = sum(r["monto_usd"] for r in rows)
        total_cop = sum(r["monto_cop"] for r in rows)

        # Pull effective budgets for the window — this walks each month and
        # merges baseline with any per-month overrides in budget_history.
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            budget_totals, budget_was_dynamic = _sum_effective_budget_over_range(
                conn, pfrom, pto,
            )
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

        # Global budget limit: sum of all effective category budgets (what
        # was actually allocated across all cats for the window), so dynamic
        # month-to-month changes are reflected.
        effective_total_budget = sum(bt.get("usd_sum", 0) for bt in budget_totals.values())
        # Lifestyle cap (the $5000/mo reference), also reported for context.
        lifestyle_cap = bot.BUDGET_LIMIT_USD * n_months
        # Use the lifestyle cap as the gauge reference for continuity with
        # existing UI expectations.
        budget_limit = lifestyle_cap

        return {
            "month": pto,  # legacy field — points at the end of the window
            "from": pfrom,
            "to": pto,
            "n_months": n_months,
            "budget_was_dynamic": budget_was_dynamic,
            "monthly_budget_limit_usd": bot.BUDGET_LIMIT_USD,
            "budget_limit_usd": budget_limit,
            "effective_total_budget_usd": round(effective_total_budget, 2),
            "total_usd": round(total_usd, 2),
            "total_cop": round(total_cop, 0),
            "pct_of_budget": round(total_usd / budget_limit, 4) if budget_limit else 0,
            "available_usd": round(budget_limit - total_usd, 2),
            "count": len(rows),
            "categories": categories,
            "by_user": list(by_user.values()),
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
                },
                "expenses": {
                    "total_usd": expense_total,
                    "by_category": expense_by_cat,
                    "budget_limit_usd": budget_limit,
                    "monthly_budget_limit_usd": bot.BUDGET_LIMIT_USD,
                    "effective_total_budget_usd": round(effective_total_budget, 2),
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
                "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago "
                "FROM expenses WHERE id = ?",
                (expense_id,),
            ).fetchone()
            return {
                "id": row[0], "user_name": row[1], "fecha": row[2],
                "monto_cop": row[3], "monto_usd": row[4],
                "categoria": row[5], "nota": row[6], "metodo_pago": row[7],
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

    @api.get("/api/export/csv")
    def export_csv(month: Optional[str] = Query(None)):
        prefix = _month_prefix(month)
        rows = _query_all(
            "SELECT id, user_name, fecha, monto_cop, monto_usd, categoria, nota, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago "
            "FROM expenses WHERE fecha LIKE ? ORDER BY fecha DESC, id DESC",
            (f"{prefix}%",),
        )
        if not rows:
            raise HTTPException(404, f"no expenses for {prefix}")
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["ID", "Usuario", "Fecha", "Monto_COP", "Monto_USD", "Categoría", "Nota", "Método de Pago"])
        for r in rows:
            writer.writerow([
                r["id"], r["user_name"], r["fecha"],
                r["monto_cop"], r["monto_usd"], r["categoria"],
                r["nota"] or "", r["metodo_pago"],
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
