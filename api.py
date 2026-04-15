"""
REST API for the expense-bot dashboard.

Read-only endpoints in this first cut. Mutations (PUT/POST/DELETE) come in
Phase 3 once the frontend exists. The server is mounted into the same
asyncio event loop as the bot (see bot.main() → run_bot_and_api).

All endpoints return JSON. Paths are under /api/* so the future static
frontend can live at / and not collide.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse

# Importing bot gives us access to DB_PATH and the live config dicts
# (BUDGET, PAYMENT_METHODS, TRM, BOB_RATE, AED_RATE, BUDGET_LIMIT_USD) without
# re-reading the config table on every request. It also means bot.py is the
# single source of truth for schema and config — api.py is a thin wrapper.
import bot


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
    def get_summary(month: Optional[str] = Query(None)):
        """Month summary — totals, % of budget, per-category, per-user."""
        prefix = _month_prefix(month)
        rows = _query_all(
            "SELECT user_name, fecha, monto_cop, monto_usd, categoria, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago "
            "FROM expenses WHERE fecha LIKE ?",
            (f"{prefix}%",),
        )
        total_usd = sum(r["monto_usd"] for r in rows)
        total_cop = sum(r["monto_cop"] for r in rows)
        by_cat: dict[str, dict] = {}
        for r in rows:
            cat = r["categoria"]
            cat_info = bot.BUDGET.get(cat, {"usd": 0, "icon": "📦", "label": cat})
            slot = by_cat.setdefault(
                cat,
                {
                    "categoria": cat,
                    "icon": cat_info.get("icon", "📦"),
                    "label": cat_info.get("label", cat),
                    "budget_usd": cat_info.get("usd", 0),
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

        return {
            "month": prefix,
            "budget_limit_usd": bot.BUDGET_LIMIT_USD,
            "total_usd": round(total_usd, 2),
            "total_cop": round(total_cop, 0),
            "pct_of_budget": round(total_usd / bot.BUDGET_LIMIT_USD, 4) if bot.BUDGET_LIMIT_USD else 0,
            "available_usd": round(bot.BUDGET_LIMIT_USD - total_usd, 2),
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

    return api
