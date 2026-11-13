"""Tarjetas (Daniel 12–13 nov 2026): pilas por banco, calendario de pagos, fijos por confirmar,
abonos y suscripciones con «desde cuándo».

Reglas que salen del diseño aprobado:
- Lo usado de cada tarjeta = último dato del banco (lectura) + gastos anotados con sus métodos
  después de ese día − abonos después de ese día. Un dato nuevo del banco manda.
- Las tarjetas «respaldadas» (la ·0757 de Mado cuelga de la ·0312) tienen saldo propio, pero su cupo
  no se suma. Las amparadas (·1034, la Wio y la ENBD de Mado) no son tarjetas aparte: sus métodos
  suman en la tarjeta de Daniel y salen como «consumos Mado».
- Hasta noviembre de 2026 los fijos los anotó el bot el día 1. Desde el período FIJOS_CONFIRMAR_DESDE
  el bot ya no los anota: cada fijo entra al P&G cuando se confirma («✓ Pasó»), una sola vez por mes.
- Tarjetas y Patrimonio van separados: un abono solo cambia la tarjeta.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

FIJOS_CONFIRMAR_DESDE = "2026-12"

# id, banco, nombre, últimos, dueño, moneda, cupo, respalda, métodos, corte, pago, recordatorio (label), color, orden
_SEMILLA = [
    ("0312", "Banco de Bogotá", "Visa LATAM", "0312", "Daniel", "COP", 26560000, None, ["BDB Visa Latam Dani"], 25, 17, "BDB Visa Latam Dani", "#1d4fa3", 10),
    ("8298", "Banco de Bogotá", "Mastercard Black", "8298", "Daniel", "COP", 40000000, None, ["BDB MC Dani"], 25, 17, "BDB MC Dani", "#3a3f4b", 11),
    ("0757", "Banco de Bogotá", "Visa LATAM", "0757", "Mado", "COP", 25000000, "0312", ["BDB Visa Latam Mado"], None, 9, "BDB Visa Latam Mado", "#6d28d9", 12),
    ("6977", "BBVA", "Mastercard Black", "6977", "Daniel", "COP", 20000000, None, ["BBVA MC Dani", "BBVA MC Mado"], 30, 15, None, "#0c2c5c", 20),
    ("5740", "BBVA", "Mastercard Black", "5740", "Daniel", "COP", 50000000, None, ["BBVA MC 5740 Dani"], 30, 15, None, "#0f2f66", 21),
    ("7857", "BBVA", "Visa Infinite", "7857", "Daniel", "COP", 40000000, None, ["BBVA Visa Infinite 7857 Dani"], 30, 15, None, "#1e293b", 22),
    ("3047", "Falabella", "Mastercard", "3047", "Daniel", "COP", 9200000, None, ["Falabella Mastercard Dani"], 29, 15, None, "#2f6b3a", 30),
    ("3810", "RappiCard", "Visa", "3810", "Daniel", "COP", 21499719, None, ["RappiCard Dani"], 3, None, None, "#c2410c", 40),
    ("wio", "Wio", "Mastercard", "5807", "Daniel", "AED", 53000, None, ["WIO MC Dani", "WIO MC Mado"], None, 28, None, "#4c1d95", 50),
    ("enbd", "Emirates NBD", "Skywards Infinite", "9204", "Daniel", "AED", 31500, None, ["ENBD Visa AED", "ENBD Visa AED Mado"], None, None, None, "#1f2937", 60),
    ("sapphire", "Chase", "Sapphire Preferred", "1062", "Mado", "USD", 9800, None, ["Chase Sapphire Mado"], 22, 19, None, "#1c3f73", 70),
    ("freedom", "Chase", "Freedom Unlimited", "6289", "Mado", "USD", 5400, None, ["Chase Freedom Mado"], 4, 1, None, "#1f6fc0", 71),
    ("pichincha", "Banco Pichincha", "Tarjeta de crédito", "", "Mado", "USD", 7000, None, ["Pichincha Tarjeta Mado"], None, None, None, "#12305f", 80),
]
# Último dato del banco: video del 12 nov (apps de cada banco) y extractos de Chase
_LECTURAS = [
    ("0312", "2026-11-12T17:16", 14400672, "video 12 nov"), ("0757", "2026-11-12T17:16", 1009882, "video 12 nov"),
    ("8298", "2026-11-12T17:16", 124007, "video 12 nov"), ("6977", "2026-11-12T17:14", 26579, "video 12 nov"),
    ("5740", "2026-11-12T17:14", 0, "video 12 nov"), ("7857", "2026-11-12T17:14", 0, "video 12 nov"),
    ("3047", "2026-11-12T17:17", 178300, "video 12 nov"), ("3810", "2026-11-12T17:19", 46300, "video 12 nov"),
    ("wio", "2026-11-12T17:18", 99.63, "video 12 nov"), ("enbd", "2026-11-12T17:17", 0, "video 12 nov"),
    ("sapphire", "2026-10-22T23:59", 350.89, "extracto 22 oct"), ("freedom", "2026-11-04T23:59", 241.66, "extracto 4 nov"),
]
# Día de cobro de cada fijo (extractos de octubre de BdB y Falabella; Chase de octubre)
_DIAS = [("Claude", 6), ("iCloud", 2), ("Uber Pro", 16), ("Rappi Pro", 13), ("YouTube", 14), ("HBO", 11), ("Netflix", 17),
         ("Wispr", 8), ("Tigo línea Daniel (día 3)", 3), ("Tigo línea Daniel 2", 13), ("Tigo internet", 13), ("Oura Daniel", 16),
         ("Oura Mado", 16), ("Chubb", 2), ("Cuota manejo + seguro BdB Visa", 25), ("Cuota manejo + seguro BdB MC", 25),
         ("Cuota manejo Falabella", 29), ("Clubes Falabella", 2), ("Google Workspace Tuo", 1), ("Slack", 13), ("1Password", 16),
         ("Spotify", 10), ("Google Workspace Hypero", 1), ("Wio ·", 1), ("Hipoteca", 28)]


def preparar(conn):
    """Tablas y semillas. Idempotente: no pisa lo que ya exista."""
    conn.execute("""CREATE TABLE IF NOT EXISTS tarjetas (
        id TEXT PRIMARY KEY, banco TEXT NOT NULL, nombre TEXT NOT NULL, ultimos TEXT, duenio TEXT NOT NULL DEFAULT 'Daniel',
        moneda TEXT NOT NULL DEFAULT 'COP', cupo REAL, respalda TEXT, metodos TEXT NOT NULL DEFAULT '[]',
        corte_dia INTEGER, pago_dia INTEGER, recordatorio_id INTEGER, color TEXT, orden INTEGER DEFAULT 100,
        activa INTEGER NOT NULL DEFAULT 1, nota TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tarjeta_lecturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tarjeta_id TEXT NOT NULL, fecha TEXT NOT NULL, usado REAL NOT NULL,
        fuente TEXT, nota TEXT, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tarjeta_abonos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tarjeta_id TEXT NOT NULL, fecha TEXT NOT NULL, monto REAL NOT NULL,
        nota TEXT, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS fijo_confirmaciones (
        recurring_id INTEGER NOT NULL, period TEXT NOT NULL, estado TEXT NOT NULL, expense_id INTEGER,
        creado_aqui INTEGER NOT NULL DEFAULT 0, monto REAL, fuente TEXT, at TEXT NOT NULL,
        PRIMARY KEY (recurring_id, period))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS recurring_versiones (
        recurring_id INTEGER NOT NULL, desde TEXT NOT NULL, monto_cop REAL, monto_usd REAL, currency TEXT,
        metodo_pago TEXT, active INTEGER, dia_cobro INTEGER, at TEXT NOT NULL, PRIMARY KEY (recurring_id, desde))""")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recurring_expenses)")]
    if "dia_cobro" not in cols:
        conn.execute("ALTER TABLE recurring_expenses ADD COLUMN dia_cobro INTEGER")
    now = _ahora_utc()
    if not conn.execute("SELECT 1 FROM tarjetas LIMIT 1").fetchone():
        for (tid, banco, nombre, ult, duenio, mon, cupo, resp, mets, corte, pago, rec, color, orden) in _SEMILLA:
            rid = None
            if rec:
                r = conn.execute("SELECT id FROM payment_reminders WHERE label = ?", (rec,)).fetchone()
                rid = r[0] if r else None
            conn.execute("INSERT INTO tarjetas (id, banco, nombre, ultimos, duenio, moneda, cupo, respalda, metodos, corte_dia, "
                         "pago_dia, recordatorio_id, color, orden, activa, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                         (tid, banco, nombre, ult, duenio, mon, cupo, resp, json.dumps(mets, ensure_ascii=False), corte, pago,
                          rid, color, orden, now))
        for tid, fecha, usado, fuente in _LECTURAS:
            conn.execute("INSERT INTO tarjeta_lecturas (tarjeta_id, fecha, usado, fuente, created_at) VALUES (?,?,?,?,?)",
                         (tid, fecha, usado, fuente, now))
    for pista, dia in _DIAS:
        conn.execute("UPDATE recurring_expenses SET dia_cobro = ? WHERE dia_cobro IS NULL AND label LIKE ?", (dia, f"%{pista}%"))
    # Spotify de Mado se paga con su Chase de débito (Daniel, 13 nov)
    conn.execute("UPDATE recurring_expenses SET metodo_pago = 'Chase Debito Mado' WHERE label LIKE 'Spotify%' "
                 "AND metodo_pago = 'BDB Visa Latam Mado'")
    conn.commit()


# ───────────── utilidades ─────────────
_COL = timezone(timedelta(hours=-5))


def _ahora():
    """Hora de Colombia sin zona (así se guardan las fechas que ve Daniel)."""
    return datetime.now(_COL).replace(tzinfo=None)


def _ahora_utc():
    """created_at de la base va en UTC sin zona (como lo guardan el bot y la API)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _hoy():
    return _ahora().date()


def _lectura_utc(fecha):
    """La hora del dato del banco (Colombia) llevada a UTC para compararla con created_at."""
    if not fecha or len(fecha) <= 10:
        return fecha or ""
    try:
        return (datetime.fromisoformat(fecha[:16]) + timedelta(hours=5)).isoformat()
    except ValueError:
        return fecha


def _recordatorio_de(conn, tid):
    """La fecha de pago de la tarjeta: la ligada a mano o una de «Próximos pagos» con el nombre de uno de sus métodos."""
    t = conn.execute("SELECT recordatorio_id, metodos FROM tarjetas WHERE id = ?", (tid,)).fetchone()
    if not t:
        return None
    if t[0]:
        return t[0]
    mets = [m.lower() for m in json.loads(t[1] or "[]")]
    for rid, label in conn.execute("SELECT id, label FROM payment_reminders WHERE active = 1"):
        if (label or "").strip().lower() in mets:
            return rid
    return None


def _tarjeta_de_recordatorio(conn, rid):
    for (tid,) in conn.execute("SELECT id FROM tarjetas WHERE activa = 1"):
        if _recordatorio_de(conn, tid) == rid:
            return tid
    return None


def _ciclo_actual(due_day, ref):
    """El pago en curso con la misma regla de «Próximos pagos»: el de este mes hasta 5 días después de vencer; luego el siguiente."""
    per = _ym(ref)
    return per if (_fecha_en(per, due_day) - ref).days >= -5 else _sumar_mes(per, 1)


def _periodo_a_pagar(due_day, pagados, ref):
    """El próximo pago sin pagar desde el ciclo en curso (si el de este mes venció hace 5 días o menos, sale ese, en rojo)."""
    per = _ciclo_actual(due_day, ref)
    for k in (0, 1, 2):
        p2 = _sumar_mes(per, k)
        if p2 not in pagados:
            return p2, _fecha_en(p2, due_day)
    return None, None


def _ym(d):
    return f"{d.year:04d}-{d.month:02d}"


def _sumar_mes(ym, k):
    y, m = map(int, ym.split("-"))
    t = y * 12 + (m - 1) + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _fecha_en(ym, dia):
    y, m = map(int, ym.split("-"))
    ultimo = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(int(dia), ultimo))


