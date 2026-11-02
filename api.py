"""
REST API for the expense-bot dashboard.

Mounted into the same asyncio event loop as the bot (see bot.main() →
run_bot_and_api). Serves the static frontend at / and all JSON endpoints
under /api/*.

Acceso con PIN: si la variable DASHBOARD_PIN está puesta, todo pide sesión
(cookie firmada) salvo /api/health. Sin la variable el dashboard queda abierto
como antes. Ver la sección "🔐 Acceso con PIN" más abajo.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, Response, HTMLResponse
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


# ──────────────── 🔐 Acceso con PIN ────────────────
# - DASHBOARD_PIN (variable de Railway): si está vacía, el dashboard queda
#   abierto como antes; si está puesta, todo pide sesión salvo las rutas
#   abiertas de abajo.
# - Sesión = cookie httpOnly firmada con HMAC-SHA256. La clave mezcla
#   config.session_secret (migración 019) con el PIN, así que cambiar el PIN
#   cierra todas las sesiones abiertas.
# - "Recordar este dispositivo" = 180 días; si no, cookie de sesión del
#   navegador (y la firma vence en 1 día).
# - Fuerza bruta: 5 PIN errados por IP → bloqueo 15 min; 30 errados en total
#   en una hora → bloqueo general 1 h.
# - DASHBOARD_API_TOKEN (opcional): header X-Api-Token para scripts.
AUTH_COOKIE = "gg_session"
AUTH_REMEMBER_DAYS = 180
AUTH_IP_MAX_FAILS = 5
AUTH_IP_LOCK_SECONDS = 15 * 60
AUTH_GLOBAL_MAX_FAILS = 30
AUTH_GLOBAL_WINDOW_SECONDS = 60 * 60
_AUTH_OPEN_PATHS = {"/api/health", "/api/login", "/api/logout", "/api/auth/status", "/favicon.ico", "/static/tw.css"}
_auth_fails_by_ip: dict[str, list[float]] = {}
_auth_fails_global: list[float] = []
_auth_secret_cache: dict[str, bytes] = {}


class LoginBody(BaseModel):
    pin: str
    remember: Optional[bool] = True


class NaturalezaBody(BaseModel):
    naturaleza: str   # 'esencial' | 'estilo' | 'gusto'


class QuickExpense(BaseModel):
    """➕ Gasto rápido desde el dashboard (en cualquier moneda del bot)."""
    monto: float
    moneda: Optional[str] = "COP"
    categoria: str
    nota: Optional[str] = ""
    metodo_pago: Optional[str] = "Sin especificar"
    user_name: Optional[str] = None      # si no viene, la persona que entró con su PIN
    fecha: Optional[str] = None          # YYYY-MM-DD; si no viene, hoy (hora Colombia)


# Versión del recorrido guiado: al cambiarla, a todos les vuelve a salir.
TOUR_VERSION = "2026-11-resumen"  # cambiarla vuelve a mostrar el recorrido a todos


# ──────────────── 🔎 Revisión ────────────────
REVIEW_MAX_MONTHS = 36
REVIEW_MIN_MES = 20   # registros mínimos para que un mes cuente como inicio de los datos
_REVIEW_TAG_RE = re.compile(r"\s*·\s*conciliaci[oó]n.*$", re.IGNORECASE)


def _review_note_key(nota: str) -> str:
    """Agrupa notas parecidas ('Rappi McDonalds' y 'rappi mcdonalds ')."""
    n = _REVIEW_TAG_RE.sub("", (nota or "").strip())
    n = re.sub(r"\s+", " ", n).strip(" .,-").lower()
    return n[:48] or "(sin nota)"


def _review_prev_range(desde: str, hasta: str) -> tuple[str, str]:
    """El período anterior de la misma cantidad de meses."""
    months = list(_iter_months(desde, hasta))
    y, m = int(desde[:4]), int(desde[5:7])
    tot = (y * 12 + m - 1) - len(months)
    py, pm = divmod(tot, 12)
    p_from = f"{py:04d}-{pm + 1:02d}"
    ey, em = divmod(y * 12 + m - 2, 12)
    return p_from, f"{ey:04d}-{em + 1:02d}"


def _auth_pins() -> dict[str, str]:
    """PINes del dashboard desde DASHBOARD_PIN.

    Formato: un PIN por persona, "Daniel:123456,Mado:654321". También acepta
    un solo PIN sin nombre (queda como persona "dashboard"). Vacío = sin login.
    """
    raw = (os.environ.get("DASHBOARD_PIN") or "").strip()
    pins: dict[str, str] = {}
    for i, part in enumerate(p.strip() for p in raw.split(",")):
        if not part:
            continue
        name, pin = (part.split(":", 1) if ":" in part else ("dashboard", part))
        name = re.sub(r"[^A-Za-z0-9_-]", "", name.strip())[:20] or f"persona{i + 1}"
        if pin.strip():
            pins[name] = pin.strip()
    return pins


def _auth_enabled() -> bool:
    return bool(_auth_pins())


def _auth_api_token() -> str:
    return (os.environ.get("DASHBOARD_API_TOKEN") or "").strip()


def _auth_key(user: str, pin: str) -> bytes:
    """Clave de firma por persona: secreto de la base + persona + su PIN.
    Cambiar el PIN de alguien cierra solo sus sesiones."""
    ck = user + "\x00" + pin
    if ck not in _auth_secret_cache:
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute("SELECT value FROM config WHERE key = 'session_secret'").fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            raise RuntimeError("falta config.session_secret (migración 019)")
        if len(_auth_secret_cache) > 32:
            _auth_secret_cache.clear()
        _auth_secret_cache[ck] = hashlib.sha256((row[0] + "|" + user + "|" + pin).encode()).digest()
    return _auth_secret_cache[ck]


def _auth_make_token(user: str, days: int) -> str:
    pin = _auth_pins()[user]
    payload = f"v2.{int(time.time()) + days * 86400}.{secrets.token_hex(8)}.{user}"
    return payload + "." + hmac.new(_auth_key(user, pin), payload.encode(), hashlib.sha256).hexdigest()


def _auth_token_user(token: str) -> Optional[str]:
    """Persona dueña de la cookie si es válida y vigente; si no, None."""
    try:
        version, exp, nonce, user, sig = (token or "").split(".")
    except ValueError:
        return None
    pin = _auth_pins().get(user)
    if version != "v2" or not pin or not exp.isdigit() or int(exp) < time.time():
        return None
    good = hmac.new(_auth_key(user, pin), f"{version}.{exp}.{nonce}.{user}".encode(), hashlib.sha256).hexdigest()
    return user if hmac.compare_digest(sig, good) else None


def _auth_client_ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "?")


def _auth_is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def _auth_request_user(request: Request) -> Optional[str]:
    """Quién hace la petición: la persona de la cookie, 'api' si trae el
    token de scripts, o None si no tiene acceso."""
    api_tok = _auth_api_token()
    given = request.headers.get("x-api-token") or ""
    if api_tok and given and hmac.compare_digest(given, api_tok):
        return "api"
    return _auth_token_user(request.cookies.get(AUTH_COOKIE, ""))


LOGIN_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#f6f5f2">
<title>Gastos GG · Entrar</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💰</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Fraunces:opsz,wght@9..144,600&display=swap" rel="stylesheet">
<script>(function(){var t=null;try{t=localStorage.getItem('theme_mode')}catch(e){}var h=new Date().getHours();
if(t==='dark'||((!t||t==='auto')&&(h>=18||h<6)))document.documentElement.className='dark';})();</script>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1e1c19;--mute:#7e786e;--line:#e8e4dc;--field:#f3f1ec;--btn:#1e1c19;--btnink:#faf9f6;--err:#b0472c}
html.dark{--bg:#141311;--card:#1e1c19;--ink:#f3f1ec;--mute:#a9a297;--line:#2d2a26;--field:#2d2a26;--btn:#f3f1ec;--btnink:#1e1c19;--err:#e07a5f}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,sans-serif;padding:16px;-webkit-font-smoothing:antialiased}
form{width:100%;max-width:360px;background:var(--card);border-radius:24px;padding:32px 26px;
box-shadow:0 1px 2px rgba(30,28,25,.04),0 8px 24px rgba(30,28,25,.06)}
.logo{width:40px;height:40px;border-radius:50%;background:var(--btn);color:var(--btnink);display:grid;place-items:center;font-weight:600;font-size:14px;margin-bottom:18px}
h1{margin:0 0 4px;font-family:Fraunces,Georgia,serif;font-weight:600;font-size:30px;letter-spacing:-.02em}p{margin:0 0 22px;color:var(--mute);font-size:14px}
input[type=password]{width:100%;font-size:24px;letter-spacing:.35em;text-align:center;padding:14px;border:0;
border-radius:16px;background:var(--field);color:var(--ink)}
input[type=password]:focus{outline:2px solid #e5a54b;outline-offset:2px}
label{display:flex;gap:8px;align-items:center;margin:16px 0 20px;font-size:14px;color:var(--mute)}
input[type=checkbox]{accent-color:var(--btn);width:16px;height:16px}
button{width:100%;padding:13px;border:0;border-radius:999px;background:var(--btn);color:var(--btnink);font-size:15px;font-weight:600;cursor:pointer}
button:disabled{opacity:.5}.err{color:var(--err);font-size:13px;min-height:18px;margin-top:12px;text-align:center}
</style></head><body>
<form id="f" autocomplete="on">
<div class="logo">gg</div><h1>Gastos GG</h1><p>Escribe tu PIN para entrar.</p>
<input type="text" name="username" value="gastos-gg" autocomplete="username" hidden>
<input id="pin" type="password" name="password" inputmode="numeric" autocomplete="current-password" required autofocus aria-label="PIN">
<label><input id="rem" type="checkbox" checked> Recordar este dispositivo</label>
<button id="b" type="submit">Entrar</button>
<div class="err" id="e" role="alert"></div>
</form>
<script>
document.getElementById('f').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const b = document.getElementById('b'), e = document.getElementById('e');
  b.disabled = true; e.textContent = '';
  try {
    const r = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin: document.getElementById('pin').value, remember: document.getElementById('rem').checked})});
    if (r.ok) { location.replace('/' + location.hash); return; }
    const d = await r.json().catch(() => ({}));
    e.textContent = d.detail || ('No se pudo entrar (' + r.status + ')');
    document.getElementById('pin').select();
  } catch (err) { e.textContent = 'Sin conexión. Intenta de nuevo.'; }
  b.disabled = false;
});
</script>
</body></html>"""


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


# ──────────────── 📅 Diferidos: gestión por grupo ────────────────
# Módulo al lado de 🔁 Fijos. Cada diferido es un grupo de cuotas (filas de
# expenses con el mismo deferred_group_id) + una fila opcional en
# deferred_meta (concepto y moneda). Se puede crear directo (sin gasto
# previo), cambiar concepto/categoría/método/persona/modo de todas las
# cuotas, cambiar el valor de la cuota (todas o desde la cuota K), el
# número de cuotas, el mes de la primera cuota, y cancelar las pendientes.
DEFERRED_CURRENCIES = {"COP", "USD"}
DEFERRED_MAX_CUOTAS = 60


class DeferredCreate(BaseModel):
    concepto: str
    categoria: str
    monto: float
    monto_tipo: Optional[str] = "total"   # 'total' (se reparte en N) | 'cuota' (valor de cada cuota)
    currency: Optional[str] = "COP"       # 'COP' | 'USD'
    cuotas: int                           # 2..60
    fecha_inicio: str                     # YYYY-MM-DD de la cuota 1
    metodo_pago: Optional[str] = "Sin especificar"
    user_name: Optional[str] = "Daniel"
    mode: Optional[str] = "credito"
    clase_contable: Optional[str] = "gasto"


class DeferredUpdate(BaseModel):
    concepto: Optional[str] = None
    categoria: Optional[str] = None
    metodo_pago: Optional[str] = None
    user_name: Optional[str] = None
    mode: Optional[str] = None
    cuota: Optional[float] = None         # nuevo valor de cuota, en la moneda del grupo
    currency: Optional[str] = None        # cambiar moneda (exige cuota)
    desde: Optional[int] = None           # la nueva cuota aplica desde la cuota K (default 1 = todas)
    cuotas: Optional[int] = None          # nuevo número total de cuotas
    fecha_inicio: Optional[str] = None    # YYYY-MM-DD: re-fecha todas las cuotas desde aquí


def _month_offset(fecha: str, k: int) -> str:
    """YYYY-MM-DD movido k meses (k puede ser negativo), ajustando el día
    al último del mes destino si hace falta (31-ene + 1 → 28/29-feb)."""
    import calendar
    y, m, d = (int(x) for x in fecha[:10].split("-"))
    tot = m - 1 + k
    ny, nm = y + tot // 12, tot % 12 + 1
    return f"{ny:04d}-{nm:02d}-{min(d, calendar.monthrange(ny, nm)[1]):02d}"


def _split_cuotas(total: float, n: int, currency: str) -> list[float]:
    """Reparte un total en n cuotas: COP en pesos enteros, USD en centavos.
    El residuo del redondeo va en la última cuota para que la suma cuadre."""
    q = 0 if currency == "COP" else 2
    base = round(total / n, q)
    cuotas = [base] * n
    cuotas[-1] = round(total - base * (n - 1), q)
    return cuotas


def _amounts_for(conn, fecha: str, cuota: float, currency: str) -> tuple[float, float]:
    """(monto_cop, monto_usd) de una cuota con la TRM del mes de su fecha."""
    trm = bot._trm_for_period(conn, fecha[:7])
    if currency == "USD":
        return float(round(cuota * trm)), round(cuota, 2)
    return float(cuota), round(cuota / trm, 2)


def _deferred_today() -> str:
    return datetime.now(bot.COL_TZ).strftime("%Y-%m-%d")


_DEFERRED_COLS = ("id, fecha, monto_cop, monto_usd, categoria, nota, user_name, metodo_pago, "
                  "clase_contable, COALESCE(deferred_index, 1), COALESCE(deferred_total, 1), "
                  "deferred_group_id, deferred_mode")


def _deferred_rows(conn, gid: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_DEFERRED_COLS} FROM expenses WHERE COALESCE(deferred_group_id, id) = ? "
        "AND (deferred_group_id IS NOT NULL OR COALESCE(deferred_total, 1) > 1) "
        "ORDER BY COALESCE(deferred_index, 1), fecha, id",
        (gid,),
    ).fetchall()
    keys = ["id", "fecha", "monto_cop", "monto_usd", "categoria", "nota", "user_name", "metodo_pago",
            "clase_contable", "index", "total", "group_id", "mode"]
    return [dict(zip(keys, r)) for r in rows]


def _deferred_summary(rows: list[dict], meta: Optional[tuple], today: str) -> dict:
    """Arma la vista de un grupo a partir de sus cuotas (ya ordenadas)."""
    gid = rows[0]["group_id"] or rows[0]["id"]
    currency = (meta[1] if meta and meta[1] else "COP")
    concepto = (meta[0] if meta and meta[0] else "") or next(
        (r["nota"].strip() for r in rows if (r["nota"] or "").strip()), "") or rows[0]["categoria"]
    key = "monto_usd" if currency == "USD" else "monto_cop"
    pend = [r for r in rows if r["fecha"][:10] > today]
    pag = [r for r in rows if r["fecha"][:10] <= today]
    ref = (pend[0] if pend else rows[-1])
    n = max(max(r["total"] for r in rows), len(rows))
    return {
        "group_id": gid,
        "concepto": concepto,
        "currency": currency,
        "categoria": rows[0]["categoria"],
        "metodo_pago": rows[0]["metodo_pago"],
        "user_name": rows[0]["user_name"],
        "mode": rows[0]["mode"],
        "cuotas_total": n,
        "cuotas_registradas": len(rows),
        "incompleto": len(rows) != n,
        "pagadas": len(pag),
        "pendientes": len(pend),
        "cuota": round(ref[key] or 0, 2),
        "total": round(sum(r[key] or 0 for r in rows), 2),
        "total_cop": round(sum(r["monto_cop"] or 0 for r in rows)),
        "saldo": round(sum(r[key] or 0 for r in pend), 2),
        "saldo_cop": round(sum(r["monto_cop"] or 0 for r in pend)),
        "desde": rows[0]["fecha"][:10],
        "hasta": rows[-1]["fecha"][:10],
        "estado": "activo" if pend else "terminado",
        "cuotas": [{"id": r["id"], "index": r["index"], "fecha": r["fecha"][:10],
                    "monto_cop": r["monto_cop"], "monto_usd": r["monto_usd"],
                    "pagada": r["fecha"][:10] <= today} for r in rows],
    }


