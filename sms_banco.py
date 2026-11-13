"""SMS de compra del banco (Daniel 13-nov): el iPhone reenvía cada SMS de Banco de Bogotá («Tu compra por …») y aquí
se decide qué hacer, sin duplicar lo que ya se anotó (por ejemplo, lo pagado con Apple Pay):

1. Si ya hay un gasto con esa tarjeta, el mismo valor y del mismo día → el SMS solo lo confirma («unido»).
2. Si es un fijo de esa tarjeta (desde diciembre: Netflix, iCloud…) → el fijo queda marcado «llegó el SMS»; el ✓ lo da Daniel.
3. Si no → queda «pendiente» en la bandeja «Del banco · por anotar», con la categoría sugerida por el comercio.
   Nada entra al P&G sin su toque. Si después se anota a mano el mismo gasto, el SMS se une solo (reconciliar).

Formato real del SMS (85264):
  «Banco de Bogota: Tu compra por 26,000 fue aprobada con Tarjeta Crédito 0312 el 11/11/26 19:49:01 en CITY PARKING ¿Dudas? …»
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone

_COL = timezone(timedelta(hours=-5))

_RE_COMPRA = re.compile(
    r"compra\s+por\s+(?P<mon>USD|US\$|COP|\$)?\s*(?P<monto>[\d.,]+)\s*(?P<mon2>USD|COP)?\s+fue\s+aprobada\s+con\s+tarjeta\s+"
    r"(?P<tipo>cr[eé]dito|d[eé]bito)?\s*(?:\w+\s+)?(?P<ult>\d{4})\s+el\s+(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{2,4})\s+"
    r"(?P<hora>\d{1,2}:\d{2}(?::\d{2})?)\s+en\s+(?P<comercio>.+?)(?:\s*¿|\s*\?|\s+Dudas|\.\s*$|$)",
    re.IGNORECASE | re.DOTALL)


def _ahora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def preparar(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS sms_banco (
        id INTEGER PRIMARY KEY AUTOINCREMENT, huella TEXT UNIQUE, texto TEXT NOT NULL, persona TEXT,
        monto REAL, moneda TEXT, ultimos TEXT, tarjeta_id TEXT, metodo_pago TEXT, comercio TEXT, fecha TEXT, hora TEXT,
        estado TEXT NOT NULL, expense_id INTEGER, recurring_id INTEGER, periodo TEXT, recibido_at TEXT NOT NULL, resuelto_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS sms_banco_estado ON sms_banco (estado)")
    conn.commit()


def _numero(txt):
    """«26,000» · «1,234,567.89» · «26.000» · «100.00» → número."""
    x = txt.strip()
    if "," in x and "." in x:
        x = x.replace(",", "") if x.rfind(".") > x.rfind(",") else x.replace(".", "").replace(",", ".")
    elif "," in x:
        x = x.replace(",", "") if len(x.split(",")[-1]) == 3 else x.replace(",", ".")
    elif "." in x and len(x.split(".")[-1]) == 3:
        x = x.replace(".", "")
    return float(x)


def leer(texto):
    """Los datos de un SMS de compra, o None si no es uno."""
    t = " ".join(str(texto or "").split())
    m = _RE_COMPRA.search(t)
    if not m:
        return None
    mon = (m.group("mon") or m.group("mon2") or "").upper()
    y = int(m.group("y"))
    y = y + 2000 if y < 100 else y
    try:
        f = date(y, int(m.group("m")), int(m.group("d")))
        monto = _numero(m.group("monto"))
    except ValueError:
        return None
    comercio = m.group("comercio").strip().rstrip(".").strip()
    return {"monto": monto, "moneda": "USD" if mon in ("USD", "US$") else "COP", "ultimos": m.group("ult"),
            "fecha": f.isoformat(), "hora": m.group("hora"), "comercio": comercio[:80]}


def _tarjeta(conn, ultimos):
    fila = conn.execute("SELECT id, metodos FROM tarjetas WHERE ultimos = ? AND activa = 1", (ultimos,)).fetchone()
    if not fila:
        return None, []
    return fila[0], json.loads(fila[1] or "[]")


def _norm(x):
    x = unicodedata.normalize("NFD", str(x or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", x).split())


def sugerir_categoria(conn, budget, comercio):
    """La categoría de ese comercio: primero la de SMS anteriores ya unidos o anotados (mismo comercio del banco),
    si no, la que más se usó en el último año con una nota parecida."""
    com = _norm(comercio)[:80]
    if len(com) < 3:
        return None
    previas = {}
    for cat, c2 in conn.execute("SELECT e.categoria, s.comercio FROM sms_banco s JOIN expenses e ON e.id = s.expense_id "
                                "WHERE s.estado IN ('unido', 'anotado') ORDER BY s.id DESC LIMIT 400"):
        if cat in (budget or {}) and _norm(c2) == com:
            previas[cat] = previas.get(cat, 0) + 1
    if previas:
        return max(previas, key=previas.get)
    desde = (datetime.now(_COL) - timedelta(days=365)).strftime("%Y-%m-%d")
    cuenta, orden = {}, []
    for cat, nota in conn.execute("SELECT categoria, nota FROM expenses WHERE fecha >= ? AND recurring_id IS NULL "
                                  "AND deferred_group_id IS NULL AND COALESCE(nota, '') != '' ORDER BY id DESC", (desde,)):
        n = _norm(nota)
        if len(n) < 3 or cat not in (budget or {}):
            continue
        if n == com or (len(com) >= 4 and com in n) or (len(n) >= 4 and n in com):
            cuenta[cat] = cuenta.get(cat, 0) + 1
            if cat not in orden:
                orden.append(cat)
    if not cuenta:
        return None
    return max(orden, key=lambda k: (cuenta[k], -orden.index(k)))


def _gasto_igual(conn, s, metodos):
    """Un gasto ya anotado que es esta misma compra: mismo valor, misma tarjeta (o sin método), ±1 día, y que ningún otro SMS tomó."""
    d = date.fromisoformat(s["fecha"])
    usados = "SELECT expense_id FROM sms_banco WHERE expense_id IS NOT NULL"
    filas = conn.execute(f"SELECT id, monto_cop, monto_usd, metodo_pago, fecha, COALESCE(tasa_fija, 0) FROM expenses WHERE fecha BETWEEN ? AND ? "
                         f"AND id NOT IN ({usados})", ((d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat())).fetchall()
    cands = []
    for e_id, cop, usd, met, f, fija in filas:
        if s["moneda"] == "USD":
            igual = usd is not None and abs(float(usd) - s["monto"]) <= max(0.02, 0.01 * s["monto"])
        elif fija:   # anotado en dólares (Claude, Wispr…) y el banco lo cobra en pesos: la tasa del banco no es la del bot
            igual = cop is not None and abs(float(cop) - s["monto"]) <= 0.03 * s["monto"]
        else:
            igual = cop is not None and abs(float(cop) - s["monto"]) <= 1
        if not igual:
            continue
        dias = abs((date.fromisoformat(f) - d).days)
        banco = (metodos[0].split(" ")[0] + " ") if metodos else None   # «BDB »: otra tarjeta del mismo banco
        if met in metodos:
            cands.append((0, dias, e_id))
        elif not met or met == "Sin especificar":
            cands.append((1, dias, e_id))
        elif banco and str(met).startswith(banco) and dias == 0:   # anotada con la otra tarjeta del mismo banco, el mismo día
            cands.append((2, dias, e_id))
    return sorted(cands)[0][2] if cands else None


def _fijo_igual(conn, bot, s, tid):
    """Desde diciembre: un fijo de esa tarjeta, por confirmar en ese mes, de valor parecido (±15 %)."""
    import tarjetas
    periodo = s["fecha"][:7]
    if periodo < tarjetas.FIJOS_CONFIRMAR_DESDE or not tid:
        return None
    tomados = {r[0] for r in conn.execute("SELECT recurring_id FROM sms_banco WHERE estado = 'fijo' AND periodo = ?", (periodo,))}
    com = _norm(s.get("comercio"))
    dia = int(s["fecha"][8:10])
    mejor = None
    for f in tarjetas.fijos(conn, bot, periodo):
        if not f["active"] or f["tarjeta_id"] != tid or f["id"] in tomados or f["estado"] not in ("por_confirmar", "previsto"):
            continue
        ref = float((f["monto_usd"] if s["moneda"] == "USD" else f["monto_cop"]) or 0)
        if s["moneda"] != (f["currency"] or "COP"):
            ref = float(f["en_dolares"] or 0) if s["moneda"] == "USD" else float(f["en_pesos"] or 0)
        if ref > 0 and abs(ref - s["monto"]) <= 0.15 * ref:
            # dos fijos del mismo valor (Netflix e iCloud, $44.900): primero el que se llama como el comercio, luego el día de cobro
            palabras = [w for w in _norm(f["label"]).split() if len(w) >= 4]
            nombre = 0 if any(w in com for w in palabras) else 1
            clave = (nombre, abs((f["dia_cobro"] or dia) - dia), abs(ref - s["monto"]))
            if mejor is None or clave < mejor[0]:
                mejor = (clave, f["id"])
    return (mejor[1], periodo) if mejor else None


def _fijo_por_consumo(conn, comercio):
    """FLYPASS, GoPass…: fijos que se llevan como promedio del mes. Cada peaje que llega por SMS no es un gasto aparte:
    se suma al fijo de ese mes (así se ve el valor real antes del extracto). Devuelve el id del fijo o None."""
    com = _norm(comercio)
    for rid, label in conn.execute("SELECT id, label FROM recurring_expenses WHERE active = 1"):
        n = _norm(label)
        if "promedio" not in n:
            continue
        palabras = [w for w in n.split() if len(w) >= 5 and w not in ("promedio", "recargas", "peajes")]
        if any(w in com for w in palabras):
            return rid
    return None


def suma_por_consumo(conn, periodo):
    """{recurring_id: (suma, cuántos)} de los SMS de peajes y recargas del mes."""
    try:
        return {r[0]: (r[1] or 0, r[2]) for r in conn.execute(
            "SELECT recurring_id, SUM(monto), COUNT(*) FROM sms_banco WHERE estado = 'consumo' AND periodo = ? GROUP BY recurring_id", (periodo,))}
    except sqlite3.OperationalError:
        return {}


def recibir(conn, bot, texto, persona):
    """Guarda el SMS y decide: unido / fijo / pendiente. Devuelve lo que el iPhone le muestra a la persona."""
    preparar(conn)
    s = leer(texto)
    huella = hashlib.sha256(" ".join(str(texto or "").split()).encode()).hexdigest()
    if conn.execute("SELECT 1 FROM sms_banco WHERE huella = ?", (huella,)).fetchone():
        return {"estado": "repetido", "titulo": "Ya lo tenía", "mensaje": "Ese SMS ya había llegado."}
    if not s:
        conn.execute("INSERT INTO sms_banco (huella, texto, persona, estado, recibido_at) VALUES (?,?,?, 'no_entendido', ?)",
                     (huella, str(texto)[:600], persona, _ahora_utc()))
        conn.commit()
        return {"estado": "no_entendido", "titulo": "No era una compra", "mensaje": "El SMS no tiene el formato de compra del banco; no se anotó nada."}
    tid, metodos = _tarjeta(conn, s["ultimos"])
    metodo = metodos[0] if metodos else "Sin especificar"
    base = (huella, str(texto)[:600], persona, s["monto"], s["moneda"], s["ultimos"], tid, metodo, s["comercio"], s["fecha"], s["hora"])
    valor = ("US$" if s["moneda"] == "USD" else "$") + f"{s['monto']:,.0f}".replace(",", ".") if s["monto"] >= 100 or s["moneda"] == "COP" \
        else "US$" + f"{s['monto']:.2f}".replace(".", ",")
    e_id = _gasto_igual(conn, s, metodos)
    if e_id:
        conn.execute("INSERT INTO sms_banco (huella, texto, persona, monto, moneda, ultimos, tarjeta_id, metodo_pago, comercio, fecha, hora, "
                     "estado, expense_id, recibido_at, resuelto_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'unido', ?, ?, ?)",
                     base + (e_id, _ahora_utc(), _ahora_utc()))
        conn.commit()
        return {"estado": "unido", "titulo": "Ya estaba anotado", "mensaje": f"{s['comercio']} {valor}: ya lo tenías, no se suma otra vez."}
    rc = _fijo_por_consumo(conn, s["comercio"])
    if rc:
        conn.execute("INSERT INTO sms_banco (huella, texto, persona, monto, moneda, ultimos, tarjeta_id, metodo_pago, comercio, fecha, hora, "
                     "estado, recurring_id, periodo, recibido_at, resuelto_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'consumo', ?, ?, ?, ?)",
                     base + (rc, s["fecha"][:7], _ahora_utc(), _ahora_utc()))
        conn.commit()
        return {"estado": "consumo", "titulo": "Peaje sumado", "mensaje": f"{s['comercio']} {valor}: va al promedio del mes, no se anota aparte."}
    fj = _fijo_igual(conn, bot, s, tid)
    if fj:
        conn.execute("INSERT INTO sms_banco (huella, texto, persona, monto, moneda, ultimos, tarjeta_id, metodo_pago, comercio, fecha, hora, "
                     "estado, recurring_id, periodo, recibido_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'fijo', ?, ?, ?)",
                     base + (fj[0], fj[1], _ahora_utc()))
        conn.commit()
        return {"estado": "fijo", "titulo": "Llegó un fijo", "mensaje": f"{s['comercio']} {valor}: quedó marcado en Tarjetas › Por confirmar."}
    conn.execute("INSERT INTO sms_banco (huella, texto, persona, monto, moneda, ultimos, tarjeta_id, metodo_pago, comercio, fecha, hora, "
                 "estado, recibido_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pendiente', ?)", base + (_ahora_utc(),))
    conn.commit()
    return {"estado": "pendiente", "titulo": "Por anotar", "mensaje": f"{s['comercio']} {valor}: quedó en «Del banco · por anotar»."}


def reconciliar(conn):
    """Los SMS pendientes que ya tienen su gasto (anotado después, por Apple Pay, el tablero o Telegram) se unen solos."""
    preparar(conn)
    n = 0
    for sid, monto, moneda, fecha, tid in conn.execute("SELECT id, monto, moneda, fecha, tarjeta_id FROM sms_banco WHERE estado = 'pendiente'").fetchall():
        metodos = []
        if tid:
            fila = conn.execute("SELECT metodos FROM tarjetas WHERE id = ?", (tid,)).fetchone()
            metodos = json.loads((fila or ["[]"])[0] or "[]")
        e_id = _gasto_igual(conn, {"monto": monto, "moneda": moneda, "fecha": fecha}, metodos)
        if e_id:
            conn.execute("UPDATE sms_banco SET estado = 'unido', expense_id = ?, resuelto_at = ? WHERE id = ?", (e_id, _ahora_utc(), sid))
            n += 1
    if n:
        conn.commit()
    return n


def bandeja(conn, bot):
    """Lo pendiente con su categoría sugerida, y cuántos se unieron solos en los últimos 30 días (para dar confianza)."""
    reconciliar(conn)
    filas = conn.execute("SELECT id, persona, monto, moneda, ultimos, tarjeta_id, metodo_pago, comercio, fecha, hora FROM sms_banco "
                         "WHERE estado = 'pendiente' ORDER BY fecha DESC, hora DESC").fetchall()
    out = []
    for sid, persona, monto, moneda, ult, tid, met, com, fecha, hora in filas:
        out.append({"id": sid, "persona": persona, "monto": monto, "moneda": moneda, "ultimos": ult, "tarjeta_id": tid,
                    "metodo_pago": met, "comercio": com, "fecha": fecha, "hora": hora,
                    "sugerida": sugerir_categoria(conn, getattr(bot, "BUDGET", {}), com)})
    desde = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None).isoformat()
    unidos = conn.execute("SELECT COUNT(*) FROM sms_banco WHERE estado = 'unido' AND recibido_at >= ?", (desde,)).fetchone()[0]
    return {"pendientes": out, "unidos_30d": unidos}


def resolver(conn, sid, estado, expense_id=None):
    if estado not in ("anotado", "descartado"):
        raise ValueError("estado inválido")
    r = conn.execute("UPDATE sms_banco SET estado = ?, expense_id = COALESCE(?, expense_id), resuelto_at = ? WHERE id = ? AND estado = 'pendiente'",
                     (estado, expense_id, _ahora_utc(), sid))
    conn.commit()
    return r.rowcount == 1


def fijos_con_sms(conn, periodo):
    """{recurring_id} con SMS del banco en ese mes (para el «llegó el SMS» de Tarjetas)."""
    try:
        return {r[0] for r in conn.execute("SELECT recurring_id FROM sms_banco WHERE estado = 'fijo' AND periodo = ?", (periodo,))}
    except sqlite3.OperationalError:
        return set()