def _tasas(bot):
    return {"COP": float(bot.TRM or 0) or 1.0, "AED": float(bot.AED_RATE or 3.6725), "USD": 1.0}


def _en_moneda(monto_cop, monto_usd, moneda, tasas):
    if moneda == "COP":
        return float(monto_cop or 0)
    usd = float(monto_usd or 0)
    return usd * tasas["AED"] if moneda == "AED" else usd


def _a_usd(valor, moneda, tasas):
    if valor is None:
        return None
    return valor / tasas["COP"] if moneda == "COP" else valor / tasas["AED"] if moneda == "AED" else valor


def _filas(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.row_factory = None


# ───────────── tarjetas ─────────────
def estado(conn, bot):
    """Cada tarjeta con lo usado recalculado y los totales sin duplicar (en su moneda y en dólares)."""
    tasas = _tasas(bot)
    hoy = _hoy()
    tarjetas = _filas(conn, "SELECT * FROM tarjetas WHERE activa = 1 ORDER BY orden, id")
    recordatorios = {r["id"]: r for r in _filas(conn, "SELECT id, label, due_day, amount, currency, active FROM payment_reminders")}
    por_label = {(r["label"] or "").strip().lower(): r["id"] for r in recordatorios.values() if r["active"]}
    pagados = {}
    for r in _filas(conn, "SELECT reminder_id, period FROM payment_reminder_paid"):
        pagados.setdefault(r["reminder_id"], set()).add(r["period"])
    out = []
    for t in tarjetas:
        metodos = json.loads(t["metodos"] or "[]")
        if not t["recordatorio_id"]:   # una fecha de pago nueva en «Próximos pagos» con el nombre del método queda ligada sola
            t["recordatorio_id"] = next((por_label[m.lower()] for m in metodos if m.lower() in por_label), None)
        lec = _filas(conn, "SELECT fecha, usado, fuente FROM tarjeta_lecturas WHERE tarjeta_id = ? ORDER BY fecha DESC, id DESC LIMIT 1",
                     (t["id"],))
        lec = lec[0] if lec else None
        desde = lec["fecha"][:10] if lec else "0000-00-00"
        marcas = ",".join("?" * len(metodos)) or "''"
        # Compras sueltas después del dato del banco (hasta hoy). Las diferidas cuentan enteras si la compra (su primera
        # cuota) es posterior al dato: el banco ya trae completas las de antes, aunque tengan cuotas en meses futuros.
        lec_ts = _lectura_utc(lec["fecha"]) if lec else ""
        gastos = _filas(conn, f"SELECT metodo_pago, monto_cop, monto_usd FROM expenses WHERE deferred_group_id IS NULL "
                              f"AND (fecha > ? OR (fecha = ? AND created_at > ?)) AND fecha <= ? AND metodo_pago IN ({marcas})",
                        (desde, desde, lec_ts, hoy.isoformat(), *metodos)) if metodos else []
        if metodos:
            gastos += _filas(conn, f"SELECT e.metodo_pago, e.monto_cop, e.monto_usd FROM expenses e JOIN (SELECT deferred_group_id g, "
                                   f"MIN(fecha) ini FROM expenses WHERE deferred_group_id IS NOT NULL GROUP BY deferred_group_id) d "
                                   f"ON d.g = e.deferred_group_id WHERE d.ini > ? AND d.ini <= ? AND e.metodo_pago IN ({marcas})",
                             (desde, hoy.isoformat(), *metodos))
        sumado = sum(_en_moneda(g["monto_cop"], g["monto_usd"], t["moneda"], tasas) for g in gastos)
        de_mado = sum(_en_moneda(g["monto_cop"], g["monto_usd"], t["moneda"], tasas) for g in gastos
                      if (g["metodo_pago"] or "").endswith(" Mado"))
        abonos = conn.execute("SELECT COALESCE(SUM(monto), 0) FROM tarjeta_abonos WHERE tarjeta_id = ? "
                              "AND (fecha > ? OR (fecha = ? AND created_at > ?))", (t["id"], desde, desde, lec_ts)).fetchone()[0]
        usado = None if lec is None else max(float(lec["usado"]) + sumado - float(abonos or 0), 0.0)
        libre = None if (usado is None or t["cupo"] is None) else float(t["cupo"]) - usado
        # Consumos de Mado: su tarjeta respaldada entera, o lo anotado con métodos « Mado» en las amparadas
        consumos_mado = usado if t["duenio"] == "Mado" else de_mado
        prox = None
        rec = recordatorios.get(t["recordatorio_id"]) if t["recordatorio_id"] else None
        dia_pago = (rec or {}).get("due_day") or t["pago_dia"]
        if dia_pago and rec:   # el pago con fecha y estado sale de «Próximos pagos»; sin recordatorio solo se dice el día
            per, f = _periodo_a_pagar(dia_pago, pagados.get(t["recordatorio_id"], set()), hoy)
            if per:   # si el de este mes ya venció y no se ha pagado, sale ese (vencido) antes que el siguiente
                prox = {"fecha": f.isoformat(), "periodo": per, "dias": (f - hoy).days, "vencido": f < hoy,
                        "ciclo_actual": per == _ciclo_actual(dia_pago, hoy),   # solo este lo deja hecho un abono
                        "monto": (rec or {}).get("amount"), "recordatorio_id": t["recordatorio_id"]}
        out.append({**t, "metodos": metodos, "lectura": lec, "sumado_desde_lectura": round(sumado, 2),
                    "abonos_desde_lectura": float(abonos or 0), "usado": None if usado is None else round(usado, 2),
                    "libre": None if libre is None else round(libre, 2),
                    "consumos_mado": None if consumos_mado is None else round(consumos_mado, 2), "proximo_pago": prox,
                    "dia_pago": dia_pago})
    totales = {}
    for t in out:
        m = totales.setdefault(t["moneda"], {"cupo": 0.0, "usado": 0.0, "consumos_mado": 0.0, "sin_dato": 0, "cupo_sin_dato": 0.0})
        if not t["respalda"] and t["cupo"] is not None:
            m["cupo"] += float(t["cupo"])
        if t["usado"] is None:
            m["sin_dato"] += 1
            if not t["respalda"] and t["cupo"] is not None:
                m["cupo_sin_dato"] += float(t["cupo"])
        else:
            m["usado"] += t["usado"]
            m["consumos_mado"] += t["consumos_mado"] or 0
    usd = {"cupo": 0.0, "usado": 0.0, "consumos_mado": 0.0, "cupo_sin_dato": 0.0}
    for mon, m in totales.items():
        m["libre"] = m["cupo"] - m["cupo_sin_dato"] - m["usado"]   # sin dato del banco no se cuenta como libre
        for k in usd:
            usd[k] += _a_usd(m[k], mon, tasas)
    usd["libre"] = usd["cupo"] - usd["cupo_sin_dato"] - usd["usado"]
    return {"tarjetas": out, "totales": totales, "en_dolares": {k: round(v, 2) for k, v in usd.items()},
            "tasas": {"COP": tasas["COP"], "AED": tasas["AED"]}, "hoy": hoy.isoformat()}


def poner_lectura(conn, tid, usado, fecha=None, fuente="tú", nota=None):
    if not conn.execute("SELECT 1 FROM tarjetas WHERE id = ?", (tid,)).fetchone():
        raise KeyError(tid)
    conn.execute("INSERT INTO tarjeta_lecturas (tarjeta_id, fecha, usado, fuente, nota, created_at) VALUES (?,?,?,?,?,?)",
                 (tid, fecha or _ahora().strftime("%Y-%m-%dT%H:%M"), float(usado), fuente, nota, _ahora_utc()))
    conn.commit()


def abonar(conn, tid, monto, fecha=None, nota=None):
    if not conn.execute("SELECT 1 FROM tarjetas WHERE id = ?", (tid,)).fetchone():
        raise KeyError(tid)
    t = (_recordatorio_de(conn, tid),)
    if float(monto) <= 0:
        raise ValueError("el abono debe ser mayor que cero")
    f = fecha or _hoy().isoformat()
    marcado = None
    if t[0]:   # el pago del recordatorio queda hecho si el abono cubre lo que pide (o si no tiene monto)
        rec = conn.execute("SELECT due_day, amount FROM payment_reminders WHERE id = ?", (t[0],)).fetchone()
        if rec and rec[0]:
            pagados = {r[0] for r in conn.execute("SELECT period FROM payment_reminder_paid WHERE reminder_id = ?", (t[0],))}
            per = _ciclo_actual(int(rec[0]), date.fromisoformat(f))
            if per not in pagados and (rec[1] is None or float(monto) >= 0.98 * float(rec[1])):
                conn.execute("INSERT OR REPLACE INTO payment_reminder_paid (reminder_id, period, paid_at) VALUES (?,?,?)",
                             (t[0], per, _ahora_utc()))
                marcado = per
    cur = conn.execute("INSERT INTO tarjeta_abonos (tarjeta_id, fecha, monto, nota, created_at, periodo_marcado) VALUES (?,?,?,?,?,?)",
                       (tid, f, float(monto), nota, _ahora_utc(), marcado))
    conn.commit()
    return {"id": cur.lastrowid, "recordatorio_pagado": marcado}


def borrar_abono(conn, aid):
    """Quita el abono; si ese abono dejó un pago como hecho, el pago vuelve a quedar pendiente."""
    f0 = conn.execute("SELECT periodo_marcado, tarjeta_id FROM tarjeta_abonos WHERE id = ?", (aid,)).fetchone()
    if not f0:
        raise KeyError(aid)
    fila = (f0[0], _recordatorio_de(conn, f0[1]))
    conn.execute("DELETE FROM tarjeta_abonos WHERE id = ?", (aid,))
    if fila[0] and fila[1]:
        otro = conn.execute("SELECT 1 FROM tarjeta_abonos WHERE tarjeta_id = ? AND periodo_marcado = ?", (f0[1], fila[0])).fetchone()
        if not otro:
            conn.execute("DELETE FROM payment_reminder_paid WHERE reminder_id = ? AND period = ?", (fila[1], fila[0]))
    conn.commit()
    return {"ok": True, "pago_reabierto": fila[0] if fila[0] and fila[1] else None}


# ───────────── fijos: versiones y confirmaciones ─────────────
def version_de(conn, rid, periodo):
    """Valores del fijo vigentes en el período (lo cambiado «desde» un mes no toca los anteriores)."""
    base = _filas(conn, "SELECT id, label, monto_cop, monto_usd, COALESCE(currency, 'COP') AS currency, metodo_pago, active, "
                        "dia_cobro, categoria, user_name FROM recurring_expenses WHERE id = ?", (rid,))
    if not base:
        return None
    v = dict(base[0])
    ver = _filas(conn, "SELECT * FROM recurring_versiones WHERE recurring_id = ? AND desde <= ? ORDER BY desde DESC LIMIT 1",
                 (rid, periodo))
    if ver:
        for k in ("monto_cop", "monto_usd", "currency", "metodo_pago", "active", "dia_cobro"):
            if ver[0][k] is not None:
                v[k] = ver[0][k]
    return v


def cambiar_fijo(conn, rid, cambios, desde):
    """Cambia valor, moneda, tarjeta, día o activo desde un mes. Guarda la versión anterior la primera vez."""
    actual = version_de(conn, rid, "9999-12")
    if actual is None:
        raise KeyError(rid)
    now = _ahora_utc()
    if not conn.execute("SELECT 1 FROM recurring_versiones WHERE recurring_id = ?", (rid,)).fetchone():
        conn.execute("INSERT INTO recurring_versiones (recurring_id, desde, monto_cop, monto_usd, currency, metodo_pago, active, "
                     "dia_cobro, at) VALUES (?, '2000-01', ?, ?, ?, ?, ?, ?, ?)",
                     (rid, actual["monto_cop"], actual["monto_usd"], actual["currency"], actual["metodo_pago"], actual["active"],
                      actual["dia_cobro"], now))
    nuevo = {**actual, **{k: v for k, v in cambios.items() if v is not None}}
    conn.execute("INSERT OR REPLACE INTO recurring_versiones (recurring_id, desde, monto_cop, monto_usd, currency, metodo_pago, "
                 "active, dia_cobro, at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (rid, desde, nuevo["monto_cop"], nuevo["monto_usd"], nuevo["currency"], nuevo["metodo_pago"], nuevo["active"],
                  nuevo["dia_cobro"], now))
    # La fila base refleja lo vigente hoy (la usan el bot y lo que ya existía)
    hoy_v = version_de(conn, rid, _ym(_hoy()))
    conn.execute("UPDATE recurring_expenses SET monto_cop = ?, monto_usd = ?, currency = ?, metodo_pago = ?, active = ?, "
                 "dia_cobro = ?, updated_at = ? WHERE id = ?",
                 (hoy_v["monto_cop"], hoy_v["monto_usd"], hoy_v["currency"], hoy_v["metodo_pago"], hoy_v["active"],
                  hoy_v["dia_cobro"], now, rid))
    conn.commit()
    return version_de(conn, rid, desde)


