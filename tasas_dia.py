"""Tasa de cambio de cada día (Daniel 12-nov-2026): cada gasto se valora con la tasa del día en que se hizo,
no con una tasa del mes escrita a mano.

Fuentes (públicas, sin llave):
- COP: TRM oficial de la Superfinanciera (datos.gov.co), la misma del patrimonio. Fines de semana y festivos
  toman la TRM vigente ese día.
- CLP: peso chileno de mercado (Yahoo CLP=X); si falla, dólar observado del Banco Central de Chile (mindicador.cl).
- ARS: dólar cripto/USDT, lo que de verdad paga Daniel con El Dorado (argentinadatos.com y dolarapi.com), precio
  de compra: los pesos que te dan por cada dólar.
- BOB: oficial del Banco Central de Bolivia (bo.dolarapi.com); solo hay el valor del día, así que la historia se
  arma desde hoy.
- AED: fija al dólar, 3,6725.

Qué cambia en los gastos: los que se cobran en pesos colombianos (tarjetas colombianas, transferencias locales,
efectivo, sin especificar) guardan monto_usd = monto_cop ÷ TRM de su fecha. Los que se pagan en otra moneda
(Wio, ENBD, USDT, ARQ, Chase), los fijos en dólares y los marcados con tasa_fija conservan su valor en dólares.
Un disparador en la base aplica la regla a todo gasto nuevo o editado, venga de donde venga.
"""
import datetime as dt
import json
import sqlite3
import threading
import time

import httpx

AED_FIJA = 3.6725
NO_COP = ("WIO", "ENBD", "USDT", "ARQ", "CHASE")   # igual que api._NON_COP_METHOD_HINTS
DESDE = "2025-06-01"
_UA = {"User-Agent": "gastos-gg"}

_SQL_TRM = "(SELECT valor FROM tasas_dia WHERE moneda = 'COP' AND fecha <= NEW.fecha ORDER BY fecha DESC LIMIT 1)"
_SQL_COND = (
    "COALESCE(NEW.tasa_fija, 0) = 0 AND COALESCE(NEW.monto_cop, 0) > 0 "
    + "".join(f"AND UPPER(COALESCE(NEW.metodo_pago, '')) NOT LIKE '%{h}%' " for h in NO_COP)
    + "AND NOT EXISTS (SELECT 1 FROM recurring_expenses r WHERE r.id = NEW.recurring_id AND COALESCE(r.currency, 'COP') = 'USD') "
    + f"AND COALESCE({_SQL_TRM}, 0) > 0"
)


def preparar(conn):
    """Tabla de tasas, columna tasa_fija y los disparadores. Idempotente."""
    conn.execute("CREATE TABLE IF NOT EXISTS tasas_dia (fecha TEXT NOT NULL, moneda TEXT NOT NULL, valor REAL NOT NULL, "
                 "fuente TEXT, actualizado TEXT, PRIMARY KEY (fecha, moneda))")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(expenses)")]
    if "tasa_fija" not in cols:
        conn.execute("ALTER TABLE expenses ADD COLUMN tasa_fija INTEGER DEFAULT 0")
    conn.execute(f"CREATE TRIGGER IF NOT EXISTS gastos_tasa_dia_nuevo AFTER INSERT ON expenses WHEN {_SQL_COND} "
                 f"BEGIN UPDATE expenses SET monto_usd = ROUND(NEW.monto_cop / {_SQL_TRM}, 2) WHERE id = NEW.id; END")
    conn.execute(f"CREATE TRIGGER IF NOT EXISTS gastos_tasa_dia_editado AFTER UPDATE OF monto_cop, fecha, metodo_pago, tasa_fija ON expenses WHEN {_SQL_COND} "
                 f"BEGIN UPDATE expenses SET monto_usd = ROUND(NEW.monto_cop / {_SQL_TRM}, 2) WHERE id = NEW.id; END")
    conn.commit()


def _existe(conn, tabla):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabla,)).fetchone())


# ---------- descargas ----------
def _cop(desde):
    filas = httpx.get("https://www.datos.gov.co/resource/32sa-8pi3.json",
                      params={"$where": f"vigenciadesde >= '{desde}T00:00:00'", "$order": "vigenciadesde", "$limit": 5000},
                      timeout=20, headers=_UA).json()
    out = {}
    for r in filas:
        a, b = dt.date.fromisoformat(r["vigenciadesde"][:10]), dt.date.fromisoformat(r["vigenciahasta"][:10])
        d = a
        while d <= b:
            out[d.isoformat()] = float(r["valor"])
            d += dt.timedelta(days=1)
    return out, "TRM Superfinanciera"