# ──────────────── Reconciliación de extractos bancarios v2 ────────────────
# Daniel sube un JSON extraído con Claude chat de uno o varios extractos
# bancarios consolidados. El backend clasifica cada movimiento, lo matchea
# contra los expenses del bot (cross-check bidireccional), y devuelve el
# diff. Daniel reconcilia en la UI con flujo guiado de 4 pasos: importar
# preview → procesar items → resolver huérfanos → cerrar import.
#
# Plan formal: ~/.claude/plans/wiggly-swinging-rose.md
# Tablas: reconciliation_imports, reconciliation_items,
#         reconciliation_audit_log, reconcile_keyword_rules (migración 011).
# Endpoints: implementados incrementalmente en commits B, C, E.
# Este bloque (commit A) define solo CONSTANTES + HELPERS internos.

# Mapping prefijo del extracto bancario → método_pago en el bot.
# El prefijo es el primer token de la descripción del extracto (antes del " - ").
# Si el banco no está en este mapping, suggested_method_pago queda None y
# el item queda sin sugerencia; el usuario debe adjudicarlo manualmente.
RECONCILE_PREFIX_TO_METHOD = {
    "BdB Mastercard":         "BDB MC Dani",
    "BdB LATAM Visa Daniel":  "BDB Visa Latam Dani",
    "BdB LATAM Visa Maria":   "BDB Visa Latam Mado",
    "BdB LATAM Visa Maria DOlores": "BDB Visa Latam Mado",
    "BBVA TCBLACK":           "BBVA MC Dani",
    "Wio":                    "WIO MC Dani",
    "BdB Ahorros":            "Transferencia BDB",
    "BBVA Ahorros":           "Transferencia BBVA",
    "Falabella":              "Falabella Mastercard Dani",
    "Bancolombia":            "Bancolombia MC Dani",
}

# Patrones para inferir item_type desde la descripción. Orden importa
# (primer match gana). Si nada matchea Y el bank_prefix es de TC,
# se asume 'compra_tc'. Si es de cuenta de ahorros, se asume 'otro'.
RECONCILE_TYPE_PATTERNS = [
    ("gmf",                    r"gravamen|4\s*x\s*1[\.,]?\s*000|impuesto.*4x1"),
    ("conversion_int",         r"conversion\s+compra\s+internacional"),
    ("comision",               r"comision\s+(transferencia|ach|interbancaria|por\s+internet)|iva\s+comision"),
    ("cuota_manejo",           r"cuota\s+de\s+manejo"),
    ("interes_tc",             r"intereses\s+corrientes|intereses\s+del\s+periodo"),
    ("seguro_deudor",          r"seg\s+deud|seguro\s+deud"),
    ("avance_cajero",          r"avance\s+cajero|avance\s+en\s+cajero|comision\s+avance"),
    ("foreign_exchange_fee",   r"foreign\s+exchange\s+fee"),
    # CAR DOMI TRA y BRE-B son transferencias salientes — Daniel confirmó
    # que CAR DOMI TRA1007015365 son transferencias interbancarias BBVA.
    ("transferencia_saliente", r"envio\s+por\s+bre|envio\s+a\s+|cargo\s+transferencia\s+ach|cargo\s+transferencia\s+canal|car\s+domi\s+tra|\bbre-?b\b"),
    ("pago_pse",               r"pago\s+por\s+pse|pago\s+con\s+bre"),
]

# Tipos clasificados como "cargos del banco" — auto-categorizables como
# 'comisiones', no son compras del usuario.
RECONCILE_BANK_CHARGE_TYPES = {
    "gmf", "conversion_int", "comision", "cuota_manejo",
    "interes_tc", "seguro_deudor", "avance_cajero", "foreign_exchange_fee",
}


def _derive_bank_prefix(descripcion: str) -> str:
    """Extrae el prefijo del banco/cuenta del campo descripcion.

    Las descripciones del extracto vienen con formato
    '<Banco/Cuenta> - <Detalle>'. Retorna lo que está antes del primer ' - '.
    Si no hay separador, retorna ''.
    """
    if not descripcion:
        return ""
    if " - " in descripcion:
        return descripcion.split(" - ", 1)[0].strip()
    return ""


def _classify_item_type(descripcion: str, bank_prefix: str) -> str:
    """Determina item_type desde la descripción + bank_prefix.

    Orden de evaluación:
      1. Patrones de RECONCILE_TYPE_PATTERNS (regex match en descripción).
      2. Si nada matchea Y bank_prefix es de TC → 'compra_tc'.
      3. Si nada matchea Y bank_prefix es de ahorros → 'otro'.
    """
    d = (descripcion or "").lower()
    for tipo, pat in RECONCILE_TYPE_PATTERNS:
        if re.search(pat, d):
            return tipo
    tc_prefixes = {"BdB Mastercard", "BdB LATAM Visa Daniel",
                   "BdB LATAM Visa Maria", "BdB LATAM Visa Maria DOlores",
                   "BBVA TCBLACK", "Wio", "Falabella", "Bancolombia"}
    if bank_prefix in tc_prefixes:
        return "compra_tc"
    return "otro"


def _normalize_desc_for_fingerprint(s: str) -> str:
    """Normaliza descripción para fingerprint determinístico."""
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s[:100]


def _make_fingerprint(bank: str, fecha: str, monto: float, desc: str) -> str:
    """Hash único para idempotencia de imports re-subidos."""
    import hashlib
    key = f"{bank}|{fecha}|{round(float(monto), 2)}|{_normalize_desc_for_fingerprint(desc)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _suggest_category_from_db(conn, descripcion: str) -> Optional[str]:
    """Aplica las heurísticas keyword guardadas en reconcile_keyword_rules
    sobre la descripción. Retorna la categoría sugerida del primer match
    (orden por priority ascendente). None si nada matchea.

    A diferencia del catálogo hardcoded del v1, esta versión consulta la
    DB en cada call — Daniel puede agregar/editar reglas desde el
    dashboard sin redeploy.
    """
    if not descripcion:
        return None
    d = descripcion.lower()
    rows = conn.execute(
        "SELECT pattern, suggested_categoria FROM reconcile_keyword_rules "
        "WHERE enabled = 1 ORDER BY priority ASC, id ASC"
    ).fetchall()
    for pat, cat in rows:
        try:
            if re.search(pat, d):
                return cat
        except re.error:
            # Pattern inválido en DB — skip silently. UI debería validar
            # con /rules/test antes de guardar.
            continue
    return None


def _try_match_expense(conn, item_fecha: str, item_monto: float,
                       method_pago: str, used_expense_ids: set,
                       tol_days: int = 5, tol_cop: int = 5000,
                       tol_pct: float = 0.05,
                       tol_days_back: Optional[int] = None,
                       tol_days_forward: Optional[int] = None) -> Optional[int]:
    """Busca el mejor expense del bot que matchea el item del extracto.

    Criterios:
      - método_pago == method_pago O 'Sin especificar' (recoge unbranded).
      - fecha en [item_fecha - tol_days_back, item_fecha + tol_days_forward]
        (default: simétrico ±tol_days).
      - monto_cop dentro de ±max(tol_cop, monto * tol_pct).
    Retorna expense_id del mejor match o None. Si encuentra varios, elige
    el de menor diff (fecha + monto). No devuelve expenses ya en used_expense_ids.
    """
    from datetime import datetime, timedelta
    try:
        d_item = datetime.strptime(item_fecha[:10], "%Y-%m-%d")
    except Exception:
        return None
    days_back = tol_days_back if tol_days_back is not None else tol_days
    days_forward = tol_days_forward if tol_days_forward is not None else tol_days
    d_from = (d_item - timedelta(days=days_back)).strftime("%Y-%m-%d")
    d_to = (d_item + timedelta(days=days_forward)).strftime("%Y-%m-%d")
    tol_amount = max(tol_cop, abs(item_monto) * tol_pct)
    rows = conn.execute(
        "SELECT id, fecha, monto_cop, metodo_pago FROM expenses "
        "WHERE substr(fecha, 1, 10) >= ? AND substr(fecha, 1, 10) <= ? "
        "AND ABS(monto_cop - ?) <= ? "
        "AND (metodo_pago = ? OR metodo_pago = 'Sin especificar' OR metodo_pago IS NULL)",
        (d_from, d_to, item_monto, tol_amount, method_pago),
    ).fetchall()
    if not rows:
        return None
    # Score: preferir match exacto de método > misma fecha > monto exacto
    best, best_score = None, -1
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


class ReconcileTxnIn(BaseModel):
    fecha: str
    monto_cop: float
    descripcion: str
    moneda_original: Optional[str] = None
    monto_original: Optional[float] = None


class ReconcilePreviewRequest(BaseModel):
    label: Optional[str] = None
    cycle_label: Optional[str] = None
    period_from: Optional[str] = None
    period_to: Optional[str] = None
    covered_methods: Optional[list[str]] = None
    tol_days_back: Optional[int] = 5
    tol_days_forward: Optional[int] = 5
    tol_pct: Optional[float] = 0.05
    notes: Optional[str] = None
    transactions: list[ReconcileTxnIn]


class ReconcileItemUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    matched_expense_id: Optional[int] = None


class ReconcileAdjudicateBody(BaseModel):
    categoria: str
    metodo_pago: Optional[str] = None
    user_name: Optional[str] = None
    nota: Optional[str] = None
    clase_contable: Optional[str] = None


class ReconcileRelinkBody(BaseModel):
    expense_id: int


class ReconcileMarkBody(BaseModel):
    action: str  # 'reviewed' or 'ignored'


class ReconcileBulkCreateBody(BaseModel):
    item_ids: list[int]
    metodo_override: Optional[str] = None
    user_name: Optional[str] = None


class ReconcileFindCandidatesBody(BaseModel):
    tol_days: Optional[int] = 10
    tol_pct: Optional[float] = 0.10


class ReconcileRuleBody(BaseModel):
    pattern: Optional[str] = None
    suggested_categoria: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    notes: Optional[str] = None


class ReconcileRuleTestBody(BaseModel):
    pattern: str
    sample_descriptions: list[str]


class RatesUpdate(BaseModel):
    TRM: float
    BOB_RATE: float
    AED_RATE: float
    # Opcionales por compat: PUTs antiguos sin estos campos siguen funcionando;
    # si vienen None, el endpoint conserva el valor actual.
    CLP_RATE: Optional[float] = None
    ARS_RATE: Optional[float] = None


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
    CLP_RATE: Optional[float] = None
    ARS_RATE: Optional[float] = None
    # Divisas activas ese mes (códigos: COP/BOB/AED/CLP/ARS). None = no cambiar.
    active_currencies: Optional[list] = None


class RecurringCreate(BaseModel):
    label: str
    monto_cop: Optional[float] = None
    categoria: str
    metodo_pago: Optional[str] = "Transferencia BBVA"
    user_name: Optional[str] = "Daniel"
    active: Optional[int] = 1
    # 'COP' → monto_cop fijo. 'USD' → monto_usd fijo (ej. Claude $100); el
    # monto_cop del template es solo referencia (usd × TRM).
    currency: Optional[str] = "COP"
    monto_usd: Optional[float] = None


class RecurringUpdate(BaseModel):
    label: Optional[str] = None
    monto_cop: Optional[float] = None
    categoria: Optional[str] = None
    metodo_pago: Optional[str] = None
    user_name: Optional[str] = None
    active: Optional[int] = None
    currency: Optional[str] = None
    monto_usd: Optional[float] = None


_REMINDER_KINDS = ("tarjeta", "credito", "hipoteca", "otro")
_REMINDER_CURRENCIES = ("COP", "USD", "AED")


class ReminderCreate(BaseModel):
    label: str
    kind: Optional[str] = "tarjeta"
    due_day: Optional[int] = None      # día del mes 1–31 (None = sin fecha aún)
    amount: Optional[float] = None
    currency: Optional[str] = "COP"
    notes: Optional[str] = None


class ReminderUpdate(BaseModel):
    label: Optional[str] = None
    kind: Optional[str] = None
    due_day: Optional[int] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    notes: Optional[str] = None
    active: Optional[int] = None
    # Para poder BORRAR la fecha o el valor (None no se distingue de "no enviado")
    clear_due_day: Optional[bool] = False
    clear_amount: Optional[bool] = False