def _gasto_parecido(conn, v, periodo, ref, es_usd):
    """Un gasto anotado a mano en el mes, de la misma categoría y valor parecido (±15 %), que ningún otro fijo haya tomado.
    Primero el del mismo método de pago y el de valor más cercano (Tigo ×2, Oura ×2 no se cruzan)."""
    cands = []
    for e_id, e_cop, e_usd, e_met in conn.execute(
            "SELECT id, monto_cop, monto_usd, metodo_pago FROM expenses WHERE recurring_id IS NULL AND categoria = ? "
            "AND substr(fecha, 1, 7) = ? AND id NOT IN (SELECT expense_id FROM fijo_confirmaciones WHERE expense_id IS NOT NULL)",
            (v["categoria"], periodo)).fetchall():
        dif = abs(float((e_usd if es_usd else e_cop) or 0) - ref)
        if dif <= 0.15 * ref:
            cands.append((0 if (e_met or "") == (v["metodo_pago"] or "") else 1, dif, e_id))
    return sorted(cands)[0][2] if cands else None


def confirmar(conn, bot, rid, periodo, estado_, monto=None, fuente="tú"):
    """«✓ Pasó» crea el gasto del fijo en ese mes, una sola vez; «No pasó» no anota nada. Si ya hay un gasto de ese fijo
    en el mes (anotado a mano o por el bot), se une a ese."""
    if estado_ not in ("paso", "no_paso"):
        raise ValueError("estado debe ser 'paso' o 'no_paso'")
    if monto is not None and float(monto) <= 0:
        raise ValueError("el valor debe ser mayor que cero")
    if periodo > _ym(_hoy()):
        raise ValueError("ese mes todavía no llega")
    previo = conn.execute("SELECT estado, expense_id FROM fijo_confirmaciones WHERE recurring_id = ? AND period = ?",
                          (rid, periodo)).fetchone()
    if previo:
        return {"ya": True, "estado": previo[0], "expense_id": previo[1]}
    v = version_de(conn, rid, periodo)
    if v is None:
        raise KeyError(rid)
    now = _ahora_utc()
    exp_id, creado, unido = None, 0, None
    if estado_ == "paso":
        ya = conn.execute("SELECT id FROM expenses WHERE recurring_id = ? AND substr(fecha, 1, 7) = ? ORDER BY id LIMIT 1",
                          (rid, periodo)).fetchone()
        es_usd = v["currency"] == "USD"
        trm = bot._trm_for_period(conn, periodo)
        if monto is not None:
            m_usd = round(float(monto), 2) if es_usd else round(float(monto) / trm, 2)
            m_cop = float(round(float(monto) * trm)) if es_usd else float(monto)
        else:
            m_usd = round(float(v["monto_usd"] or 0), 2) if es_usd else round(float(v["monto_cop"] or 0) / trm, 2)
            m_cop = float(round(float(v["monto_usd"] or 0) * trm)) if es_usd else float(v["monto_cop"] or 0)
        ref = m_usd if es_usd else m_cop
        if not ya and ref > 0:
            gid = _gasto_parecido(conn, v, periodo, ref, es_usd)
            if gid:
                ya = (gid,)
                creado = 2   # unido a un gasto anotado a mano: se le pone el fijo para que nadie más lo tome
                conn.execute("UPDATE expenses SET recurring_id = ? WHERE id = ? AND recurring_id IS NULL", (rid, ya[0]))
        if ya:
            exp_id = ya[0]
            if monto is not None:   # «llegó con otro valor»: el gasto al que se une queda con ese valor
                conn.execute("UPDATE expenses SET monto_cop = ?, monto_usd = ? WHERE id = ?", (m_cop, m_usd, exp_id))
            fila = conn.execute("SELECT fecha, nota, monto_cop, monto_usd FROM expenses WHERE id = ?", (exp_id,)).fetchone()
            if fila:
                unido = {"id": exp_id, "fecha": fila[0], "nota": fila[1], "monto_cop": fila[2], "monto_usd": fila[3]}
        else:
            fecha = _fecha_en(periodo, v["dia_cobro"] or 1).isoformat()
            cur = conn.execute(
                "INSERT INTO expenses (user_name, fecha, monto_cop, monto_usd, categoria, nota, created_at, metodo_pago, "
                "clase_contable, recurring_id, tasa_fija) VALUES (?,?,?,?,?,?,?,?, 'gasto', ?, ?)",
                (v["user_name"] or "Daniel", fecha, m_cop, m_usd, v["categoria"], (v["label"] or v["categoria"]) + " (fijo confirmado)",
                 now, v["metodo_pago"] or "Sin especificar", rid, 1 if es_usd else 0))
            exp_id, creado = cur.lastrowid, 1
        conn.execute("INSERT OR IGNORE INTO recurring_postings (recurring_id, period, expense_id, posted_at) VALUES (?,?,?,?)",
                     (rid, periodo, exp_id, now))
    else:
        conn.execute("INSERT OR IGNORE INTO recurring_postings (recurring_id, period, expense_id, posted_at) VALUES (?,?,NULL,?)",
                     (rid, periodo, now))
    conn.execute("INSERT INTO fijo_confirmaciones (recurring_id, period, estado, expense_id, creado_aqui, monto, fuente, at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (rid, periodo, estado_, exp_id, creado, monto, fuente, now))
    if estado_ == "paso" and fuente != "próximos pagos":   # el mismo pago en «Próximos pagos» queda hecho (una sola acción)
        r = conn.execute("SELECT id FROM payment_reminders WHERE lower(label) = lower(?) AND active = 1", (v["label"] or "",)).fetchone()
        if r:
            cur_p = conn.execute("INSERT OR IGNORE INTO payment_reminder_paid (reminder_id, period, paid_at) VALUES (?,?,?)", (r[0], periodo, now))
            if cur_p.rowcount:
                conn.execute("UPDATE fijo_confirmaciones SET marco_pago = ? WHERE recurring_id = ? AND period = ?", (r[0], rid, periodo))
    conn.commit()
    return {"ya": False, "estado": estado_, "expense_id": exp_id, "creado": creado == 1, "unido": unido}


def corregir(conn, bot, rid, periodo, monto):
    """«Corregir valor»: cambia el monto del gasto de ese fijo en ese mes (el creado o el unido), en la moneda del fijo."""
    if float(monto) <= 0:
        raise ValueError("el valor debe ser mayor que cero")
    fila = conn.execute("SELECT estado, expense_id FROM fijo_confirmaciones WHERE recurring_id = ? AND period = ?", (rid, periodo)).fetchone()
    if not fila or fila[0] != "paso" or not fila[1]:
        raise ValueError("ese fijo no está confirmado en ese mes")
    v = version_de(conn, rid, periodo)
    es_usd = v["currency"] == "USD"
    trm = bot._trm_for_period(conn, periodo)
    m_usd = round(float(monto), 2) if es_usd else round(float(monto) / trm, 2)
    m_cop = float(round(float(monto) * trm)) if es_usd else float(round(float(monto)))
    conn.execute("UPDATE expenses SET monto_cop = ?, monto_usd = ?, tasa_fija = ? WHERE id = ?", (m_cop, m_usd, 1 if es_usd else 0, fila[1]))
    conn.execute("UPDATE fijo_confirmaciones SET monto = ? WHERE recurring_id = ? AND period = ?", (float(monto), rid, periodo))
    conn.commit()
    return {"ok": True, "expense_id": fila[1], "monto_cop": m_cop, "monto_usd": m_usd}


def deshacer(conn, rid, periodo):
    """Quita la confirmación; si el gasto lo creó la confirmación, lo borra (si se unió a uno existente, ese se queda)."""
    fila = conn.execute("SELECT expense_id, creado_aqui, marco_pago FROM fijo_confirmaciones WHERE recurring_id = ? AND period = ?",
                        (rid, periodo)).fetchone()
    if not fila:
        return {"ok": False}
    if fila[2]:   # la confirmación había dejado pagado ese mes en «Próximos pagos»: vuelve a quedar pendiente
        conn.execute("DELETE FROM payment_reminder_paid WHERE reminder_id = ? AND period = ?", (fila[2], periodo))
    if fila[0] and fila[1] == 1:
        conn.execute("DELETE FROM expenses WHERE id = ?", (fila[0],))
    elif fila[0] and fila[1] == 2:   # se había unido a un gasto anotado a mano: el gasto se queda, sin el fijo
        conn.execute("UPDATE expenses SET recurring_id = NULL WHERE id = ? AND recurring_id = ?", (fila[0], rid))
    conn.execute("DELETE FROM fijo_confirmaciones WHERE recurring_id = ? AND period = ?", (rid, periodo))
    conn.execute("DELETE FROM recurring_postings WHERE recurring_id = ? AND period = ?", (rid, periodo))
    conn.commit()
    return {"ok": True}


def al_marcar_pago(conn, bot, rid, periodo, pagado):
    """«Próximos pagos» y Tarjetas van juntos: marcar pagada la tarjeta deja su abono (baja lo usado) y marcar un pago
    que también es un fijo (la hipoteca) lo confirma. Desmarcar quita solo lo que se hizo desde aquí."""
    rec = conn.execute("SELECT label, amount FROM payment_reminders WHERE id = ?", (rid,)).fetchone()
    if not rec:
        return {}
    hecho = {}
    nota = "pagado en Próximos pagos"
    tid = _tarjeta_de_recordatorio(conn, rid)
    t = conn.execute("SELECT id, moneda FROM tarjetas WHERE id = ?", (tid,)).fetchone() if tid else None
    if t:
        ya = conn.execute("SELECT id FROM tarjeta_abonos WHERE tarjeta_id = ? AND periodo_marcado = ?", (t[0], periodo)).fetchone()
        rmon = conn.execute("SELECT COALESCE(currency, 'COP'), due_day FROM payment_reminders WHERE id = ?", (rid,)).fetchone()
        if pagado and not ya and rec[1] and rmon[0] == t[1]:
            # con la fecha del pago (si ya pasó): un dato del banco posterior ya trae ese pago y no lo resta dos veces
            f_pago = min(_fecha_en(periodo, rmon[1]), _hoy()) if rmon[1] else _hoy()
            conn.execute("INSERT INTO tarjeta_abonos (tarjeta_id, fecha, monto, nota, created_at, periodo_marcado) VALUES (?,?,?,?,?,?)",
                         (t[0], f_pago.isoformat(), float(rec[1]), nota, _ahora_utc(), periodo))
            hecho["abono"] = float(rec[1])
        elif not pagado:
            conn.execute("DELETE FROM tarjeta_abonos WHERE tarjeta_id = ? AND periodo_marcado = ? AND nota = ?", (t[0], periodo, nota))
    if periodo >= FIJOS_CONFIRMAR_DESDE and periodo <= _ym(_hoy()):
        f = conn.execute("SELECT id FROM recurring_expenses WHERE lower(label) = lower(?)", (rec[0] or "",)).fetchone()
        if f:
            c = conn.execute("SELECT fuente FROM fijo_confirmaciones WHERE recurring_id = ? AND period = ?", (f[0], periodo)).fetchone()
            if pagado and not c:
                confirmar(conn, bot, f[0], periodo, "paso", fuente="próximos pagos")
                hecho["fijo_confirmado"] = f[0]
            elif not pagado and c and c[0] == "próximos pagos":
                deshacer(conn, f[0], periodo)
    conn.commit()
    return hecho


def tarjeta_de_metodo(conn):
    out = {}
    for t in _filas(conn, "SELECT id, metodos FROM tarjetas WHERE activa = 1"):
        for m in json.loads(t["metodos"] or "[]"):
            out[m] = t["id"]
    return out


def fijos(conn, bot, periodo=None):
    """La lista de suscripciones y fijos con lo vigente en el período, su tarjeta y su estado en ese mes."""
    periodo = periodo or _ym(_hoy())
    tasas = _tasas(bot)
    mapa = tarjeta_de_metodo(conn)
    conf = {r["recurring_id"]: r for r in _filas(conn, "SELECT * FROM fijo_confirmaciones WHERE period = ?", (periodo,))}
    posteados = {r[0] for r in conn.execute("SELECT recurring_id FROM recurring_postings WHERE period = ?", (periodo,))}
    out = []
    for (rid,) in conn.execute("SELECT id FROM recurring_expenses ORDER BY id").fetchall():
        v = version_de(conn, rid, periodo)
        en_pesos = float(v["monto_usd"] or 0) * tasas["COP"] if v["currency"] == "USD" else float(v["monto_cop"] or 0)
        c = conf.get(rid)
        if c:
            est = "confirmado" if c["estado"] == "paso" else "no_paso"
        elif periodo < FIJOS_CONFIRMAR_DESDE:
            est = "anotado_bot" if rid in posteados else "sin_anotar"
        else:
            hoy = _hoy()
            dia = _fecha_en(periodo, v["dia_cobro"]) if v["dia_cobro"] else None
            est = "por_confirmar" if (dia and dia <= hoy) or (not dia and periodo <= _ym(hoy)) else "previsto"
        versiones = _filas(conn, "SELECT desde, monto_cop, monto_usd, currency, metodo_pago, active FROM recurring_versiones "
                                 "WHERE recurring_id = ? ORDER BY desde", (rid,))
        a_mano = None   # sin confirmar pero ya anotado a mano en el mes: «Queda» no lo debe apartar otra vez
        if est in ("por_confirmar", "previsto") and v["active"]:
            es_usd = v["currency"] == "USD"
            ref = float((v["monto_usd"] if es_usd else v["monto_cop"]) or 0)
            ya = conn.execute("SELECT id FROM expenses WHERE recurring_id = ? AND substr(fecha, 1, 7) = ?", (rid, periodo)).fetchone()
            a_mano = ya[0] if ya else (_gasto_parecido(conn, v, periodo, ref, es_usd) if ref > 0 else None)
        monto_conf = None   # lo que de verdad entró al P&G (otro valor o el gasto al que se unió)
        if c and c["expense_id"]:
            e = conn.execute("SELECT monto_cop, monto_usd FROM expenses WHERE id = ?", (c["expense_id"],)).fetchone()
            if e:
                monto_conf = e[1] if v["currency"] == "USD" else e[0]
        out.append({**v, "periodo": periodo, "tarjeta_id": mapa.get(v["metodo_pago"]), "en_pesos": round(en_pesos),
                    "en_dolares": round(float(v["monto_usd"] or 0) if v["currency"] == "USD" else en_pesos / tasas["COP"], 2),
                    "estado": est, "expense_id": c["expense_id"] if c else None, "monto_confirmado": monto_conf, "gasto_a_mano": a_mano,
                    "versiones": versiones})
    return out


def calendario(conn, bot, desde, hasta):
    """Pagos (Próximos pagos), cortes con su valor estimado y fijos día a día entre dos fechas (YYYY-MM-DD)."""
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    est = estado(conn, bot)
    tarjetas = {t["id"]: t for t in est["tarjetas"]}
    items = []
    meses = []
    p = _ym(d0)
    while p <= _ym(d1):
        meses.append(p)
        p = _sumar_mes(p, 1)
    recs = _filas(conn, "SELECT id, label, kind, due_day, amount, currency FROM payment_reminders WHERE active = 1 AND due_day IS NOT NULL")
    pagados = {(r["reminder_id"], r["period"]) for r in _filas(conn, "SELECT reminder_id, period FROM payment_reminder_paid")}
    rec_tarjeta = {t["recordatorio_id"]: t["id"] for t in est["tarjetas"] if t["recordatorio_id"]}
    fijos_por_mes = {per: fijos(conn, bot, per) for per in meses}
    hoy_ = _hoy()
    proximo_corte = {}
    for t in est["tarjetas"]:
        if t["corte_dia"]:
            for k in (0, 1):
                fc = _fecha_en(_sumar_mes(_ym(hoy_), k), t["corte_dia"])
                if fc >= hoy_:
                    proximo_corte[t["id"]] = fc
                    break
    etiquetas_fijo = {}
    for per in meses:
        for f in fijos_por_mes[per]:
            etiquetas_fijo.setdefault((f["label"] or "").lower(), f)
    for per in meses:
        for r in recs:
            f = _fecha_en(per, r["due_day"])
            if d0 <= f <= d1:
                fijo = etiquetas_fijo.get((r["label"] or "").lower())
                items.append({"tipo": "pago", "fecha": f.isoformat(), "periodo": per, "label": r["label"], "kind": r["kind"],
                              "monto": r["amount"], "moneda": r["currency"], "recordatorio_id": r["id"],
                              "tarjeta_id": rec_tarjeta.get(r["id"]), "pagado": (r["id"], per) in pagados,
                              "fijo_id": fijo["id"] if fijo else None})
        for t in est["tarjetas"]:
            if t["corte_dia"]:
                f = _fecha_en(per, t["corte_dia"])
                if d0 <= f <= d1:
                    items.append({"tipo": "corte", "fecha": f.isoformat(), "periodo": per, "tarjeta_id": t["id"],
                                  "label": f"Corte {t['banco']} ·{t['ultimos'] or t['nombre']}",
                                  "monto": t["usado"] if f == proximo_corte.get(t["id"]) else None,   # solo el próximo corte
                                  "moneda": t["moneda"]})
        con_pago = {i["fijo_id"] for i in items if i["tipo"] == "pago" and i.get("fijo_id")}
        for fj in fijos_por_mes[per]:
            if not fj["active"] or not fj["dia_cobro"] or fj["id"] in con_pago:
                continue
            f = _fecha_en(per, fj["dia_cobro"])
            if d0 <= f <= d1:
                items.append({"tipo": "fijo", "fecha": f.isoformat(), "periodo": per, "fijo_id": fj["id"], "label": fj["label"],
                              "monto": fj["monto_usd"] if fj["currency"] == "USD" else fj["monto_cop"], "moneda": fj["currency"],
                              "tarjeta_id": fj["tarjeta_id"], "metodo_pago": fj["metodo_pago"], "estado": fj["estado"],
                              "monto_confirmado": fj["monto_confirmado"]})
    items.sort(key=lambda i: (i["fecha"], {"pago": 0, "corte": 1, "fijo": 2}[i["tipo"]]))
    # Bandeja: fijos de meses con confirmar (desde dic) que ya pasaron y nadie confirmó
    bandeja = []
    hoy = _hoy()
    per = FIJOS_CONFIRMAR_DESDE
    while per <= _ym(hoy):
        for fj in fijos(conn, bot, per):
            if fj["active"] and fj["estado"] == "por_confirmar":
                bandeja.append({"fijo_id": fj["id"], "periodo": per, "label": fj["label"], "metodo_pago": fj["metodo_pago"],
                                "tarjeta_id": fj["tarjeta_id"], "fecha": _fecha_en(per, fj["dia_cobro"] or 1).isoformat(),
                                "monto": fj["monto_usd"] if fj["currency"] == "USD" else fj["monto_cop"], "moneda": fj["currency"]})
        per = _sumar_mes(per, 1)
    bandeja.sort(key=lambda b: b["fecha"])
    return {"desde": desde, "hasta": hasta, "items": items, "bandeja": bandeja, "confirmar_desde": FIJOS_CONFIRMAR_DESDE,
            "tarjetas": {k: {"banco": v["banco"], "nombre": v["nombre"], "ultimos": v["ultimos"], "color": v["color"],
                             "duenio": v["duenio"]} for k, v in tarjetas.items()}}