def _clp(desde):
    """Peso chileno de mercado (Yahoo CLP=X, cierre de cada día). Respaldo: dólar observado (mindicador.cl)."""
    out = {}
    try:
        d = httpx.get("https://query1.finance.yahoo.com/v8/finance/chart/CLP=X", params={"range": "2y", "interval": "1d"},
                      timeout=30, headers={"User-Agent": "Mozilla/5.0"}).json()["chart"]["result"][0]
        for ts, v in zip(d["timestamp"], d["indicators"]["quote"][0]["close"]):
            if v:
                out[dt.datetime.utcfromtimestamp(ts).date().isoformat()] = float(v)
        fuente = "mercado (Yahoo CLP=X)"
    except Exception:
        for anio in range(int(desde[:4]), dt.date.today().year + 1):
            for x in httpx.get(f"https://mindicador.cl/api/dolar/{anio}", timeout=40, headers=_UA).json().get("serie") or []:
                out[x["fecha"][:10]] = float(x["valor"])
        fuente = "dólar observado, Banco Central de Chile"
    return _rellenar({k: v for k, v in out.items() if k >= desde}, desde), fuente


def _ars(desde):
    out = {}
    for x in httpx.get("https://api.argentinadatos.com/v1/cotizaciones/dolares/cripto", timeout=20, headers=_UA).json():
        if x.get("fecha", "") >= desde and x.get("compra"):
            out[x["fecha"]] = float(x["compra"])
    try:
        hoy = httpx.get("https://dolarapi.com/v1/dolares/cripto", timeout=15, headers=_UA).json()
        out[hoy["fechaActualizacion"][:10]] = float(hoy["compra"])
    except Exception:
        pass
    return _rellenar(out, desde), "dólar cripto (USDT), compra"


def _bob(desde):
    x = httpx.get("https://bo.dolarapi.com/v1/dolares/oficial", timeout=15, headers=_UA).json()
    return {x["fechaActualizacion"][:10]: float(x["compra"])}, "oficial, Banco Central de Bolivia"


def _rellenar(dias, desde):
    """Días sin dato (fines de semana, festivos) toman el último anterior, hasta hoy."""
    if not dias:
        return {}
    out, ultimo = {}, None
    d, fin = dt.date.fromisoformat(max(desde, min(dias))), dt.date.today()
    while d <= fin:
        k = d.isoformat()
        ultimo = dias.get(k, ultimo)
        if ultimo:
            out[k] = ultimo
        d += dt.timedelta(days=1)
    return out


FUENTES = {"COP": _cop, "CLP": _clp, "ARS": _ars, "BOB": _bob}


def bajar(db_path, desde=DESDE):
    """Descarga y guarda lo que haya. Una fuente caída no frena las demás. Devuelve {moneda: días guardados o error}."""
    res = {}
    ahora = dt.datetime.now().isoformat(timespec="seconds")
    for mon, f in FUENTES.items():
        try:
            dias, fuente = f(desde)
            if not dias:
                res[mon] = "sin datos"
                continue
            with sqlite3.connect(db_path) as c:
                c.executemany("INSERT OR REPLACE INTO tasas_dia (fecha, moneda, valor, fuente, actualizado) VALUES (?,?,?,?,?)",
                              [(k, mon, v, fuente, ahora) for k, v in dias.items()])
            res[mon] = len(dias)
        except Exception as e:
            res[mon] = f"error: {type(e).__name__}"
    return res


# ---------- consulta ----------
def tasa(conn, moneda, fecha):
    if moneda == "AED":
        return AED_FIJA
    row = conn.execute("SELECT valor FROM tasas_dia WHERE moneda = ? AND fecha <= ? ORDER BY fecha DESC LIMIT 1", (moneda, fecha)).fetchone()
    return float(row[0]) if row else None


def de_hoy(conn):
    hoy = dt.date.today().isoformat()
    return {m: tasa(conn, m, hoy) for m in ("COP", "BOB", "AED", "CLP", "ARS")}


def promedio_mes(conn, moneda, periodo):
    if moneda == "AED":
        return AED_FIJA
    hasta = min(dt.date.today().isoformat(), periodo + "-31")
    row = conn.execute("SELECT AVG(valor), COUNT(*) FROM tasas_dia WHERE moneda = ? AND fecha >= ? AND fecha <= ?",
                       (moneda, periodo + "-01", hasta)).fetchone()
    return float(row[0]) if row and row[1] else None


def bitacora(conn, desde, hasta):
    """[{fecha, COP, BOB, AED, CLP, ARS}] día por día (la tasa que se usó para los gastos de ese día)."""
    d, fin, out = dt.date.fromisoformat(desde), dt.date.fromisoformat(hasta), []
    while d <= fin:
        k = d.isoformat()
        out.append({"fecha": k, **{m: tasa(conn, m, k) for m in ("COP", "BOB", "AED", "CLP", "ARS")}})
        d += dt.timedelta(days=1)
    return out


# ---------- recalcular ----------
def _es_cop(metodo):
    m = (metodo or "").upper()
    return not any(h in m for h in NO_COP)