class ReminderPaid(BaseModel):
    period: str          # YYYY-MM del vencimiento que se marca
    paid: bool = True


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

    # ──────────────── 🔐 Acceso con PIN ────────────────
    @api.middleware("http")
    async def _require_pin(request: Request, call_next):
        path = request.url.path
        if not _auth_enabled() or path in _AUTH_OPEN_PATHS or _auth_request_user(request):
            return await call_next(request)
        if request.method == "GET" and path in ("/", "/index.html"):
            return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store"})
        # Clear-Site-Data "cache": si el 401 viene de una copia vieja del
        # dashboard guardada en el navegador (de antes del PIN), se borra esa
        # caché y la próxima vez que abra el enlace llega la pantalla del PIN.
        # Solo borra caché HTTP, no cookies ni datos.
        return JSONResponse({"detail": "auth_required"}, status_code=401,
                            headers={"Cache-Control": "no-store", "Clear-Site-Data": '"cache"'})

    @api.get("/api/auth/status")
    def auth_status(request: Request):
        if not _auth_enabled():
            return {"enabled": False, "logged_in": True, "user": None}
        user = _auth_request_user(request)
        return {"enabled": True, "logged_in": bool(user), "user": user}

    @api.post("/api/login")
    def login(body: LoginBody, request: Request):
        pins = _auth_pins()
        if not pins:
            return {"ok": True, "auth": "disabled"}
        now = time.time()
        ip = _auth_client_ip(request)
        fails = [t for t in _auth_fails_by_ip.get(ip, []) if now - t < AUTH_IP_LOCK_SECONDS]
        _auth_fails_global[:] = [t for t in _auth_fails_global if now - t < AUTH_GLOBAL_WINDOW_SECONDS]
        if len(fails) >= AUTH_IP_MAX_FAILS or len(_auth_fails_global) >= AUTH_GLOBAL_MAX_FAILS:
            raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")
        given = (body.pin or "").strip().encode()
        user = None
        for name, pin in pins.items():   # se comparan todos para no filtrar tiempos
            if hmac.compare_digest(given, pin.encode()) and user is None:
                user = name
        if not user:
            fails.append(now)
            _auth_fails_by_ip[ip] = fails
            _auth_fails_global.append(now)
            print(f"[auth] PIN incorrecto desde {ip} ({len(fails)}/{AUTH_IP_MAX_FAILS})")
            raise HTTPException(401, "PIN incorrecto")
        _auth_fails_by_ip.pop(ip, None)
        print(f"[auth] entró {user} desde {ip}")
        remember = body.remember is not False
        resp = JSONResponse({"ok": True, "user": user})
        resp.set_cookie(
            AUTH_COOKIE, _auth_make_token(user, AUTH_REMEMBER_DAYS if remember else 1),
            max_age=AUTH_REMEMBER_DAYS * 86400 if remember else None,
            httponly=True, secure=_auth_is_https(request), samesite="lax", path="/",
        )
        return resp

    @api.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(AUTH_COOKIE, path="/")
        return resp

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
            "clp_rate": bot.CLP_RATE,
            "ars_rate": bot.ARS_RATE,
        }

    @api.get("/api/budget")
    def get_budget():
        """The full BUDGET dict as currently loaded in memory."""
        total = sum(v.get("usd", 0) for v in bot.BUDGET.values())
        return {
            "budget_limit_usd": bot.BUDGET_LIMIT_USD,
            "total_assigned_usd": total,
            "categories": bot.BUDGET,
            # Tipo efectivo de cada categoría (guardado, por defecto o heredado del
            # padre). Aparte de `categories` para que el editor no lo guarde de vuelta.
            "naturalezas": {k: bot.naturaleza_de(k) for k in bot.BUDGET},
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
            "CLP_RATE": bot.CLP_RATE,
            "ARS_RATE": bot.ARS_RATE,
        }

    @api.get("/api/expenses")
    def get_expenses(
        month: Optional[str] = Query(None, description="YYYY-MM. Defaults to current month."),
        frm: Optional[str] = Query(None, alias="from", description="YYYY-MM (rango, con `to`)"),
        to: Optional[str] = Query(None, description="YYYY-MM (rango, con `from`)"),
        limit: int = Query(1000, ge=1, le=10000),
    ):
        cols = ("SELECT id, user_id, user_name, fecha, monto_cop, monto_usd, "
                "categoria, nota, created_at, "
                "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago, "
                "COALESCE(clase_contable, 'gasto') AS clase_contable, "
                "COALESCE(deferred_total, 1) AS deferred_total, "
                "COALESCE(deferred_index, 1) AS deferred_index, "
                "deferred_group_id, deferred_mode, recurring_id "
                "FROM expenses ")
        if frm or to:
            # Rango de meses (Resumen/Movimientos con 3, 6, 12 meses o el año)
            for v in (frm, to):
                if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v or ""):
                    raise HTTPException(400, "from y to deben ser YYYY-MM")
            if frm > to:
                raise HTTPException(400, "from debe ser ≤ to")
            rows = _query_all(cols + "WHERE substr(fecha, 1, 7) BETWEEN ? AND ? "
                              "ORDER BY fecha DESC, id DESC LIMIT ?", (frm, to, limit))
            return {"from": frm, "to": to, "count": len(rows), "expenses": rows}
        prefix = _month_prefix(month)
        rows = _query_all(cols + "WHERE fecha LIKE ? ORDER BY fecha DESC, id DESC LIMIT ?",
                          (f"{prefix}%", limit))
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
    _SUPPORTED_CURRENCIES = ("COP", "USD", "BOB", "AED", "CLP", "ARS")
    # Divisas que Daniel opera SIEMPRE (mensual automático) vs opcionales que
    # activa por mes. USD es la base (=1, no lleva tasa).
    _ALWAYS_ACTIVE_CURRENCIES = ["COP", "AED"]
    _OPTIONAL_CURRENCIES = ["BOB", "CLP", "ARS"]
    # Métodos de pago que NO cobran en COP: su monto_cop es un valor derivado y
    # su USD no depende de la TRM, así que NO se recalculan al editar la TRM del
    # mes. El resto (BDB/BBVA/Bancolombia/Falabella/Efectivo/Transferencias
    # locales/Sin especificar) sí cobra en COP → monto_usd = monto_cop / TRM.
    _NON_COP_METHOD_HINTS = ("WIO", "ENBD", "USDT", "CHASE")

    def _active_currencies_for(raw) -> list:
        """Parsea el JSON de active_currencies; si viene vacío/None devuelve el
        set 'siempre' (COP/AED). Garantiza que COP/AED siempre estén incluidas."""
        act = []
        if raw:
            try:
                act = [c for c in json.loads(raw) if c in _SUPPORTED_CURRENCIES]
            except Exception:
                act = []
        merged = list(dict.fromkeys(_ALWAYS_ACTIVE_CURRENCIES + act))
        return merged

    def _rates_for_period(conn, period: str) -> dict:
        """Return the rates applicable to a given YYYY-MM period.

        Prefers a row in exchange_rates_history; falls back to the current
        globals (bot.TRM/BOB_RATE/AED_RATE/CLP_RATE/ARS_RATE) if no historical
        row exists. clp_rate/ars_rate pueden ser NULL en meses viejos (las
        columnas se agregaron en migración 012) → COALESCE al global.
        Read-only — does NOT write a history row automatically.
        """
        row = conn.execute(
            "SELECT trm, bob_rate, aed_rate, "
            "COALESCE(clp_rate, ?), COALESCE(ars_rate, ?) "
            "FROM exchange_rates_history WHERE period = ?",
            (bot.CLP_RATE, bot.ARS_RATE, period),
        ).fetchone()
        if row:
            return {"TRM": row[0], "BOB_RATE": row[1], "AED_RATE": row[2],
                    "CLP_RATE": row[3], "ARS_RATE": row[4]}
        return {"TRM": bot.TRM, "BOB_RATE": bot.BOB_RATE, "AED_RATE": bot.AED_RATE,
                "CLP_RATE": bot.CLP_RATE, "ARS_RATE": bot.ARS_RATE}

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
        if currency == "CLP":
            rate = float(rates.get("CLP_RATE") or 1)
            return round(float(monto) / rate, 2), rate
        if currency == "ARS":
            rate = float(rates.get("ARS_RATE") or 1)
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
                "SELECT period, trm, bob_rate, aed_rate, "
                "COALESCE(clp_rate, ?), COALESCE(ars_rate, ?), updated_at "
                "FROM exchange_rates_history ORDER BY period DESC",
                (bot.CLP_RATE, bot.ARS_RATE),
            ).fetchall()
            return {
                "history": [
                    {"period": r[0], "TRM": r[1], "BOB_RATE": r[2],
                     "AED_RATE": r[3], "CLP_RATE": r[4], "ARS_RATE": r[5],
                     "updated_at": r[6]}
                    for r in rows
                ],
                "current_globals": {
                    "TRM": bot.TRM, "BOB_RATE": bot.BOB_RATE, "AED_RATE": bot.AED_RATE,
                    "CLP_RATE": bot.CLP_RATE, "ARS_RATE": bot.ARS_RATE,
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
            row = conn.execute(
                "SELECT active_currencies FROM exchange_rates_history WHERE period = ?", (period,)
            ).fetchone()
            has_row = row is not None
            active = _active_currencies_for(row[0] if row else None)
            return {"period": period, "rates": rates, "is_historical": has_row,
                    "active_currencies": active}
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
            # CLP/ARS son opcionales en el body (compat). Si no vienen, conservar
            # lo que ya tenga el período, o el global si el período no existe.
            existing = conn.execute(
                "SELECT COALESCE(clp_rate, ?), COALESCE(ars_rate, ?) "
                "FROM exchange_rates_history WHERE period = ?",
                (bot.CLP_RATE, bot.ARS_RATE, period),
            ).fetchone()
            cur_clp = existing[0] if existing else bot.CLP_RATE
            cur_ars = existing[1] if existing else bot.ARS_RATE
            clp = float(rates.CLP_RATE) if rates.CLP_RATE is not None else float(cur_clp)
            ars = float(rates.ARS_RATE) if rates.ARS_RATE is not None else float(cur_ars)
            if clp <= 0 or ars <= 0:
                raise HTTPException(400, "las tasas CLP/ARS deben ser positivas")
            # active_currencies: si viene en el body úsalo, si no conserva lo del
            # período (o el set 'siempre' por defecto). COP/AED siempre incluidas.
            existing_ac = conn.execute(
                "SELECT active_currencies FROM exchange_rates_history WHERE period = ?", (period,)
            ).fetchone()
            if rates.active_currencies is not None:
                ac_list = _active_currencies_for(
                    json.dumps([str(c).upper() for c in rates.active_currencies]))
            else:
                ac_list = _active_currencies_for(existing_ac[0] if existing_ac else None)
            ac_json = json.dumps(ac_list)
            conn.execute(
                "INSERT INTO exchange_rates_history (period, trm, bob_rate, aed_rate, clp_rate, ars_rate, active_currencies, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(period) DO UPDATE SET "
                "  trm=excluded.trm, bob_rate=excluded.bob_rate, "
                "  aed_rate=excluded.aed_rate, clp_rate=excluded.clp_rate, "
                "  ars_rate=excluded.ars_rate, active_currencies=excluded.active_currencies, "
                "  updated_at=excluded.updated_at",
                (period, float(rates.TRM), float(rates.BOB_RATE),
                 float(rates.AED_RATE), clp, ars, ac_json, now),
            )
            conn.commit()

            # Recompute monto_usd for all income entries in this period so the
            # P&L for the month reflects the updated rates.
            new_rates = {"TRM": rates.TRM, "BOB_RATE": rates.BOB_RATE, "AED_RATE": rates.AED_RATE,
                         "CLP_RATE": clp, "ARS_RATE": ars}
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

            # Recompute monto_usd de los EGRESOS del período a la TRM del mes.
            # Daniel registra la TRM promedio del mes a ojo y quiere que el mes
            # entero quede valorado a esa tasa.
            #
            # Solo se recalculan los gastos que se COBRAN EN COP (tarjetas
            # colombianas, efectivo, transferencias locales, sin especificar):
            # ahí monto_cop es el cargo real y monto_usd = monto_cop / TRM.
            # Los métodos que NO cobran en COP (Wio/AED, ENBD, USDT, Chase) se
            # SALTAN: su monto_cop es un valor derivado y su USD no depende de
            # la TRM colombiana — recalcularlos los corrompería.
            #
            # También se SALTAN los gastos posteados por un fijo recurrente en
            # USD (ej. Claude Pro $100): su valor real es el USD, no el COP.
            trm_new = float(rates.TRM)
            exp_rows = conn.execute(
                "SELECT e.id, e.monto_cop, COALESCE(e.metodo_pago, ''), "
                "COALESCE(r.currency, 'COP') "
                "FROM expenses e LEFT JOIN recurring_expenses r ON r.id = e.recurring_id "
                "WHERE e.fecha LIKE ?",
                (period + "%",),
            ).fetchall()
            recomputed_expenses = 0
            skipped_expenses = 0
            skipped_usd_fixed = 0
            for eid, monto_cop, metodo, rec_currency in exp_rows:
                if rec_currency == "USD":
                    skipped_usd_fixed += 1
                    continue
                m = (metodo or "").upper()
                if any(h in m for h in _NON_COP_METHOD_HINTS):
                    skipped_expenses += 1
                    continue
                new_usd = round(float(monto_cop or 0) / trm_new, 2)
                conn.execute("UPDATE expenses SET monto_usd = ? WHERE id = ?", (new_usd, eid))
                recomputed_expenses += 1
            conn.commit()
        finally:
            conn.close()
        return {
            "ok": True, "period": period,
            "rates": new_rates,
            "recomputed_income_entries": len(entries),
            "recomputed_expenses": recomputed_expenses,
            "skipped_expenses_non_cop": skipped_expenses,
            "skipped_expenses_usd_fixed": skipped_usd_fixed,
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
            # Tipo de gasto (🔎 Revisión): el editor de categorías no lo manda,
            # así que se conserva el que ya tenía para no borrarlo al guardar.
            nat = info.get("naturaleza") or (bot.BUDGET.get(key) or {}).get("naturaleza")
            if nat in bot.NATURALEZA_TIPOS:
                cleaned[key]["naturaleza"] = nat

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
        # CLP/ARS opcionales: si no vienen, preservar el global actual (no borrar).
        clp = float(rates.CLP_RATE) if rates.CLP_RATE is not None else float(bot.CLP_RATE)
        ars = float(rates.ARS_RATE) if rates.ARS_RATE is not None else float(bot.ARS_RATE)
        if clp <= 0 or ars <= 0:
            raise HTTPException(400, "las tasas CLP/ARS deben ser positivas")
        new_rates = {
            "TRM": int(rates.TRM),
            "BOB_RATE": float(rates.BOB_RATE),
            "AED_RATE": float(rates.AED_RATE),
            "CLP_RATE": clp,
            "ARS_RATE": ars,
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

    # ──────────────── Reconciliación de extractos v2 ────────────────
    # Endpoints implementados incrementalmente en commits B, C, E del plan
    # ~/.claude/plans/wiggly-swinging-rose.md.
    #
    # Commit A (este): solo schema DB (migración 011) + helpers internos
    # módulo-level (RECONCILE_PREFIX_TO_METHOD, _classify_item_type,
    # _try_match_expense, _suggest_category_from_db, etc.). Cero endpoints
    # nuevos — los viejos del v1 se eliminaron porque sus tablas se hacen
    # DROP en la migración 011.
    #
    # Commit B: POST /preview, POST /confirm, GET /imports[/{id}], DELETE /imports
    # Commit C: /items/{id}/{adjudicate|revert|relink|mark}, bulk_create,
    #          rematch, reclassify, /orphans_*
    # Commit D: UI completa flujo guiado 4 pasos (solo static/index.html)
    # Commit E: /rules CRUD + widget dashboard

    # In-memory cache de previews (no se persiste hasta /confirm)
    # Limpieza: previews >2h se descartan automáticamente.
    if not hasattr(api, "_recon_preview_cache"):
        api._recon_preview_cache = {}

    def _audit_log(conn, import_id, item_id, action, payload, expense_id=None):
        conn.execute(
            "INSERT INTO reconciliation_audit_log "
            "(import_id, item_id, action, payload_json, expense_id_affected, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (import_id, item_id, action,
             json.dumps(payload, ensure_ascii=False) if payload else None,
             expense_id, datetime.now().isoformat()),
        )

    @api.post("/api/reconcile/preview")
    def reconcile_preview(body: ReconcilePreviewRequest):
        """Genera preview del diff SIN persistir nada. Retorna preview_id en
        memoria + clasificación + matching + huérfanos. El usuario revisa
        y confirma con POST /confirm/{preview_id} para persistir, o ignora
        para descartar."""
        if not body.transactions:
            raise HTTPException(400, "transactions vacío")
        if len(body.transactions) > 1000:
            raise HTTPException(400, "max 1000 transactions por import")

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            # Compute period bounds
            fechas = [t.fecha[:10] for t in body.transactions if t.fecha]
            period_from = body.period_from or (min(fechas) if fechas else None)
            period_to = body.period_to or (max(fechas) if fechas else None)
            total_cop = sum(float(t.monto_cop or 0) for t in body.transactions)

            # Existing fingerprints across all imports → para idempotencia
            existing_fps = {row[0] for row in conn.execute(
                "SELECT fingerprint FROM reconciliation_items"
            ).fetchall()}

            used_ids: set[int] = set()
            items_preview = []
            stats = {"matched": 0, "unmatched_extract": 0, "bank_charge": 0,
                     "transfer_internal": 0, "duplicate": 0, "other": 0,
                     "pago_pse": 0}

            for t in body.transactions:
                bank_prefix = _derive_bank_prefix(t.descripcion)
                item_type = _classify_item_type(t.descripcion, bank_prefix)
                fp = _make_fingerprint(bank_prefix, t.fecha[:10],
                                       float(t.monto_cop), t.descripcion)
                suggested_method = RECONCILE_PREFIX_TO_METHOD.get(bank_prefix)
                suggested_cat = _suggest_category_from_db(conn, t.descripcion)
                matched_id = None
                status = "pending"

                if fp in existing_fps:
                    status = "duplicate"
                elif item_type in RECONCILE_BANK_CHARGE_TYPES:
                    status = "bank_charge"
                    if not suggested_cat:
                        suggested_cat = "comisiones"
                elif item_type in ("compra_tc", "pago_pse",
                                   "transferencia_saliente"):
                    if suggested_method:
                        matched_id = _try_match_expense(
                            conn, t.fecha[:10], float(t.monto_cop),
                            suggested_method, used_ids,
                            tol_days_back=body.tol_days_back,
                            tol_days_forward=body.tol_days_forward,
                            tol_pct=body.tol_pct,
                        )
                    if matched_id:
                        used_ids.add(matched_id)
                        status = "matched"
                    elif item_type == "transferencia_saliente":
                        status = "transfer_internal"
                    else:
                        status = "unmatched_extract"
                else:
                    status = "other"

                stats[status] = stats.get(status, 0) + 1
                items_preview.append({
                    "fecha": t.fecha[:10],
                    "monto_cop": float(t.monto_cop),
                    "monto_original": t.monto_original,
                    "moneda_original": t.moneda_original,
                    "descripcion": t.descripcion,
                    "bank_prefix": bank_prefix,
                    "item_type": item_type,
                    "fingerprint": fp,
                    "matched_expense_id": matched_id,
                    "status": status,
                    "suggested_method_pago": suggested_method,
                    "suggested_categoria": suggested_cat,
                })

            # Cross-check bot → extracto: huérfanos
            covered = body.covered_methods or []
            orphans = []
            if covered and period_from and period_to:
                rows = conn.execute(
                    "SELECT id, fecha, monto_cop, monto_usd, categoria, nota, "
                    "COALESCE(metodo_pago,'Sin especificar') AS metodo_pago "
                    "FROM expenses "
                    "WHERE substr(fecha,1,10) >= ? AND substr(fecha,1,10) <= ? "
                    "AND COALESCE(metodo_pago,'Sin especificar') IN (" +
                    ",".join("?" * len(covered)) + ")",
                    [period_from, period_to] + covered,
                ).fetchall()
                matched_set = {i["matched_expense_id"] for i in items_preview
                               if i.get("matched_expense_id")}
                for r in rows:
                    if r[0] not in matched_set:
                        orphans.append({
                            "id": r[0], "fecha": r[1],
                            "monto_cop": r[2], "monto_usd": r[3],
                            "categoria": r[4], "nota": r[5] or "",
                            "metodo_pago": r[6],
                        })

            preview_id = "prev_" + datetime.now().strftime("%Y%m%d%H%M%S%f")
            preview_data = {
                "preview_id": preview_id,
                "label": body.label or f"Import {datetime.now().strftime('%Y-%m-%d')}",
                "cycle_label": body.cycle_label,
                "period_from": period_from,
                "period_to": period_to,
                "covered_methods": covered,
                "tol_days_back": body.tol_days_back,
                "tol_days_forward": body.tol_days_forward,
                "tol_pct": body.tol_pct,
                "notes": body.notes,
                "total_items": len(body.transactions),
                "total_cop": total_cop,
                "stats": stats,
                "items": items_preview,
                "orphans_in_bot": orphans,
                "created_at": datetime.now().isoformat(),
            }
            api._recon_preview_cache[preview_id] = preview_data
            # Limpiar previews antiguos
            cutoff = datetime.now().timestamp() - 7200
            for pid in list(api._recon_preview_cache.keys()):
                ts = api._recon_preview_cache[pid].get("created_at", "")
                try:
                    if datetime.fromisoformat(ts).timestamp() < cutoff:
                        api._recon_preview_cache.pop(pid, None)
                except Exception:
                    pass
            return preview_data
        finally:
            conn.close()

    @api.post("/api/reconcile/confirm/{preview_id}")
    def reconcile_confirm(preview_id: str):
        """Persiste el preview en DB. preview_id válido por 2h."""
        preview = api._recon_preview_cache.get(preview_id)
        if not preview:
            raise HTTPException(404, f"preview {preview_id} no encontrado o expirado")

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            now = datetime.now().isoformat()
            cur = conn.execute(
                "INSERT INTO reconciliation_imports "
                "(label, period_from, period_to, cycle_label, covered_methods_json, "
                " tol_days_back, tol_days_forward, tol_pct, total_items, total_cop, "
                " status, imported_at, confirmed_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?)",
                (preview["label"], preview["period_from"], preview["period_to"],
                 preview.get("cycle_label"),
                 json.dumps(preview.get("covered_methods", []), ensure_ascii=False),
                 preview.get("tol_days_back"), preview.get("tol_days_forward"),
                 preview.get("tol_pct"),
                 preview["total_items"], preview["total_cop"],
                 now, now, preview.get("notes") or ""),
            )
            import_id = cur.lastrowid
            for it in preview["items"]:
                conn.execute(
                    "INSERT INTO reconciliation_items "
                    "(import_id, fecha, monto_cop, monto_original, moneda_original, "
                    " descripcion, bank_prefix, item_type, fingerprint, "
                    " matched_expense_id, status, suggested_method_pago, suggested_categoria) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (import_id, it["fecha"], it["monto_cop"],
                     it.get("monto_original"), it.get("moneda_original"),
                     it["descripcion"], it["bank_prefix"], it["item_type"],
                     it["fingerprint"], it.get("matched_expense_id"),
                     it["status"], it.get("suggested_method_pago"),
                     it.get("suggested_categoria")),
                )
            _audit_log(conn, import_id, None, "import_confirmed",
                       {"preview_id": preview_id, "stats": preview["stats"],
                        "total_items": preview["total_items"]})
            conn.commit()
            api._recon_preview_cache.pop(preview_id, None)
            return {"ok": True, "import_id": import_id,
                    "total_items": preview["total_items"],
                    "stats": preview["stats"]}
        finally:
            conn.close()

    @api.get("/api/reconcile/imports")
    def list_reconcile_imports(limit: int = Query(50, ge=1, le=200)):
        rows = _query_all(
            "SELECT id, label, cycle_label, period_from, period_to, "
            "total_items, total_cop, status, imported_at, closed_at, notes "
            "FROM reconciliation_imports ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return {"imports": rows}

    @api.get("/api/reconcile/imports/{import_id}")
    def get_reconcile_import(import_id: int,
                              status: Optional[str] = Query(None),
                              month: Optional[str] = Query(None)):
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
            if month:
                q += " AND substr(fecha, 1, 7) = ?"
                params.append(month)
            q += " ORDER BY fecha, id"
            items = [dict(r) for r in conn.execute(q, params).fetchall()]
            stats: dict = {}
            for r in conn.execute(
                "SELECT status, COUNT(*), SUM(monto_cop) FROM reconciliation_items "
                "WHERE import_id = ? GROUP BY status", (import_id,),
            ).fetchall():
                stats[r[0]] = {"count": r[1], "total_cop": round(r[2] or 0, 2)}
            audit = [dict(r) for r in conn.execute(
                "SELECT * FROM reconciliation_audit_log WHERE import_id = ? "
                "ORDER BY id DESC LIMIT 100", (import_id,),
            ).fetchall()]
            return {"import": dict(imp), "items": items, "stats": stats,
                    "audit_log": audit}
        finally:
            conn.close()

    @api.post("/api/reconcile/imports/{import_id}/close")
    def close_reconcile_import(import_id: int):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute(
                "SELECT id FROM reconciliation_imports WHERE id = ?", (import_id,)
            ).fetchone()
            if not r:
                raise HTTPException(404, f"import {import_id} not found")
            now = datetime.now().isoformat()
            stats = {row[0]: row[1] for row in conn.execute(
                "SELECT status, COUNT(*) FROM reconciliation_items "
                "WHERE import_id = ? GROUP BY status", (import_id,),
            ).fetchall()}
            conn.execute(
                "UPDATE reconciliation_imports SET status='closed', closed_at=? "
                "WHERE id = ?", (now, import_id),
            )
            _audit_log(conn, import_id, None, "import_closed", {"final_stats": stats})
            conn.commit()
            return {"ok": True, "import_id": import_id, "final_stats": stats}
        finally:
            conn.close()

    @api.delete("/api/reconcile/imports/{import_id}")
    def delete_reconcile_import(import_id: int):
        """Borra import + items en cascada. NO toca expenses creados."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute(
                "SELECT COUNT(*) FROM reconciliation_imports WHERE id = ?", (import_id,)
            ).fetchone()
            if not r or r[0] == 0:
                raise HTTPException(404, f"import {import_id} not found")
            conn.execute("DELETE FROM reconciliation_audit_log WHERE import_id = ?", (import_id,))
            conn.execute("DELETE FROM reconciliation_items WHERE import_id = ?", (import_id,))
            conn.execute("DELETE FROM reconciliation_imports WHERE id = ?", (import_id,))
            conn.commit()
            return {"ok": True, "deleted_import_id": import_id}
        finally:
            conn.close()

    # ─── Item actions (Commit C) ───

    @api.patch("/api/reconcile/items/{item_id}")
    def reconcile_patch_item(item_id: int, body: ReconcileItemUpdate):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            existing = conn.execute(
                "SELECT id, import_id FROM reconciliation_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not existing:
                raise HTTPException(404, f"item {item_id} not found")
            data = body.dict(exclude_unset=True)
            if not data:
                raise HTTPException(400, "no fields to update")
            sets = ", ".join(f"{k} = ?" for k in data)
            params = list(data.values()) + [item_id]
            conn.execute(f"UPDATE reconciliation_items SET {sets} WHERE id = ?", params)
            _audit_log(conn, existing[1], item_id, "item_patched", data)
            conn.commit()
            return {"ok": True, "id": item_id, **data}
        finally:
            conn.close()

    @api.post("/api/reconcile/items/{item_id}/adjudicate")
    def reconcile_adjudicate(item_id: int, body: ReconcileAdjudicateBody):
        """Crea un expense desde un item del extracto con categoría/método
        EXPLÍCITOS del usuario. NO acepta fallbacks silenciosos.
        """
        if not body.categoria:
            raise HTTPException(400, "categoria es requerida (sin fallback 'otro')")
        if body.categoria not in (bot.BUDGET or {}):
            raise HTTPException(400, f"categoria {body.categoria!r} no existe en BUDGET")

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, import_id, fecha, monto_cop, descripcion, status "
                "FROM reconciliation_items WHERE id = ?", (item_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"item {item_id} not found")
            rid, import_id, fecha, monto_cop, desc, status = row
            if status in ("matched", "added_to_bot"):
                raise HTTPException(409, f"item ya está {status}")

            metodo = body.metodo_pago or "Sin especificar"
            user_name = body.user_name or "Daniel"
            nota = body.nota or desc[:120]
            clase = body.clase_contable or "gasto"
            monto_usd = round(float(monto_cop) / bot.TRM, 2) if bot.TRM else 0

            cur = conn.execute(
                "INSERT INTO expenses (user_id, user_name, fecha, monto_cop, monto_usd, "
                " categoria, nota, created_at, metodo_pago, clase_contable, "
                " deferred_total, deferred_index) "
                "VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)",
                (user_name, fecha, float(monto_cop), monto_usd, body.categoria,
                 nota, datetime.now().isoformat(), metodo, clase),
            )
            new_expense_id = cur.lastrowid
            conn.execute(
                "UPDATE reconciliation_items SET "
                "matched_expense_id = ?, status = 'added_to_bot', "
                "user_chosen_categoria = ?, user_chosen_metodo = ?, "
                "user_chosen_user_name = ?, user_chosen_clase_contable = ? "
                "WHERE id = ?",
                (new_expense_id, body.categoria, metodo, user_name, clase, item_id),
            )
            _audit_log(conn, import_id, item_id, "item_adjudicated",
                       {"categoria": body.categoria, "metodo_pago": metodo,
                        "user_name": user_name, "clase_contable": clase,
                        "nota": nota},
                       expense_id=new_expense_id)
            conn.commit()
            return {"ok": True, "expense_id": new_expense_id,
                    "categoria": body.categoria, "metodo_pago": metodo,
                    "user_name": user_name, "clase_contable": clase}
        finally:
            conn.close()

    @api.post("/api/reconcile/items/{item_id}/revert")
    def reconcile_revert_item(item_id: int):
        """Borra el expense creado desde este item, devuelve item al status
        original según item_type."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, import_id, matched_expense_id, status, item_type "
                "FROM reconciliation_items WHERE id = ?", (item_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"item {item_id} not found")
            rid, import_id, expense_id, status, item_type = row
            if status not in ("added_to_bot", "matched"):
                raise HTTPException(400,
                    f"item {item_id} no es revertible (status: {status})")

            deleted = False
            if expense_id and status == "added_to_bot":
                r = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
                deleted = r.rowcount > 0

            new_status = "unmatched_extract"
            if item_type in RECONCILE_BANK_CHARGE_TYPES:
                new_status = "bank_charge"
            elif item_type == "transferencia_saliente":
                new_status = "transfer_internal"
            conn.execute(
                "UPDATE reconciliation_items SET status = ?, "
                "matched_expense_id = NULL, user_chosen_categoria = NULL, "
                "user_chosen_metodo = NULL, user_chosen_user_name = NULL, "
                "user_chosen_clase_contable = NULL WHERE id = ?",
                (new_status, item_id),
            )
            _audit_log(conn, import_id, item_id, "item_reverted",
                       {"deleted_expense": deleted, "new_status": new_status},
                       expense_id=expense_id if deleted else None)
            conn.commit()
            return {"ok": True, "item_id": item_id,
                    "deleted_expense_id": expense_id if deleted else None,
                    "new_status": new_status}
        finally:
            conn.close()

    @api.post("/api/reconcile/items/{item_id}/relink")
    def reconcile_relink(item_id: int, body: ReconcileRelinkBody):
        """Vincula manualmente un item del extracto a un expense existente
        del bot."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, import_id FROM reconciliation_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"item {item_id} not found")
            exp = conn.execute(
                "SELECT id FROM expenses WHERE id = ?", (body.expense_id,),
            ).fetchone()
            if not exp:
                raise HTTPException(404, f"expense {body.expense_id} not found")
            conn.execute(
                "UPDATE reconciliation_items SET matched_expense_id = ?, "
                "status = 'matched' WHERE id = ?", (body.expense_id, item_id),
            )
            _audit_log(conn, row[1], item_id, "item_relinked",
                       {"expense_id": body.expense_id}, expense_id=body.expense_id)
            conn.commit()
            return {"ok": True, "item_id": item_id, "expense_id": body.expense_id}
        finally:
            conn.close()

    @api.post("/api/reconcile/items/{item_id}/mark")
    def reconcile_mark_item(item_id: int, body: ReconcileMarkBody):
        """Marca un item como reviewed o ignored (no crea expense)."""
        if body.action not in ("reviewed", "ignored"):
            raise HTTPException(400, f"action debe ser 'reviewed' o 'ignored'")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, import_id FROM reconciliation_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"item {item_id} not found")
            conn.execute(
                "UPDATE reconciliation_items SET status = ? WHERE id = ?",
                (body.action, item_id),
            )
            _audit_log(conn, row[1], item_id, f"item_{body.action}", None)
            conn.commit()
            return {"ok": True, "item_id": item_id, "status": body.action}
        finally:
            conn.close()

    @api.post("/api/reconcile/imports/{import_id}/bulk_create_bank_charges")
    def bulk_create_bank_charges(import_id: int,
                                  body: ReconcileBulkCreateBody):
        """Crea N expenses desde items bank_charge. Cada item DEBE tener
        suggested_categoria != null (si no, falla con 400)."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            placeholders = ",".join("?" * len(body.item_ids))
            rows = conn.execute(
                f"SELECT id, fecha, monto_cop, descripcion, suggested_categoria, "
                f"suggested_method_pago, status FROM reconciliation_items "
                f"WHERE import_id = ? AND id IN ({placeholders})",
                [import_id] + list(body.item_ids),
            ).fetchall()
            created = []
            now = datetime.now().isoformat()
            for r in rows:
                rid, fecha, monto_cop, desc, sug_cat, sug_method, status = r
                if status in ("added_to_bot", "matched"):
                    continue
                if not sug_cat:
                    raise HTTPException(400,
                        f"item {rid} sin suggested_categoria — adjudica manualmente")
                metodo = body.metodo_override or sug_method or "Sin especificar"
                user_name = body.user_name or "Daniel"
                monto_usd = round(float(monto_cop) / bot.TRM, 2) if bot.TRM else 0
                cur = conn.execute(
                    "INSERT INTO expenses (user_id, user_name, fecha, monto_cop, "
                    " monto_usd, categoria, nota, created_at, metodo_pago, "
                    " clase_contable, deferred_total, deferred_index) "
                    "VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'gasto', 1, 1)",
                    (user_name, fecha, float(monto_cop), monto_usd, sug_cat,
                     desc[:120], now, metodo),
                )
                new_id = cur.lastrowid
                conn.execute(
                    "UPDATE reconciliation_items SET matched_expense_id = ?, "
                    "status = 'added_to_bot', user_chosen_categoria = ?, "
                    "user_chosen_metodo = ?, user_chosen_user_name = ?, "
                    "user_chosen_clase_contable = 'gasto' WHERE id = ?",
                    (new_id, sug_cat, metodo, user_name, rid),
                )
                _audit_log(conn, import_id, rid, "bulk_bank_charge_created",
                           {"categoria": sug_cat, "metodo_pago": metodo},
                           expense_id=new_id)
                created.append({"item_id": rid, "expense_id": new_id})
            conn.commit()
            return {"ok": True, "created_count": len(created), "items": created}
        finally:
            conn.close()

    @api.post("/api/reconcile/imports/{import_id}/rematch")
    def reconcile_rematch(
        import_id: int,
        tol_days: Optional[int] = Query(None),
        tol_days_back: Optional[int] = Query(None),
        tol_days_forward: Optional[int] = Query(None),
        tol_pct: Optional[float] = Query(None),
        tol_cop: Optional[int] = Query(None),
        month: Optional[str] = Query(None),
    ):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            imp = conn.execute(
                "SELECT id FROM reconciliation_imports WHERE id = ?", (import_id,)
            ).fetchone()
            if not imp:
                raise HTTPException(404, f"import {import_id} not found")
            q = ("SELECT id, fecha, monto_cop, suggested_method_pago "
                 "FROM reconciliation_items WHERE import_id = ? "
                 "AND status IN ('unmatched_extract', 'pending', 'transfer_internal')")
            params: list = [import_id]
            if month:
                try:
                    datetime.strptime(month, "%Y-%m")
                except ValueError:
                    raise HTTPException(400, f"month {month!r} debe ser YYYY-MM")
                q += " AND substr(fecha, 1, 7) = ?"
                params.append(month)
            pending = conn.execute(q, params).fetchall()
            already = {r[0] for r in conn.execute(
                "SELECT matched_expense_id FROM reconciliation_items "
                "WHERE import_id = ? AND matched_expense_id IS NOT NULL",
                (import_id,),
            ).fetchall() if r[0]}
            kwargs = {}
            for k, v in [("tol_days", tol_days), ("tol_days_back", tol_days_back),
                         ("tol_days_forward", tol_days_forward),
                         ("tol_pct", tol_pct), ("tol_cop", tol_cop)]:
                if v is not None: kwargs[k] = v
            converted = 0
            for r in pending:
                rid, fecha, monto, sug_method = r
                if not sug_method:
                    continue
                eid = _try_match_expense(conn, fecha, float(monto), sug_method,
                                          already, **kwargs)
                if eid:
                    already.add(eid)
                    conn.execute(
                        "UPDATE reconciliation_items SET matched_expense_id = ?, "
                        "status = 'matched' WHERE id = ?", (eid, rid),
                    )
                    converted += 1
            _audit_log(conn, import_id, None, "rematch",
                       {"converted": converted, "tolerance": kwargs, "month": month})
            conn.commit()
            return {"ok": True, "import_id": import_id,
                    "scanned": len(pending), "converted_to_matched": converted,
                    "filter_month": month, "tolerance_used": kwargs}
        finally:
            conn.close()

    @api.post("/api/reconcile/imports/{import_id}/reclassify")
    def reconcile_reclassify(import_id: int):
        """Re-aplica heurísticas de reconcile_keyword_rules sobre items
        pending/unmatched/bank_charge/transfer_internal/other."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            imp = conn.execute(
                "SELECT id FROM reconciliation_imports WHERE id = ?", (import_id,)
            ).fetchone()
            if not imp:
                raise HTTPException(404, f"import {import_id} not found")
            rows = conn.execute(
                "SELECT id, descripcion, item_type, suggested_categoria "
                "FROM reconciliation_items WHERE import_id = ? "
                "AND status IN ('unmatched_extract', 'pending', 'bank_charge', "
                "                'transfer_internal', 'other')",
                (import_id,),
            ).fetchall()
            updated = 0
            for r in rows:
                rid, desc, itype, current = r
                new_sug = _suggest_category_from_db(conn, desc)
                if new_sug is None and itype in RECONCILE_BANK_CHARGE_TYPES:
                    new_sug = "comisiones"
                if new_sug and new_sug != current:
                    conn.execute(
                        "UPDATE reconciliation_items SET suggested_categoria = ? "
                        "WHERE id = ?", (new_sug, rid),
                    )
                    updated += 1
            _audit_log(conn, import_id, None, "reclassify", {"updated": updated})
            conn.commit()
            return {"ok": True, "scanned": len(rows), "updated": updated}
        finally:
            conn.close()

    # ─── Cross-check bot → extracto (orphans) ───

    @api.get("/api/reconcile/imports/{import_id}/orphans_in_bot")
    def reconcile_orphans_in_bot(import_id: int):
        """Lista expenses del bot del rango del import que no matchearon
        ningún item Y cuyo método_pago está en covered_methods."""
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            imp = conn.execute(
                "SELECT period_from, period_to, covered_methods_json "
                "FROM reconciliation_imports WHERE id = ?", (import_id,),
            ).fetchone()
            if not imp:
                raise HTTPException(404, f"import {import_id} not found")
            covered = json.loads(imp["covered_methods_json"] or "[]")
            if not covered:
                return {"orphans": [], "note": "covered_methods vacío"}
            matched_ids = {r[0] for r in conn.execute(
                "SELECT matched_expense_id FROM reconciliation_items "
                "WHERE import_id = ? AND matched_expense_id IS NOT NULL",
                (import_id,),
            ).fetchall() if r[0]}
            placeholders = ",".join("?" * len(covered))
            rows = conn.execute(
                f"SELECT id, fecha, monto_cop, monto_usd, categoria, nota, "
                f"COALESCE(metodo_pago,'Sin especificar') AS metodo_pago "
                f"FROM expenses "
                f"WHERE substr(fecha,1,10) >= ? AND substr(fecha,1,10) <= ? "
                f"AND COALESCE(metodo_pago,'Sin especificar') IN ({placeholders}) "
                f"ORDER BY fecha DESC",
                [imp["period_from"], imp["period_to"]] + covered,
            ).fetchall()
            orphans = [dict(r) for r in rows if r["id"] not in matched_ids]
            return {"import_id": import_id, "orphans": orphans,
                    "covered_methods": covered, "total": len(orphans)}
        finally:
            conn.close()

    @api.post("/api/reconcile/orphans/{expense_id}/find_candidates")
    def reconcile_find_candidates(expense_id: int,
                                   body: ReconcileFindCandidatesBody):
        """Busca items del extracto cercanos en fecha+monto al expense
        para que Daniel decida vincular manualmente."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            exp = conn.execute(
                "SELECT id, fecha, monto_cop, metodo_pago FROM expenses WHERE id = ?",
                (expense_id,),
            ).fetchone()
            if not exp:
                raise HTTPException(404, f"expense {expense_id} not found")
            from datetime import datetime as _dt, timedelta
            try:
                d_exp = _dt.strptime(exp[1][:10], "%Y-%m-%d")
            except Exception:
                raise HTTPException(400, "expense.fecha mal formada")
            tol_days = body.tol_days or 10
            tol_pct = body.tol_pct or 0.10
            d_from = (d_exp - timedelta(days=tol_days)).strftime("%Y-%m-%d")
            d_to = (d_exp + timedelta(days=tol_days)).strftime("%Y-%m-%d")
            tol_cop = max(5000, abs(float(exp[2])) * tol_pct)
            rows = conn.execute(
                "SELECT id, import_id, fecha, monto_cop, descripcion, status, "
                "matched_expense_id FROM reconciliation_items "
                "WHERE substr(fecha,1,10) >= ? AND substr(fecha,1,10) <= ? "
                "AND ABS(monto_cop - ?) <= ?",
                (d_from, d_to, exp[2], tol_cop),
            ).fetchall()
            candidates = [
                {"id": r[0], "import_id": r[1], "fecha": r[2],
                 "monto_cop": r[3], "descripcion": r[4], "status": r[5],
                 "matched_expense_id": r[6]}
                for r in rows
            ]
            return {"expense_id": expense_id,
                    "expense_fecha": exp[1], "expense_monto_cop": exp[2],
                    "candidates": candidates}
        finally:
            conn.close()

    @api.post("/api/reconcile/orphans/{expense_id}/relink_to/{item_id}")
    def reconcile_relink_orphan(expense_id: int, item_id: int):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            exp = conn.execute("SELECT id FROM expenses WHERE id = ?",
                                (expense_id,)).fetchone()
            if not exp:
                raise HTTPException(404, f"expense {expense_id} not found")
            it = conn.execute(
                "SELECT id, import_id FROM reconciliation_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not it:
                raise HTTPException(404, f"item {item_id} not found")
            conn.execute(
                "UPDATE reconciliation_items SET matched_expense_id = ?, "
                "status = 'matched' WHERE id = ?", (expense_id, item_id),
            )
            _audit_log(conn, it[1], item_id, "orphan_resolved",
                       {"expense_id": expense_id}, expense_id=expense_id)
            conn.commit()
            return {"ok": True, "item_id": item_id, "expense_id": expense_id}
        finally:
            conn.close()

    # ─── Heurísticas keyword (Commit E) ───

    @api.get("/api/reconcile/rules")
    def reconcile_list_rules():
        rows = _query_all(
            "SELECT id, pattern, suggested_categoria, priority, enabled, notes, "
            "created_at, updated_at FROM reconcile_keyword_rules "
            "ORDER BY priority ASC, id ASC"
        )
        return {"rules": rows}

    @api.post("/api/reconcile/rules")
    def reconcile_create_rule(body: ReconcileRuleBody):
        if not body.pattern or not body.suggested_categoria:
            raise HTTPException(400, "pattern y suggested_categoria son requeridos")
        # Validar regex
        try:
            re.compile(body.pattern)
        except re.error as e:
            raise HTTPException(400, f"regex inválido: {e}")
        if body.suggested_categoria not in (bot.BUDGET or {}):
            raise HTTPException(400, f"categoria {body.suggested_categoria!r} no existe")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            now = datetime.now().isoformat()
            cur = conn.execute(
                "INSERT INTO reconcile_keyword_rules "
                "(pattern, suggested_categoria, priority, enabled, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (body.pattern, body.suggested_categoria,
                 body.priority if body.priority is not None else 100,
                 1 if (body.enabled is None or body.enabled) else 0,
                 body.notes or "", now),
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid}
        finally:
            conn.close()

    @api.patch("/api/reconcile/rules/{rule_id}")
    def reconcile_update_rule(rule_id: int, body: ReconcileRuleBody):
        if body.pattern:
            try: re.compile(body.pattern)
            except re.error as e:
                raise HTTPException(400, f"regex inválido: {e}")
        if body.suggested_categoria and body.suggested_categoria not in (bot.BUDGET or {}):
            raise HTTPException(400, f"categoria {body.suggested_categoria!r} no existe")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            existing = conn.execute(
                "SELECT id FROM reconcile_keyword_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            if not existing:
                raise HTTPException(404, f"rule {rule_id} not found")
            data = body.dict(exclude_unset=True)
            data["updated_at"] = datetime.now().isoformat()
            sets = ", ".join(f"{k} = ?" for k in data)
            params = list(data.values()) + [rule_id]
            conn.execute(f"UPDATE reconcile_keyword_rules SET {sets} WHERE id = ?", params)
            conn.commit()
            return {"ok": True, "id": rule_id, **data}
        finally:
            conn.close()

    @api.delete("/api/reconcile/rules/{rule_id}")
    def reconcile_delete_rule(rule_id: int):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute("DELETE FROM reconcile_keyword_rules WHERE id = ?", (rule_id,))
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"rule {rule_id} not found")
            return {"ok": True, "deleted": rule_id}
        finally:
            conn.close()

    @api.post("/api/reconcile/rules/test")
    def reconcile_test_rule(body: ReconcileRuleTestBody):
        """Testea un pattern contra una lista de descripciones SIN guardar.
        Útil antes de crear una regla nueva."""
        try:
            rx = re.compile(body.pattern, re.IGNORECASE)
        except re.error as e:
            raise HTTPException(400, f"regex inválido: {e}")
        results = []
        for desc in body.sample_descriptions:
            results.append({"desc": desc, "match": bool(rx.search(desc))})
        return {"pattern": body.pattern, "results": results,
                "match_count": sum(1 for r in results if r["match"])}

    @api.get("/api/reconcile/last_summary")
    def reconcile_last_summary():
        """Resumen del último import cerrado (para widget en dashboard)."""
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            imp = conn.execute(
                "SELECT id, label, cycle_label, period_from, period_to, "
                "total_items, total_cop, status, imported_at, closed_at "
                "FROM reconciliation_imports "
                "WHERE status = 'closed' ORDER BY closed_at DESC LIMIT 1"
            ).fetchone()
            if not imp:
                return {"last_import": None}
            stats = {}
            for r in conn.execute(
                "SELECT status, COUNT(*), SUM(monto_cop) FROM reconciliation_items "
                "WHERE import_id = ? GROUP BY status", (imp["id"],)
            ).fetchall():
                stats[r[0]] = {"count": r[1], "total_cop": round(r[2] or 0, 2)}
            return {"last_import": dict(imp), "stats": stats}
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

    @api.get("/api/export/csv_full")
    def export_csv_full(month: Optional[str] = Query(None)):
        """Comprehensive monthly CSV: combina gastos + ingresos en un solo
        archivo, enriquecidos con metadata de categoría (parent, label,
        icon), método de pago, clase contable, info de cuotas diferidas
        y nombre/icono de fuente de ingreso. Diseñado para análisis
        externo (ej. subir a Claude Code y pedir patrones de eficiencia).
        """
        prefix = _month_prefix(month)

        # Gastos enriquecidos
        expense_rows = _query_all(
            "SELECT id, user_name, fecha, monto_cop, monto_usd, categoria, nota, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago, "
            "COALESCE(clase_contable, 'gasto') AS clase_contable, "
            "deferred_total, deferred_index, deferred_group_id, deferred_mode, "
            "created_at "
            "FROM expenses WHERE fecha LIKE ? ORDER BY fecha DESC, id DESC",
            (f"{prefix}%",),
        )

        # Ingresos enriquecidos con label/icon de la fuente
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            income_rows = conn.execute(
                "SELECT e.id, e.source_key, e.fecha, e.monto, e.currency, "
                "e.monto_usd, e.rate_used, e.nota, e.created_at, "
                "s.label AS source_label, s.icon AS source_icon "
                "FROM income_entries e "
                "LEFT JOIN income_sources s ON e.source_key = s.key "
                "WHERE e.period = ? "
                "ORDER BY e.fecha DESC NULLS LAST, e.id DESC",
                (prefix,),
            ).fetchall()
            income_rows = [dict(r) for r in income_rows]
        finally:
            conn.close()

        if not expense_rows and not income_rows:
            raise HTTPException(404, f"sin gastos ni ingresos para {prefix}")

        def cat_meta(key):
            v = bot.BUDGET.get(key, {}) if isinstance(bot.BUDGET, dict) else {}
            parent_key = v.get("parent") or ""
            parent_v = bot.BUDGET.get(parent_key, {}) if parent_key else {}
            return (
                v.get("label", key),
                v.get("icon", "📦"),
                parent_key,
                parent_v.get("label", "") if parent_key else "",
            )

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "tipo", "id", "fecha", "monto_cop", "monto_usd", "moneda_original",
            "categoria_key", "categoria_label", "categoria_icon",
            "parent_key", "parent_label",
            "usuario", "nota",
            "metodo_pago", "clase_contable",
            "deferred_total", "deferred_index", "deferred_group_id", "deferred_mode",
            "tasa_conversion", "created_at",
        ])

        for r in expense_rows:
            label, icon, p_key, p_label = cat_meta(r["categoria"])
            writer.writerow([
                "gasto", r["id"], r["fecha"], r["monto_cop"], r["monto_usd"], "COP",
                r["categoria"], label, icon,
                p_key, p_label,
                r["user_name"] or "", r["nota"] or "",
                r["metodo_pago"], r["clase_contable"],
                r["deferred_total"] if r["deferred_total"] is not None else "",
                r["deferred_index"] if r["deferred_index"] is not None else "",
                r["deferred_group_id"] or "",
                r["deferred_mode"] or "",
                "", r["created_at"] or "",
            ])

        for r in income_rows:
            writer.writerow([
                "ingreso", r["id"], r["fecha"] or "", "", r["monto_usd"],
                r["currency"] or "USD",
                r["source_key"] or "",
                r["source_label"] or r["source_key"] or "",
                r["source_icon"] or "💰",
                "", "",  # ingresos no usan parent/sub
                "", r["nota"] or "",
                "", "",  # ni metodo_pago ni clase_contable
                "", "", "", "",
                r["rate_used"] if r["rate_used"] is not None else "",
                r["created_at"] or "",
            ])

        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="gg_finanzas_{prefix.replace("-", "_")}.csv"'
            },
        )

    @api.get("/api/export/csv_projected")
    def export_csv_projected(month: Optional[str] = Query(None)):
        """CSV de PROYECCIONES del mes: presupuesto por categoría (con
        overrides per-período si los hay) + ingresos esperados por fuente
        activa. Complementa /api/export/csv_full (datos reales) para que
        Daniel pueda comparar plan vs ejecutado en análisis externo.
        """
        prefix = _month_prefix(month)
        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # Presupuesto efectivo por categoría para este período
            eff = _effective_budget_for_period(conn, prefix)
            # Fuentes de ingreso (todas, marcamos cuáles están activas)
            income_sources = conn.execute(
                "SELECT key, label, icon, currency, expected_usd, active, "
                "created_at, updated_at "
                "FROM income_sources ORDER BY active DESC, label"
            ).fetchall()
            income_sources = [dict(r) for r in income_sources]
        finally:
            conn.close()

        if not eff and not income_sources:
            raise HTTPException(404, f"sin proyección para {prefix}")

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "tipo", "key", "label", "icon",
            "parent_key", "parent_label",
            "monto_usd_mensual", "monto_usd_anual",
            "tipo_categoria", "is_override",
            "moneda", "active", "notas",
        ])

        # Gastos proyectados (orden: parents primero, luego subs)
        # Ordenamos para que el CSV sea legible: top-level alfabético, sub
        # debajo del parent. Mismos criterios que el picker de Telegram.
        top_level = sorted(
            [(k, v) for k, v in eff.items() if not v.get("parent")],
            key=lambda kv: kv[1].get("label", kv[0]).lower(),
        )
        for pk, pv in top_level:
            p_label = pv.get("label", pk)
            # Parent (sin importe si tiene hijos — el budget vive en los subs)
            writer.writerow([
                "gasto_proyectado", pk, p_label, pv.get("icon", "📦"),
                "", "",
                pv.get("usd", 0), pv.get("annual_usd", 0),
                pv.get("tipo", "variable"),
                "true" if pv.get("is_override") else "false",
                "USD", "1",
                "padre con sub-categorías" if pv.get("has_children") else "",
            ])
            # Subs del parent
            subs = sorted(
                [(k, v) for k, v in eff.items() if v.get("parent") == pk],
                key=lambda kv: kv[1].get("label", kv[0]).lower(),
            )
            for ck, cv in subs:
                writer.writerow([
                    "gasto_proyectado", ck, cv.get("label", ck), cv.get("icon", "📦"),
                    pk, p_label,
                    cv.get("usd", 0), cv.get("annual_usd", 0),
                    cv.get("tipo", "variable"),
                    "true" if cv.get("is_override") else "false",
                    "USD", "1", "",
                ])
        # Huérfanos (parent no encontrado) — defensivo
        seen = {k for k, _ in top_level}
        for k, v in eff.items():
            if k not in seen and v.get("parent") not in {tk for tk, _ in top_level}:
                writer.writerow([
                    "gasto_proyectado", k, v.get("label", k), v.get("icon", "📦"),
                    v.get("parent") or "", "",
                    v.get("usd", 0), v.get("annual_usd", 0),
                    v.get("tipo", "variable"),
                    "true" if v.get("is_override") else "false",
                    "USD", "1", "huérfano (parent no encontrado)",
                ])

        # Ingresos proyectados (por fuente)
        for s in income_sources:
            writer.writerow([
                "ingreso_proyectado", s["key"], s["label"] or s["key"],
                s["icon"] or "💰",
                "", "",
                s["expected_usd"] or 0,
                round((s["expected_usd"] or 0) * 12, 2),
                "", "",
                s["currency"] or "USD",
                "1" if s["active"] else "0",
                "" if s["active"] else "fuente inactiva",
            ])

        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="gg_proyeccion_{prefix.replace("-", "_")}.csv"'
            },
        )

    @api.get("/api/analytics/daily")
    def analytics_daily(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = Query(None),
    ):
        """Gasto diario en USD agrupado por categoría PADRE, en un rango de
        fechas (a nivel día, cruza meses). Devuelve la matriz diaria para la
        gráfica + stats (media/mediana por transacción) por categoría y por
        método de pago. Solo lectura — no toca nada.
        """
        import datetime as _dt
        from collections import defaultdict

        if not to:
            to = _dt.date.today().isoformat()
        if not from_:
            from_ = (_dt.date.fromisoformat(to) - _dt.timedelta(days=29)).isoformat()
        try:
            d_from = _dt.date.fromisoformat(from_)
            d_to = _dt.date.fromisoformat(to)
        except ValueError:
            raise HTTPException(400, "from/to deben ser YYYY-MM-DD")
        if d_from > d_to:
            d_from, d_to = d_to, d_from
        # Límite defensivo: máximo 366 días por consulta
        if (d_to - d_from).days > 366:
            d_from = d_to - _dt.timedelta(days=366)

        rows = _query_all(
            "SELECT fecha, monto_usd, categoria, "
            "COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago "
            "FROM expenses WHERE fecha >= ? AND fecha <= ? ORDER BY fecha",
            (d_from.isoformat(), d_to.isoformat()),
        )

        def _parent_group(cat):
            v = bot.BUDGET.get(cat, {}) if isinstance(bot.BUDGET, dict) else {}
            return v.get("parent") or cat

        def _meta(key):
            v = bot.BUDGET.get(key, {}) if isinstance(bot.BUDGET, dict) else {}
            return v.get("label", key), v.get("icon", "📦")

        def _median(lst):
            if not lst:
                return 0.0
            s = sorted(lst)
            n = len(s)
            m = n // 2
            return s[m] if n % 2 else (s[m - 1] + s[m]) / 2

        # Lista de todos los días del rango (incluye días sin gasto)
        days = []
        cur = d_from
        while cur <= d_to:
            days.append(cur.isoformat())
            cur += _dt.timedelta(days=1)

        group_total = defaultdict(float)
        daily = {d: defaultdict(float) for d in days}
        amounts_by_group = defaultdict(list)
        amounts_by_method = defaultdict(list)
        method_total = defaultdict(float)
        grand = 0.0
        for r in rows:
            g = _parent_group(r["categoria"])
            amt = float(r["monto_usd"] or 0)
            group_total[g] += amt
            day = (r["fecha"] or "")[:10]
            if day in daily:
                daily[day][g] += amt
            amounts_by_group[g].append(amt)
            amounts_by_method[r["metodo_pago"]].append(amt)
            method_total[r["metodo_pago"]] += amt
            grand += amt

        # Ordenar grupos por gasto total desc; tope 11 + "Otros" para que la
        # gráfica apilada sea legible (aunque agrupemos por padre).
        ordered = sorted(group_total.items(), key=lambda x: -x[1])
        CAP = 11
        top_keys = [k for k, _ in ordered[:CAP]]
        other_keys = [k for k, _ in ordered[CAP:]]

        chart_groups = []
        for k in top_keys:
            lab, ic = _meta(k)
            chart_groups.append({"key": k, "label": lab, "icon": ic,
                                 "total_usd": round(group_total[k], 2)})
        if other_keys:
            chart_groups.append({
                "key": "__otros__", "label": "Otros", "icon": "📦",
                "total_usd": round(sum(group_total[k] for k in other_keys), 2),
            })

        daily_out = {}
        for d in days:
            dd = daily[d]
            row = {}
            for k in top_keys:
                if dd.get(k):
                    row[k] = round(dd[k], 2)
            if other_keys:
                ot = sum(dd.get(k, 0) for k in other_keys)
                if ot:
                    row["__otros__"] = round(ot, 2)
            daily_out[d] = row

        stats_by_category = []
        for k, _ in ordered:
            lab, ic = _meta(k)
            amts = amounts_by_group[k]
            stats_by_category.append({
                "key": k, "label": lab, "icon": ic,
                "count": len(amts), "total_usd": round(group_total[k], 2),
                "mean_usd": round(sum(amts) / len(amts), 2) if amts else 0.0,
                "median_usd": round(_median(amts), 2),
            })

        stats_by_payment = []
        for m, tot in sorted(method_total.items(), key=lambda x: -x[1]):
            amts = amounts_by_method[m]
            stats_by_payment.append({
                "method": m, "count": len(amts), "total_usd": round(tot, 2),
                "mean_usd": round(sum(amts) / len(amts), 2) if amts else 0.0,
                "median_usd": round(_median(amts), 2),
            })

        return {
            "from": d_from.isoformat(),
            "to": d_to.isoformat(),
            "n_days": len(days),
            "days": days,
            "groups": chart_groups,
            "daily": daily_out,
            "grand_total_usd": round(grand, 2),
            "avg_daily_usd": round(grand / len(days), 2) if days else 0.0,
            "stats_by_category": stats_by_category,
            "stats_by_payment": stats_by_payment,
        }

    # ──────────────── Gastos fijos recurrentes ────────────────

    @api.get("/api/recurring")
    def list_recurring():
        rows = _query_all(
            "SELECT id, label, monto_cop, categoria, metodo_pago, user_name, active, "
            "COALESCE(currency, 'COP') AS currency, monto_usd, "
            "created_at, updated_at FROM recurring_expenses ORDER BY active DESC, id"
        )
        return {"recurring": rows}

    @api.post("/api/recurring")
    def create_recurring(body: RecurringCreate):
        cur_code = (body.currency or "COP").upper()
        if cur_code not in ("COP", "USD"):
            raise HTTPException(400, "currency debe ser COP o USD")
        if not body.label.strip() or not body.categoria.strip():
            raise HTTPException(400, "label y categoria son requeridos")
        if cur_code == "USD":
            if not body.monto_usd or body.monto_usd <= 0:
                raise HTTPException(400, "monto_usd (>0) es requerido para un fijo en USD")
            monto_usd = round(float(body.monto_usd), 2)
            monto_cop = float(round(monto_usd * bot.TRM))
        else:
            if not body.monto_cop or body.monto_cop <= 0:
                raise HTTPException(400, "monto_cop (>0) es requerido")
            monto_cop = float(body.monto_cop)
            monto_usd = None
        now = datetime.now().isoformat()
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            cur = conn.execute(
                "INSERT INTO recurring_expenses (label, monto_cop, categoria, metodo_pago, user_name, active, "
                "created_at, updated_at, currency, monto_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (body.label.strip(), monto_cop, body.categoria.strip(),
                 body.metodo_pago or "Transferencia BBVA", body.user_name or "Daniel",
                 1 if body.active is None else int(body.active), now, now,
                 cur_code, monto_usd),
            )
            conn.commit()
            rid = cur.lastrowid
        finally:
            conn.close()
        return {"ok": True, "id": rid}

    @api.patch("/api/recurring/{rec_id}")
    def update_recurring(rec_id: int, body: RecurringUpdate):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT COALESCE(currency, 'COP'), monto_usd FROM recurring_expenses WHERE id = ?",
                (rec_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"recurring {rec_id} no existe")
            new_cur = (body.currency or row[0]).upper()
            if new_cur not in ("COP", "USD"):
                raise HTTPException(400, "currency debe ser COP o USD")
            if body.monto_cop is not None and body.monto_cop <= 0:
                raise HTTPException(400, "monto_cop debe ser > 0")
            if body.monto_usd is not None and body.monto_usd <= 0:
                raise HTTPException(400, "monto_usd debe ser > 0")
            if new_cur == "USD" and body.monto_cop is not None:
                raise HTTPException(400, "este fijo es en USD: edita monto_usd, no monto_cop")
            if new_cur == "COP" and body.monto_usd is not None:
                raise HTTPException(400, "monto_usd solo aplica a fijos en USD")

            fields, params = [], []
            for col, val in [("label", body.label), ("monto_cop", body.monto_cop),
                             ("categoria", body.categoria), ("metodo_pago", body.metodo_pago),
                             ("user_name", body.user_name), ("active", body.active)]:
                if val is not None:
                    fields.append(f"{col} = ?")
                    params.append(val)
            if body.currency is not None:
                fields.append("currency = ?")
                params.append(new_cur)
            if new_cur == "USD" and (body.monto_usd is not None or body.currency is not None):
                usd = body.monto_usd if body.monto_usd is not None else row[1]
                if not usd or usd <= 0:
                    raise HTTPException(400, "monto_usd (>0) es requerido para un fijo en USD")
                usd = round(float(usd), 2)
                fields += ["monto_usd = ?", "monto_cop = ?"]
                params += [usd, float(round(usd * bot.TRM))]
            if not fields:
                raise HTTPException(400, "nada que actualizar")
            fields.append("updated_at = ?")
            params.append(datetime.now().isoformat())
            params.append(rec_id)
            conn.execute(f"UPDATE recurring_expenses SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "id": rec_id}

    @api.delete("/api/recurring/{rec_id}")
    def delete_recurring(rec_id: int):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute("DELETE FROM recurring_expenses WHERE id = ?", (rec_id,))
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"recurring {rec_id} no existe")
        finally:
            conn.close()
        return {"ok": True, "deleted": rec_id}

    @api.post("/api/recurring/run/{period}")
    def run_recurring(period: str):
        """Postea los recurrentes activos para el período dado (idempotente).
        Sirve para backfill de un mes puntual (ej. agosto)."""
        if not _VALID_PERIOD.match(period):
            raise HTTPException(400, f"period inválido: {period!r}")
        posted = bot.post_recurring_for_period(period)
        return {"ok": True, "period": period, "posted": posted, "count": len(posted)}

    # ──────────────── Recordatorios de pago (Próximos pagos) ────────────────
    # El estado (días que faltan, color) lo calcula el navegador con la fecha
    # local de Daniel; el servidor corre en UTC y no debe decidir "hoy".

    def _validate_reminder(kind, due_day, amount, currency):
        if kind is not None and kind not in _REMINDER_KINDS:
            raise HTTPException(400, f"kind debe ser uno de {_REMINDER_KINDS}")
        if due_day is not None and not (1 <= int(due_day) <= 31):
            raise HTTPException(400, "due_day debe estar entre 1 y 31")
        if amount is not None and amount < 0:
            raise HTTPException(400, "amount debe ser ≥ 0")
        if currency is not None and currency.upper() not in _REMINDER_CURRENCIES:
            raise HTTPException(400, f"currency debe ser una de {_REMINDER_CURRENCIES}")

    @api.get("/api/reminders")
    def list_reminders():
        rows = _query_all(
            "SELECT id, label, kind, due_day, amount, currency, notes, active, created_at, updated_at "
            "FROM payment_reminders ORDER BY active DESC, (due_day IS NULL), due_day, id"
        )
        paid = _query_all(
            "SELECT reminder_id, period, paid_at FROM payment_reminder_paid "
            "WHERE period >= ? ORDER BY period",
            ((datetime.now() - timedelta(days=100)).strftime("%Y-%m"),),
        )
        by_id = {}
        for p in paid:
            by_id.setdefault(p["reminder_id"], []).append(p["period"])
        for r in rows:
            r["paid_periods"] = by_id.get(r["id"], [])
        return {"reminders": rows}

    @api.post("/api/reminders")
    def create_reminder(body: ReminderCreate):
        label = (body.label or "").strip()
        if not label:
            raise HTTPException(400, "label es requerido")
        _validate_reminder(body.kind, body.due_day, body.amount, body.currency)
        now = datetime.now().isoformat()
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            cur = conn.execute(
                "INSERT INTO payment_reminders (label, kind, due_day, amount, currency, notes, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (label, body.kind or "tarjeta", body.due_day, body.amount,
                 (body.currency or "COP").upper(), body.notes, now, now),
            )
            conn.commit()
            rid = cur.lastrowid
        finally:
            conn.close()
        return {"ok": True, "id": rid}

    @api.patch("/api/reminders/{rid}")
    def update_reminder(rid: int, body: ReminderUpdate):
        _validate_reminder(body.kind, body.due_day, body.amount, body.currency)
        fields, params = [], []
        if body.label is not None:
            if not body.label.strip():
                raise HTTPException(400, "label no puede quedar vacío")
            fields.append("label = ?"); params.append(body.label.strip())
        for col, val in [("kind", body.kind), ("notes", body.notes), ("active", body.active)]:
            if val is not None:
                fields.append(f"{col} = ?"); params.append(val)
        if body.currency is not None:
            fields.append("currency = ?"); params.append(body.currency.upper())
        if body.clear_due_day:
            fields.append("due_day = NULL")
        elif body.due_day is not None:
            fields.append("due_day = ?"); params.append(int(body.due_day))
        if body.clear_amount:
            fields.append("amount = NULL")
        elif body.amount is not None:
            fields.append("amount = ?"); params.append(float(body.amount))
        if not fields:
            raise HTTPException(400, "nada que actualizar")
        fields.append("updated_at = ?"); params.append(datetime.now().isoformat())
        params.append(rid)
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute(f"UPDATE payment_reminders SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"recordatorio {rid} no existe")
        finally:
            conn.close()
        return {"ok": True, "id": rid}

    @api.delete("/api/reminders/{rid}")
    def delete_reminder(rid: int):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            r = conn.execute("DELETE FROM payment_reminders WHERE id = ?", (rid,))
            conn.execute("DELETE FROM payment_reminder_paid WHERE reminder_id = ?", (rid,))
            conn.commit()
            if r.rowcount == 0:
                raise HTTPException(404, f"recordatorio {rid} no existe")
        finally:
            conn.close()
        return {"ok": True, "deleted": rid}

    @api.post("/api/reminders/{rid}/paid")
    def mark_reminder_paid(rid: int, body: ReminderPaid):
        if not _VALID_PERIOD.match(body.period or ""):
            raise HTTPException(400, f"period inválido: {body.period!r}")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            if not conn.execute("SELECT 1 FROM payment_reminders WHERE id = ?", (rid,)).fetchone():
                raise HTTPException(404, f"recordatorio {rid} no existe")
            if body.paid:
                conn.execute(
                    "INSERT OR REPLACE INTO payment_reminder_paid (reminder_id, period, paid_at) VALUES (?, ?, ?)",
                    (rid, body.period, datetime.now().isoformat()),
                )
            else:
                conn.execute("DELETE FROM payment_reminder_paid WHERE reminder_id = ? AND period = ?",
                             (rid, body.period))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "id": rid, "period": body.period, "paid": body.paid}

    # ──────────────── 📅 Diferidos ────────────────
    def _deferred_view(conn, gid: int) -> dict:
        rows = _deferred_rows(conn, gid)
        if not rows:
            raise HTTPException(404, f"diferido {gid} no existe")
        meta = conn.execute("SELECT concepto, currency FROM deferred_meta WHERE group_id = ?", (gid,)).fetchone()
        return _deferred_summary(rows, meta, _deferred_today())

    @api.get("/api/deferred")
    def list_deferred():
        """Todos los grupos diferidos: activos primero, luego terminados."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            gids = [r[0] for r in conn.execute(
                "SELECT DISTINCT COALESCE(deferred_group_id, id) FROM expenses "
                "WHERE deferred_group_id IS NOT NULL OR COALESCE(deferred_total, 1) > 1"
            ).fetchall()]
            metas = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT group_id, concepto, currency FROM deferred_meta").fetchall()}
            today = _deferred_today()
            groups = []
            for gid in gids:
                rows = _deferred_rows(conn, gid)
                if rows:
                    groups.append(_deferred_summary(rows, metas.get(gid), today))
        finally:
            conn.close()
        activos = sorted([g for g in groups if g["estado"] == "activo"], key=lambda g: (g["hasta"], g["concepto"].lower()))
        terminados = sorted([g for g in groups if g["estado"] != "activo"], key=lambda g: g["hasta"], reverse=True)
        return {"groups": activos + terminados, "today": _deferred_today()}

    @api.post("/api/deferred")
    def create_deferred(body: DeferredCreate):
        """Crea un diferido nuevo: N cuotas mensuales desde fecha_inicio."""
        cur_code = (body.currency or "COP").upper()
        if cur_code not in DEFERRED_CURRENCIES:
            raise HTTPException(400, "currency debe ser COP o USD")
        if not body.concepto.strip() or not body.categoria.strip():
            raise HTTPException(400, "concepto y categoría son requeridos")
        n = int(body.cuotas)
        if n < 2 or n > DEFERRED_MAX_CUOTAS:
            raise HTTPException(400, f"cuotas debe estar entre 2 y {DEFERRED_MAX_CUOTAS}")
        if not body.monto or body.monto <= 0:
            raise HTTPException(400, "monto debe ser > 0")
        tipo = (body.monto_tipo or "total").lower()
        if tipo not in ("total", "cuota"):
            raise HTTPException(400, "monto_tipo debe ser 'total' o 'cuota'")
        mode = body.mode or "credito"
        if mode not in DEFERRAL_MODES_PERMITIDOS:
            raise HTTPException(400, f"mode debe ser uno de {sorted(DEFERRAL_MODES_PERMITIDOS)}")
        clase = body.clase_contable or "gasto"
        if clase not in CLASES_CONTABLES_PERMITIDAS:
            raise HTTPException(400, f"clase_contable debe ser una de {sorted(CLASES_CONTABLES_PERMITIDAS)}")
        try:
            datetime.strptime(body.fecha_inicio[:10], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"fecha_inicio inválida {body.fecha_inicio!r}, se espera YYYY-MM-DD")
        valores = (_split_cuotas(float(body.monto), n, cur_code) if tipo == "total"
                   else [round(float(body.monto), 0 if cur_code == "COP" else 2)] * n)
        concepto = body.concepto.strip()
        now = datetime.now().isoformat()
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            conn.execute("BEGIN")
            gid = None
            for k in range(1, n + 1):
                fecha = _month_offset(body.fecha_inicio[:10], k - 1)
                cop, usd = _amounts_for(conn, fecha, valores[k - 1], cur_code)
                cur = conn.execute(
                    "INSERT INTO expenses (user_id, user_name, fecha, monto_cop, monto_usd, categoria, nota, "
                    "created_at, metodo_pago, clase_contable, deferred_total, deferred_index, "
                    "deferred_group_id, deferred_mode) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (body.user_name or "Daniel", fecha, cop, usd, body.categoria.strip(), concepto, now,
                     body.metodo_pago or "Sin especificar", clase, n, k, gid, mode),
                )
                if gid is None:
                    gid = cur.lastrowid
                    conn.execute("UPDATE expenses SET deferred_group_id = ? WHERE id = ?", (gid, gid))
            conn.execute(
                "INSERT OR REPLACE INTO deferred_meta (group_id, concepto, currency, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)", (gid, concepto, cur_code, now, now))
            conn.execute("COMMIT")
            return {"ok": True, "group": _deferred_view(conn, gid)}
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @api.patch("/api/deferred/{gid}")
    def update_deferred(gid: int, body: DeferredUpdate):
        """Edita un diferido. Campos que no vienen no se tocan.

        Orden: datos de todas las cuotas → re-fechar (fecha_inicio) → número
        de cuotas (agrega al final o quita las últimas) → valor de la cuota
        (todas, o desde la cuota `desde`). Los montos se recalculan con la
        TRM del mes de cada cuota según la moneda del grupo.
        """
        data = body.dict(exclude_unset=True)
        if not data:
            raise HTTPException(400, "nada que cambiar")
        if "mode" in data and data["mode"] not in DEFERRAL_MODES_PERMITIDOS:
            raise HTTPException(400, f"mode debe ser uno de {sorted(DEFERRAL_MODES_PERMITIDOS)}")
        if "currency" in data:
            data["currency"] = (data["currency"] or "").upper()
            if data["currency"] not in DEFERRED_CURRENCIES:
                raise HTTPException(400, "currency debe ser COP o USD")
        if "cuota" in data and not (data["cuota"] and data["cuota"] > 0):
            raise HTTPException(400, "cuota debe ser > 0")
        if "cuotas" in data and not (2 <= int(data["cuotas"]) <= DEFERRED_MAX_CUOTAS):
            raise HTTPException(400, f"cuotas debe estar entre 2 y {DEFERRED_MAX_CUOTAS}")
        if "fecha_inicio" in data:
            try:
                datetime.strptime((data["fecha_inicio"] or "")[:10], "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "fecha_inicio inválida, se espera YYYY-MM-DD")
        for f in ("concepto", "categoria"):
            if f in data and not (data[f] or "").strip():
                raise HTTPException(400, f"{f} no puede quedar vacío")
        now = datetime.now().isoformat()
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            rows = _deferred_rows(conn, gid)
            if not rows:
                raise HTTPException(404, f"diferido {gid} no existe")
            meta = conn.execute("SELECT concepto, currency FROM deferred_meta WHERE group_id = ?", (gid,)).fetchone()
            old_cur = (meta[1] if meta and meta[1] else "COP")
            new_cur = data.get("currency", old_cur)
            if new_cur != old_cur and "cuota" not in data:
                raise HTTPException(400, "para cambiar la moneda manda también el valor de la cuota")
            conn.execute("BEGIN")
            ids = [r["id"] for r in rows]
            qmarks = ",".join("?" * len(ids))
            # 1) campos de todas las cuotas
            sets, params = [], []
            for f, col in (("concepto", "nota"), ("categoria", "categoria"), ("metodo_pago", "metodo_pago"),
                           ("user_name", "user_name"), ("mode", "deferred_mode")):
                if f in data:
                    sets.append(f"{col} = ?")
                    params.append(data[f].strip() if isinstance(data[f], str) else data[f])
            if sets:
                conn.execute(f"UPDATE expenses SET {', '.join(sets)} WHERE id IN ({qmarks})", (*params, *ids))
            # meta (concepto/moneda); los grupos viejos la estrenan aquí
            concepto = data.get("concepto", (meta[0] if meta else None) or _deferred_summary(rows, meta, _deferred_today())["concepto"])
            conn.execute(
                "INSERT INTO deferred_meta (group_id, concepto, currency, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(group_id) DO UPDATE SET concepto = excluded.concepto, currency = excluded.currency, "
                "updated_at = excluded.updated_at",
                (gid, concepto.strip() if concepto else None, new_cur, now, now))
            # fecha de la cuota 1 (aunque falte la fila 1, se deduce de la primera que exista)
            first = rows[0]
            start = _month_offset(first["fecha"][:10], -(first["index"] - 1))
            # 2) re-fechar
            if "fecha_inicio" in data:
                start = data["fecha_inicio"][:10]
                for r in rows:
                    conn.execute("UPDATE expenses SET fecha = ? WHERE id = ?", (_month_offset(start, r["index"] - 1), r["id"]))
            # 3) número de cuotas
            if "cuotas" in data:
                n_new = int(data["cuotas"])
                max_idx = max(r["index"] for r in rows)
                if n_new < max_idx:
                    conn.execute(f"DELETE FROM expenses WHERE id IN ({qmarks}) AND COALESCE(deferred_index, 1) > ?",
                                 (*ids, n_new))
                elif n_new > max_idx:
                    last = rows[-1]
                    key = "monto_usd" if old_cur == "USD" else "monto_cop"
                    val = data.get("cuota") or last[key]
                    for k in range(max_idx + 1, n_new + 1):
                        fecha = _month_offset(start, k - 1)
                        cop, usd = _amounts_for(conn, fecha, val, new_cur)
                        conn.execute(
                            "INSERT INTO expenses (user_id, user_name, fecha, monto_cop, monto_usd, categoria, nota, "
                            "created_at, metodo_pago, clase_contable, deferred_total, deferred_index, "
                            "deferred_group_id, deferred_mode) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (data.get("user_name", last["user_name"]), fecha, cop, usd,
                             data.get("categoria", last["categoria"]), concepto or last["nota"], now,
                             data.get("metodo_pago", last["metodo_pago"]), last["clase_contable"] or "gasto",
                             n_new, k, gid, data.get("mode", last["mode"])))
                conn.execute("UPDATE expenses SET deferred_total = ?, deferred_group_id = ? "
                             "WHERE COALESCE(deferred_group_id, id) = ? AND (deferred_group_id IS NOT NULL "
                             "OR COALESCE(deferred_total, 1) > 1)", (n_new, gid, gid))
            # 4) valor de la cuota (y/o moneda) desde la cuota `desde`
            rows = _deferred_rows(conn, gid)
            if "cuota" in data:
                desde = int(data.get("desde") or 1)
                for r in rows:
                    if r["index"] >= desde:
                        cop, usd = _amounts_for(conn, r["fecha"], data["cuota"], new_cur)
                        conn.execute("UPDATE expenses SET monto_cop = ?, monto_usd = ? WHERE id = ?", (cop, usd, r["id"]))
            elif "fecha_inicio" in data:
                # misma cuota, otro mes → recalcular la moneda derivada con la TRM nueva
                for r in rows:
                    val = r["monto_usd"] if new_cur == "USD" else r["monto_cop"]
                    cop, usd = _amounts_for(conn, r["fecha"], val, new_cur)
                    conn.execute("UPDATE expenses SET monto_cop = ?, monto_usd = ? WHERE id = ?", (cop, usd, r["id"]))
            conn.execute("COMMIT")
            return {"ok": True, "group": _deferred_view(conn, gid)}
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @api.delete("/api/deferred/{gid}")
    def delete_deferred(gid: int, solo_pendientes: int = 0):
        """Borra el diferido completo, o con ?solo_pendientes=1 solo las
        cuotas que aún no llegan (las de fecha posterior a hoy); las ya
        pasadas se quedan y el total de cuotas pasa a ser las que quedan."""
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            rows = _deferred_rows(conn, gid)
            if not rows:
                raise HTTPException(404, f"diferido {gid} no existe")
            ids = [r["id"] for r in rows]
            qmarks = ",".join("?" * len(ids))
            conn.execute("BEGIN")
            if solo_pendientes:
                today = _deferred_today()
                borrar = [r["id"] for r in rows if r["fecha"][:10] > today]
                quedan = [i for i in ids if i not in borrar]
                if not quedan:
                    raise HTTPException(400, "todas las cuotas son futuras; para eso borra el diferido completo")
                if borrar:
                    conn.execute(f"DELETE FROM expenses WHERE id IN ({','.join('?' * len(borrar))})", borrar)
                    conn.execute(f"UPDATE expenses SET deferred_total = ? WHERE id IN ({','.join('?' * len(quedan))})",
                                 (len(quedan), *quedan))
                conn.execute("COMMIT")
                return {"ok": True, "group_id": gid, "deleted_count": len(borrar), "kept": len(quedan)}
            conn.execute(f"DELETE FROM expenses WHERE id IN ({qmarks})", ids)
            conn.execute("DELETE FROM deferred_meta WHERE group_id = ?", (gid,))
            conn.execute("COMMIT")
            return {"ok": True, "group_id": gid, "deleted_count": len(ids)}
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ──────────────── 🔎 Revisión: tipo de gasto por categoría ────────────────
    @api.patch("/api/categories/{key}/naturaleza")
    def set_naturaleza(key: str, body: NaturalezaBody):
        nat = (body.naturaleza or "").strip().lower()
        if nat not in bot.NATURALEZA_TIPOS:
            raise HTTPException(400, f"naturaleza debe ser una de {list(bot.NATURALEZA_TIPOS)}")
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            row = conn.execute("SELECT value FROM config WHERE key = 'budget'").fetchone()
            budget = json.loads(row[0]) if row else {}
            if key not in budget:
                raise HTTPException(404, f"categoría {key!r} no existe en el presupuesto")
            budget[key]["naturaleza"] = nat
            conn.execute("UPDATE config SET value = ?, updated_at = ? WHERE key = 'budget'",
                         (json.dumps(budget, ensure_ascii=False), datetime.now().isoformat()))
            conn.commit()
        finally:
            conn.close()
        bot.load_config()
        return {"ok": True, "key": key, "naturaleza": nat}

    # ──────────────── 🔎 Revisión: análisis por categoría y tipo de gasto ────────────────
    @api.get("/api/review")
    def review(
        desde: str = Query(..., description="YYYY-MM"),
        hasta: str = Query(..., description="YYYY-MM"),
        persona: str = Query("familia", description="familia | todos | Daniel | Mado | Tuo"),
        sin_viajes: int = 0,
        sin_grandes: int = 0,
        umbral_usd: float = 600.0,
    ):
        """Gasto del período por tipo (esencial/estilo/gusto), por categoría y
        por mes, comparado con el período anterior de igual duración.

        - persona=familia deja por fuera lo de Tuo (persona o categoría).
        - sin_viajes quita la categoría viaje; sin_grandes quita compras
          sueltas ≥ umbral_usd (no cuenta fijos ni cuotas de diferidos).
        Lo excluido se reporta aparte en `excluidos`.
        """
        for v in (desde, hasta):
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v or ""):
                raise HTTPException(400, f"mes inválido {v!r}, se espera YYYY-MM")
        if desde > hasta:
            raise HTTPException(400, "desde debe ser ≤ hasta")
        meses = list(_iter_months(desde, hasta))
        if len(meses) > REVIEW_MAX_MONTHS:
            raise HTTPException(400, f"máximo {REVIEW_MAX_MONTHS} meses")
        p_from, p_to = _review_prev_range(desde, hasta)
        bot.load_config()

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT id, substr(fecha, 1, 10), COALESCE(monto_cop, 0), COALESCE(monto_usd, 0), categoria, "
                "COALESCE(nota, ''), COALESCE(user_name, ''), COALESCE(metodo_pago, ''), recurring_id, deferred_group_id "
                "FROM expenses WHERE substr(fecha, 1, 7) BETWEEN ? AND ?",
                (p_from, hasta),
            ).fetchall()
            # Inicio real de los datos = primer mes con al menos REVIEW_MIN_MES registros
            # (un gasto suelto mal fechado, p.ej. en 2025, no debe parecer historia).
            primer_mes = next((m for m, n in conn.execute(
                "SELECT substr(fecha, 1, 7) AS m, COUNT(*) FROM expenses GROUP BY m ORDER BY m").fetchall()
                if n >= REVIEW_MIN_MES), "")
            datos_desde = (conn.execute("SELECT MIN(substr(fecha, 1, 10)) FROM expenses WHERE substr(fecha, 1, 7) = ?",
                                        (primer_mes,)).fetchone()[0] or "") if primer_mes else ""
        finally:
            conn.close()
        # Un mes cuenta como completo si el bot ya tenía datos desde antes de ese mes

        def incluye_persona(user, cat):
            if persona == "todos":
                return True
            if persona == "familia":
                return user != "Tuo" and cat != "tuo"
            return user == persona

        tipos = list(bot.NATURALEZA_TIPOS) + ["sin_clasificar"]
        excl = {"viajes": {"usd": 0.0, "cop": 0.0, "count": 0},
                "grandes": {"usd": 0.0, "cop": 0.0, "count": 0, "items": []}}
        cur, prev = [], []
        por_dia: dict[str, dict] = {}
        for (eid, fecha, cop, usd, cat, nota, user, met, rec_id, grp_id) in rows:
            if not incluye_persona(user, cat):
                continue
            en_periodo = fecha[:7] >= desde
            if sin_viajes and cat == "viaje":
                if en_periodo:
                    excl["viajes"]["usd"] += usd; excl["viajes"]["cop"] += cop; excl["viajes"]["count"] += 1
                continue
            if sin_grandes and usd >= umbral_usd and not rec_id and not grp_id and bot.naturaleza_de(cat) != "esencial":
                if en_periodo:
                    g = excl["grandes"]
                    g["usd"] += usd; g["cop"] += cop; g["count"] += 1
                    g["items"].append({"id": eid, "fecha": fecha, "categoria": cat, "nota": nota,
                                       "usd": round(usd, 2), "cop": round(cop)})
                continue
            (cur if en_periodo else prev).append((fecha, cop, usd, cat, nota))
            # Día a día (calendario y días de la semana): sin fijos ni cuotas,
            # que caen siempre el mismo día y no dicen nada del hábito.
            if en_periodo and not rec_id and not grp_id:
                dd = por_dia.setdefault(fecha, {"usd": 0.0, "cop": 0.0, "gusto_usd": 0.0, "gusto_cop": 0.0, "n": 0})
                dd["usd"] += usd; dd["cop"] += cop; dd["n"] += 1
                if bot.naturaleza_de(cat) == "gusto":
                    dd["gusto_usd"] += usd; dd["gusto_cop"] += cop

        def vacio():
            return {"usd": 0.0, "cop": 0.0, "count": 0}

        def sumar(acc, cop, usd):
            acc["usd"] += usd; acc["cop"] += cop; acc["count"] += 1

        totales = {"usd": 0.0, "cop": 0.0, "count": 0, "por_tipo": {t: vacio() for t in tipos}}
        totales_prev = {"usd": 0.0, "cop": 0.0, "count": 0, "por_tipo": {t: vacio() for t in tipos}}
        por_mes = {m: {"usd": 0.0, "cop": 0.0, "por_tipo": {t: {"usd": 0.0, "cop": 0.0} for t in tipos}} for m in meses}
        cats: dict[str, dict] = {}

        def cat_entry(cat):
            if cat not in cats:
                info = bot.BUDGET.get(cat) or {}
                cats[cat] = {"key": cat, "label": info.get("label") or cat, "icon": info.get("icon") or "📦",
                             "parent": info.get("parent"), "naturaleza": bot.naturaleza_de(cat),
                             "usd": 0.0, "cop": 0.0, "count": 0, "prev_usd": 0.0, "prev_cop": 0.0, "prev_count": 0,
                             "_dias": set(), "_montos": [], "_notas": {},
                             "serie_usd": {m: 0.0 for m in meses}, "serie_cop": {m: 0.0 for m in meses}}
            return cats[cat]

        for (fecha, cop, usd, cat, nota) in cur:
            t = bot.naturaleza_de(cat)
            sumar(totales, cop, usd); sumar(totales["por_tipo"][t], cop, usd)
            pm = por_mes[fecha[:7]]
            pm["usd"] += usd; pm["cop"] += cop
            pm["por_tipo"][t]["usd"] += usd; pm["por_tipo"][t]["cop"] += cop
            c = cat_entry(cat)
            sumar(c, cop, usd)
            c["_dias"].add(fecha); c["_montos"].append(usd)
            c["serie_usd"][fecha[:7]] += usd; c["serie_cop"][fecha[:7]] += cop
            nk = _review_note_key(nota)
            n = c["_notas"].setdefault(nk, {"nota": nk, "usd": 0.0, "cop": 0.0, "count": 0})
            sumar(n, cop, usd)
        for (fecha, cop, usd, cat, nota) in prev:
            t = bot.naturaleza_de(cat)
            sumar(totales_prev, cop, usd); sumar(totales_prev["por_tipo"][t], cop, usd)
            c = cat_entry(cat)
            c["prev_usd"] += usd; c["prev_cop"] += cop; c["prev_count"] += 1

        hoy = datetime.now(bot.COL_TZ).date()
        ini = datetime.strptime(desde + "-01", "%Y-%m-%d").date()
        hy, hm = int(hasta[:4]), int(hasta[5:7])
        fin = (datetime(hy + (hm == 12), hm % 12 + 1, 1).date() - timedelta(days=1))
        dias_periodo = max(1, (min(fin, hoy) - ini).days + 1)

        def r2(x):
            return round(x, 2)

        out_cats = []
        for c in cats.values():
            montos = sorted(c.pop("_montos"))
            dias = c.pop("_dias")
            notas = sorted(c.pop("_notas").values(), key=lambda x: -x["usd"])[:8]
            mid = len(montos) // 2
            mediana = (montos[mid] if len(montos) % 2 else (montos[mid - 1] + montos[mid]) / 2) if montos else 0.0
            c.update({
                "usd": r2(c["usd"]), "cop": round(c["cop"]), "prev_usd": r2(c["prev_usd"]), "prev_cop": round(c["prev_cop"]),
                "dias": len(dias), "ticket_usd": r2(c["usd"] / c["count"]) if c["count"] else 0.0,
                "mediana_usd": r2(mediana),
                "pct": r2(100 * c["usd"] / totales["usd"]) if totales["usd"] else 0.0,
                "serie_usd": [r2(c["serie_usd"][m]) for m in meses],
                "serie_cop": [round(c["serie_cop"][m]) for m in meses],
                "top_notas": [{**n, "usd": r2(n["usd"]), "cop": round(n["cop"])} for n in notas],
            })
            out_cats.append(c)
        out_cats.sort(key=lambda c: -c["usd"])

        # Rollup por categoría padre
        padres: dict[str, dict] = {}
        for c in out_cats:
            pk = c["parent"] or c["key"]
            info = bot.BUDGET.get(pk) or {}
            p = padres.setdefault(pk, {"key": pk, "label": info.get("label") or pk, "icon": info.get("icon") or "📦",
                                       "naturaleza": bot.naturaleza_de(pk), "usd": 0.0, "cop": 0.0, "count": 0,
                                       "prev_usd": 0.0, "prev_cop": 0.0, "serie_usd": [0.0] * len(meses),
                                       "serie_cop": [0] * len(meses), "hijos": []})
            for f in ("usd", "cop", "count", "prev_usd", "prev_cop"):
                p[f] += c[f]
            p["serie_usd"] = [r2(a + b) for a, b in zip(p["serie_usd"], c["serie_usd"])]
            p["serie_cop"] = [a + b for a, b in zip(p["serie_cop"], c["serie_cop"])]
            p["hijos"].append(c["key"])
        out_padres = sorted(padres.values(), key=lambda p: -p["usd"])
        for p in out_padres:
            p["usd"] = r2(p["usd"]); p["prev_usd"] = r2(p["prev_usd"])
            p["pct"] = r2(100 * p["usd"] / totales["usd"]) if totales["usd"] else 0.0

        def redondear_tot(t):
            t["usd"] = r2(t["usd"]); t["cop"] = round(t["cop"])
            for v in t["por_tipo"].values():
                v["usd"] = r2(v["usd"]); v["cop"] = round(v["cop"])
            return t

        excl["grandes"]["items"].sort(key=lambda x: -x["usd"])
        for k in ("viajes", "grandes"):
            excl[k]["usd"] = r2(excl[k]["usd"]); excl[k]["cop"] = round(excl[k]["cop"])
        return {
            "periodo": {"desde": desde, "hasta": hasta, "meses": meses, "n_meses": len(meses), "dias": dias_periodo,
                        "completo": bool(primer_mes) and desde > primer_mes},
            "anterior": {"desde": p_from, "hasta": p_to, "completo": bool(primer_mes) and p_from > primer_mes},
            "datos_desde": datos_desde,
            "filtros": {"persona": persona, "sin_viajes": bool(sin_viajes), "sin_grandes": bool(sin_grandes),
                        "umbral_usd": umbral_usd},
            "totales": redondear_tot(totales),
            "totales_anterior": redondear_tot(totales_prev),
            "por_mes": [{"mes": m, "usd": r2(v["usd"]), "cop": round(v["cop"]),
                         "por_tipo": {t: {"usd": r2(x["usd"]), "cop": round(x["cop"])} for t, x in v["por_tipo"].items()}}
                        for m, v in por_mes.items()],
            "categorias": out_cats,
            "padres": out_padres,
            "excluidos": excl,
            "por_dia": {f: {"usd": r2(v["usd"]), "cop": round(v["cop"]), "gusto_usd": r2(v["gusto_usd"]),
                            "gusto_cop": round(v["gusto_cop"]), "n": v["n"]} for f, v in sorted(por_dia.items())},
            "presupuesto_mes": {"usd": r2(float(bot.TOTAL_BUDGET_USD or 0)),
                                "cop": round(float(bot.TOTAL_BUDGET_USD or 0) * float(bot.TRM or 0))},
        }

    # ──────────────── ➕ Gasto rápido desde el dashboard ────────────────
    @api.post("/api/expenses/quick")
    def quick_expense(body: QuickExpense, request: Request):
        """Anota un gasto en cualquier moneda del bot (COP, USD, BOB, AED, CLP,
        ARS). Convierte con las tasas del mes de la fecha (igual que Telegram).
        Si no viene persona, usa la que entró con su PIN."""
        moneda = (body.moneda or "COP").upper()
        if moneda not in _SUPPORTED_CURRENCIES:
            raise HTTPException(400, f"moneda debe ser una de {list(_SUPPORTED_CURRENCIES)}")
        if not body.monto or body.monto <= 0:
            raise HTTPException(400, "el monto debe ser mayor que 0")
        if not (body.categoria or "").strip():
            raise HTTPException(400, "elige una categoría")
        fecha = (body.fecha or datetime.now(bot.COL_TZ).strftime("%Y-%m-%d"))[:10]
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"fecha inválida {fecha!r}, se espera YYYY-MM-DD")
        quien = (body.user_name or "").strip() or (_auth_request_user(request) or "Daniel")
        if quien in ("api", "dashboard"):
            quien = "Daniel"
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            rates = _rates_for_period(conn, fecha[:7])
            monto_usd, _ = _to_usd(float(body.monto), moneda, rates)
            monto_cop = float(body.monto) if moneda == "COP" else float(round(monto_usd * float(rates.get("TRM") or bot.TRM)))
            nota = (body.nota or "").strip()
            cur = conn.execute(
                "INSERT INTO expenses (user_id, user_name, fecha, monto_cop, monto_usd, categoria, nota, created_at, "
                "metodo_pago, clase_contable, deferred_total, deferred_index) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'gasto', 1, 1)",
                (quien, fecha, monto_cop, monto_usd, body.categoria.strip(), nota, datetime.now().isoformat(),
                 (body.metodo_pago or "Sin especificar").strip()),
            )
            conn.commit()
            new_id = cur.lastrowid
        finally:
            conn.close()
        print(f"[quick] gasto #{new_id} {quien} {body.monto} {moneda} {body.categoria}")
        return {"ok": True, "id": new_id, "user_name": quien, "fecha": fecha, "monto_cop": monto_cop,
                "monto_usd": monto_usd, "categoria": body.categoria.strip(), "nota": nota, "moneda": moneda}

    # ──────────────── 🧭 Recorrido guiado: quién ya lo vio ────────────────
    def _tour_user(request: Request) -> str:
        u = _auth_request_user(request)
        return u if u and u != "api" else "dashboard"

    def _tour_state(conn) -> dict:
        row = conn.execute("SELECT value FROM config WHERE key = 'tour_visto'").fetchone()
        try:
            return json.loads(row[0]) if row else {}
        except Exception:
            return {}

    @api.get("/api/tour")
    def tour_status(request: Request):
        user = _tour_user(request)
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            visto = _tour_state(conn).get(user)
        finally:
            conn.close()
        return {"user": user, "version": TOUR_VERSION, "visto": visto == TOUR_VERSION}

    @api.post("/api/tour/visto")
    def tour_seen(request: Request):
        user = _tour_user(request)
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            st = _tour_state(conn)
            st[user] = TOUR_VERSION
            conn.execute("INSERT INTO config (key, value, updated_at) VALUES ('tour_visto', ?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                         (json.dumps(st), datetime.now().isoformat()))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "user": user, "version": TOUR_VERSION}

    # ──────────────── Static frontend ────────────────
    # Serve index.html at / and any other static assets under /static/*.
    # Keeping these AFTER the /api/* routes above ensures they don't shadow
    # the JSON API.
    index_file = STATIC_DIR / "index.html"

    @api.get("/", include_in_schema=False)
    def root():
        if not index_file.exists():
            raise HTTPException(500, f"index.html missing at {index_file}")
        # no-cache = el navegador SIEMPRE pregunta antes de usar su copia (304 si
        # no cambió). Sin esto, podía mostrar un dashboard viejo guardado sin
        # pasar por la pantalla del PIN y la API respondía 401.
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    # /favicon.ico is served inline as a data URI in index.html, but browsers
    # still hit this path — return 204 so we don't spam logs with 404s.
    # Ojo: 204 debe ir SIN cuerpo. JSONResponse(None) manda b"null" y uvicorn
    # lanza "Response content longer than Content-Length" en cada visita.
    @api.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    if STATIC_DIR.exists():
        api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return api