def recalcular(conn, desde=None, hasta=None, aplicar=False):
    """Pasa los gastos cobrados en pesos colombianos a la TRM de su día. Con aplicar=False solo dice qué cambiaría.
    Devuelve {meses: {YYYY-MM: [n, antes, despues]}, cambiados, sin_trm}."""
    q = ("SELECT e.id, e.fecha, e.monto_cop, e.monto_usd, COALESCE(e.metodo_pago, ''), COALESCE(e.tasa_fija, 0), "
         "COALESCE(r.currency, 'COP') FROM expenses e LEFT JOIN recurring_expenses r ON r.id = e.recurring_id WHERE 1=1")
    p = []
    if desde:
        q += " AND e.fecha >= ?"; p.append(desde)
    if hasta:
        q += " AND e.fecha <= ?"; p.append(hasta)
    meses, cambios, sin_trm = {}, [], 0
    for eid, fecha, cop, usd, metodo, fija, rec_cur in conn.execute(q, p).fetchall():
        if fija or rec_cur == "USD" or not _es_cop(metodo) or not cop or cop <= 0:
            continue
        t = tasa(conn, "COP", fecha)
        if not t:
            sin_trm += 1
            continue
        nuevo = round(float(cop) / t, 2)
        m = meses.setdefault(fecha[:7], [0, 0.0, 0.0])
        m[0] += 1; m[1] += float(usd or 0); m[2] += nuevo
        if abs(nuevo - float(usd or 0)) >= 0.005:
            cambios.append((nuevo, eid))
    if aplicar and cambios:
        conn.executemany("UPDATE expenses SET monto_usd = ? WHERE id = ?", cambios)
        conn.commit()
    return {"meses": {k: [v[0], round(v[1], 2), round(v[2], 2)] for k, v in sorted(meses.items())},
            "cambiados": len(cambios), "sin_trm": sin_trm, "aplicado": bool(aplicar)}


def marcar_fijos(conn, ids):
    """Gastos que se registraron en otra moneda (USD, AED, BOB…) con método colombiano: conservan su valor en dólares."""
    conn.executemany("UPDATE expenses SET tasa_fija = 1 WHERE id = ?", [(i,) for i in ids])
    conn.commit()


# ---------- en segundo plano ----------
_MODS = []   # el bot corre como __main__ y la API lo importa como «bot»: son dos copias del módulo


def agregar_modulo(mod):
    if mod is not None and all(m is not mod for m in _MODS):
        _MODS.append(mod)


def poner_vigentes(db_path, mod_bot=None):
    """Las tasas de hoy pasan a ser las vigentes del bot (Telegram, Anotar) y quedan en config.rates."""
    with sqlite3.connect(db_path) as c:
        h = de_hoy(c)
        if not h.get("COP"):
            return None
        row = c.execute("SELECT value FROM config WHERE key = 'rates'").fetchone()
        r = json.loads(row[0]) if row and row[0] else {}
        nuevo = {"TRM": round(h["COP"], 2), "BOB_RATE": h["BOB"] or r.get("BOB_RATE"), "AED_RATE": AED_FIJA,
                 "CLP_RATE": h["CLP"] or r.get("CLP_RATE"), "ARS_RATE": h["ARS"] or r.get("ARS_RATE")}
        nuevo = {k: v for k, v in nuevo.items() if v}
        c.execute("INSERT INTO config (key, value, updated_at) VALUES ('rates', ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                  "updated_at = excluded.updated_at", (json.dumps(nuevo), dt.datetime.now().isoformat()))
    agregar_modulo(mod_bot)
    for m in _MODS:
        m.TRM = nuevo["TRM"]
        m.BOB_RATE = nuevo.get("BOB_RATE", m.BOB_RATE)
        m.AED_RATE = AED_FIJA
        m.CLP_RATE = nuevo.get("CLP_RATE", m.CLP_RATE)
        m.ARS_RATE = nuevo.get("ARS_RATE", m.ARS_RATE)
    return nuevo


def ciclo(db_path, mod_bot):
    """Baja las tasas, pone las de hoy como vigentes y repasa los gastos de los últimos 10 días
    (las cuotas diferidas con fecha futura toman su TRM cuando llega su día)."""
    res = bajar(db_path)
    vig = poner_vigentes(db_path, mod_bot)
    with sqlite3.connect(db_path) as c:
        r = recalcular(c, desde=(dt.date.today() - dt.timedelta(days=10)).isoformat(), hasta=dt.date.today().isoformat(), aplicar=True)
    print(f"[tasas] {res} · vigentes {vig} · repaso 10 días: {r['cambiados']} gastos", flush=True)
    return res, vig, r


_HILO = {"vivo": False}


def iniciar(db_path, mod_bot, cada=3 * 3600):
    agregar_modulo(mod_bot)
    if _HILO["vivo"]:
        return
    _HILO["vivo"] = True

    def bucle():
        while True:
            try:
                ciclo(db_path, mod_bot)
            except Exception as e:
                print(f"[tasas] ⚠️ {e!r}", flush=True)
            time.sleep(cada)
    threading.Thread(target=bucle, daemon=True, name="tasas_dia").start()
