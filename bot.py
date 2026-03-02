"""
Expense Tracker Bot v5 Â· GonzÃ¡lez-Guevara
Telegram bot con menÃº interactivo Â· $5,000 USD/mes Â· Multi-moneda (COP/USD/BOB)
Reset automÃ¡tico el 1ro de cada mes 00:01 COL
"""

import os, re, json, sqlite3, csv, io
from datetime import datetime, timedelta, time as dtime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Zona horaria Colombia (UTC-5)
COL_TZ = timezone(timedelta(hours=-5))

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]
TRM = int(os.environ.get("TRM", "3700"))
BOB_RATE = float(os.environ.get("BOB_RATE", "9"))
DB_PATH = os.environ.get("DB_PATH", "expenses.db")

# ââââââââââââââââââââââââââââââââââââââââ
# PRESUPUESTO (USD/mes) Â· $5,000
# ââââââââââââââââââââââââââââââââââââââââ
BUDGET = {
    "hipoteca":       {"usd": 1000, "tipo": "fijo",      "icon": "🏠", "label": "Hipoteca"},
    "admin":          {"usd": 446,  "tipo": "fijo",      "icon": "🏢", "label": "Admin Nuvó"},
    "mado":           {"usd": 400,  "tipo": "fijo",      "icon": "👩", "label": "Mado USDT"},
    "supermercado":   {"usd": 400,  "tipo": "variable",  "icon": "🛒", "label": "Supermercado"},
    "viaje":          {"usd": 400,  "tipo": "variable",  "icon": "✈️", "label": "Viaje"},
    "empleada":       {"usd": 350,  "tipo": "fijo",      "icon": "🧹", "label": "Empleada"},
    "restaurante":    {"usd": 350,  "tipo": "variable",  "icon": "🍽️", "label": "Restaurante"},
    "salud":          {"usd": 309,  "tipo": "variable",  "icon": "🏥", "label": "Salud"},
    "gasolina":       {"usd": 270,  "tipo": "variable",  "icon": "⛽", "label": "Gasolina"},
    "rappi":          {"usd": 232,  "tipo": "variable",  "icon": "🛵", "label": "Rappi/Domicilio"},
    "telecom":        {"usd": 181,  "tipo": "fijo",      "icon": "📡", "label": "Telecom"},
    "trainer":        {"usd": 150,  "tipo": "semi-fijo", "icon": "💪", "label": "Trainer"},
    "claude":         {"usd": 100,  "tipo": "fijo",      "icon": "🤖", "label": "Claude Pro"},
    "cafe":           {"usd": 97,   "tipo": "variable",  "icon": "☕", "label": "Café"},
    "suscripciones":  {"usd": 92,   "tipo": "fijo",      "icon": "📱", "label": "Suscripciones"},
    "mascotas":       {"usd": 81,   "tipo": "variable",  "icon": "🐾", "label": "Mascotas"},
    "peajes":         {"usd": 45,   "tipo": "variable",  "icon": "🛣️", "label": "Peajes"},
    "uber":           {"usd": 30,   "tipo": "variable",  "icon": "🚗", "label": "Uber/Taxi"},
    "mantenimiento":  {"usd": 30,   "tipo": "variable",  "icon": "🔧", "label": "Mant. Vehículo"},
    "comisiones":     {"usd": 15,   "tipo": "fijo",      "icon": "🏦", "label": "Comisiones"},
    "seguros":        {"usd": 12,   "tipo": "fijo",      "icon": "🛡️", "label": "Seguros"},
    "parqueadero":    {"usd": 10,   "tipo": "variable",  "icon": "🅿️", "label": "Parqueadero"},
    "otro":           {"usd": 0,    "tipo": "variable",  "icon": "📦", "label": "Otro"},
}

TOTAL_BUDGET_USD = sum(v["usd"] for v in BUDGET.values())
BUDGET_LIMIT_USD = 5000

# CategorÃ­as agrupadas para el menÃº
CAT_GROUPS = {
    "ð  Hogar": ["hipoteca", "admin", "empleada", "telecom"],
    "ð½ï¸ Comida": ["supermercado", "restaurante", "rappi", "cafe"],
    "ð Transporte": ["gasolina", "peajes", "uber", "parqueadero", "mantenimiento"],
    "ð Personal": ["salud", "trainer", "mascotas", "seguros"],
    "ð» Digital": ["claude", "suscripciones", "comisiones"],
    "ð     "👩 Familia": ["mado"],
    "📦 Otro": ["viaje", "otro"],
}

# Aliases para texto rÃ¡pido
ALIASES = {
    "rest": "restaurante", "restaurantes": "restaurante", "comida": "restaurante", "almuerzo": "restaurante", "cena": "restaurante",
    "super": "supermercado", "mercado": "supermercado", "pricesmart": "supermercado", "exito": "supermercado", "jumbo": "supermercado",
    "domicilio": "rappi", "domicilios": "rappi", "delivery": "rappi", "ifood": "rappi",
    "gas": "gasolina", "tanqueo": "gasolina", "combustible": "gasolina",
    "taxi": "uber", "didi": "uber", "indriver": "uber",
    "gym": "trainer", "entreno": "trainer", "entrenamiento": "trainer",
    "medico": "salud", "medicina": "salud", "farmacia": "salud", "drogueria": "salud",
    "netflix": "suscripciones", "spotify": "suscripciones", "apple": "suscripciones", "amazon": "suscripciones", "streaming": "suscripciones",
    "parking": "parqueadero", "parqueo": "parqueadero",
    "peaje": "peajes", "gopass": "peajes",
    "cafeteria": "cafe", "cafÃ©": "cafe", "coffee": "cafe", "starbucks": "cafe",
    "veterinario": "mascotas", "vet": "mascotas", "perro": "mascotas", "gato": "mascotas",
    "vuelo": "viaje", "hotel": "viaje", "airbnb": "viaje", "avion": "viaje",
    "ropa": "otro", "tech": "otro", "compras": "otro",
    "hipoteca": "hipoteca", "mortgage": "hipoteca",
    "administracion": "admin", "administraciÃ³n": "admin",
    "internet": "telecom", "une": "telecom", "du": "telecom",
    "seguro": "seguros",
    "mado": "mado", "madeline": "mado", "mesada": "mado", "usdt": "mado", "wio": "mado",
}

# ââââââââââââââââââââââââââââââââââââââââ
# MULTI-CURRENCY PARSER
# ââââââââââââââââââââââââââââââââââââââââ
# ââ Parse amount: supports 50000, 100usd, 50bob, usd100, bob50, "bob 45", "usd 100"
AMOUNT_RE = re.compile(
    r'^(cop|usd|bob)?\s*(\d[\d.,]*)\s*(cop|usd|bob)?$',
    re.IGNORECASE
)

def parse_amount(raw: str):
    """Parse amount with optional currency. Returns (monto_cop, display, currency) or (None,None,None)."""
    raw = raw.strip()
    m = AMOUNT_RE.match(raw)
    if not m:
        return None, None, None

    prefix_cur = (m.group(1) or "").lower()
    number_str = m.group(2).replace(".", "").replace(",", "")
    suffix_cur = (m.group(3) or "").lower()

    try:
        amount = float(number_str)
    except ValueError:
        return None, None, None

    currency = prefix_cur or suffix_cur or ""

    if currency == "usd":
        monto_cop = amount * TRM
        display = f"${amount:,.0f} USD"
        return monto_cop, display, "usd"
    elif currency == "bob":
        monto_usd = amount / BOB_RATE
        monto_cop = monto_usd * TRM
        display = f"{amount:,.0f} BOB"
        return monto_cop, display, "bob"
    else:
        display = f"${amount:,.0f}".replace(",", ".") + " COP"
        return amount, display, "cop"

def smart_parse(parts):
    """
    Try to parse amount from parts list. Handles:
    - ["50000", ...] -> COP
    - ["100usd", ...] -> USD
    - ["bob", "45", ...] -> BOB (space separated)
    - ["bob45", ...] -> BOB (concatenated)
    Returns (monto_cop, display, currency, remaining_parts) or (None,None,None,parts)
    """
    if not parts:
        return None, None, None, parts

    # Try first token directly
    monto_cop, display, currency = parse_amount(parts[0])
    if monto_cop is not None:
        return monto_cop, display, currency, parts[1:]

    # Try "bob 45" / "usd 100" / "cop 50000" (space-separated currency prefix)
    if len(parts) >= 2 and parts[0].lower() in ("bob", "usd", "cop"):
        combined = parts[0] + parts[1]
        monto_cop, display, currency = parse_amount(combined)
        if monto_cop is not None:
            return monto_cop, display, currency, parts[2:]

    return None, None, None, parts

# ââââââââââââââââââââââââââââââââââââââââ
# DATABASE
# ââââââââââââââââââââââââââââââââââââââââ
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        fecha TEXT,
        monto_cop REAL,
        monto_usd REAL,
        categoria TEXT,
        nota TEXT,
        created_at TEXT
    )""")
    conn.commit()
    conn.close()

def add_expense(user_id, user_name, monto_cop, categoria, nota=""):
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now()
    monto_usd = round(monto_cop / TRM, 2)
    conn.execute(
        "INSERT INTO expenses (user_id, user_name, fecha, monto_cop, monto_usd, categoria, nota, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (user_id, user_name, now.strftime("%Y-%m-%d"), monto_cop, monto_usd, categoria, nota, now.isoformat())
    )
    conn.commit()
    last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return last_id, monto_usd

def get_month_expenses(year=None, month=None):
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    prefix = f"{y}-{m:02d}"
    rows = conn.execute(
        "SELECT id, user_name, fecha, monto_cop, monto_usd, categoria, nota FROM expenses WHERE fecha LIKE ? ORDER BY fecha DESC, id DESC",
        (f"{prefix}%",)
    ).fetchall()
    conn.close()
    return rows

def get_week_expenses():
    conn = sqlite3.connect(DB_PATH)
    today = datetime.now().date()
    start = today - timedelta(days=today.weekday())
    rows = conn.execute(
        "SELECT id, user_name, fecha, monto_cop, monto_usd, categoria, nota FROM expenses WHERE fecha >= ? ORDER BY fecha DESC, id DESC",
        (start.isoformat(),)
    ).fetchall()
    conn.close()
    return rows

def delete_expense(expense_id):
    conn = sqlite3.connect(DB_PATH)
    result = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    deleted = result.rowcount
    conn.close()
    return deleted > 0

# ââââââââââââââââââââââââââââââââââââââââ
# HELPERS
# ââââââââââââââââââââââââââââââââââââââââ
def is_allowed(user_id):
    return not ALLOWED_USERS or user_id in ALLOWED_USERS

def resolve_category(text):
    t = text.lower().strip()
    if t in BUDGET:
        return t
    return ALIASES.get(t, None)

def fmt(n):
    return f"${n:,.0f}".replace(",", ".")

def bar(pct, length=12):
    filled = int(min(pct, 1.0) * length)
    return "â" * filled + "â" * (length - filled)

def traffic(pct):
    if pct < 0.5: return "ð¢"
    if pct < 0.75: return "ð¡"
    if pct < 1.0: return "ð "
    return "ð´"

def month_summary_text():
    rows = get_month_expenses()
    now = datetime.now()
    total_usd = sum(r[4] for r in rows)
    total_cop = total_usd * TRM
    pct = total_usd / BUDGET_LIMIT_USD if BUDGET_LIMIT_USD > 0 else 0
    days_in_month = (datetime(now.year, now.month % 12 + 1, 1) - timedelta(days=1)).day if now.month < 12 else 31
    day_of_month = now.day
    ideal_pct = day_of_month / days_in_month
    days_left = days_in_month - day_of_month

    by_cat = {}
    for _, _, _, _, usd, cat, _ in rows:
        by_cat.setdefault(cat, 0)
        by_cat[cat] += usd

    header = (
        f"ââââââââââââââââââââââââââââââââ\n"
        f"  ð  {now.strftime('%B %Y').upper()}\n"
        f"ââââââââââââââââââââââââââââââââ\n\n"
    )

    # Main gauge
    gauge = (
        f"  {traffic(pct)} {fmt(total_usd * TRM)} COP\n"
        f"  {bar(pct)} {pct:.0%}\n"
        f"  ${total_usd:,.0f} / ${BUDGET_LIMIT_USD:,} USD\n\n"
    )

    # Pace check
    if pct > ideal_pct + 0.1:
        pace = f"  â¡ Vas rÃ¡pido â llevas {pct:.0%} del budget en dÃ­a {day_of_month}/{days_in_month}\n\n"
    elif pct < ideal_pct - 0.1:
        pace = f"  â¨ Buen ritmo â vas por debajo del ideal\n\n"
    else:
        pace = f"  ð En lÃ­nea con el ritmo esperado\n\n"

    # Category breakdown
    cats_text = "  ââ CategorÃ­as con gasto ââ\n"
    for cat in sorted(by_cat.keys(), key=lambda c: by_cat[c], reverse=True):
        cat_usd = by_cat[cat]
        info = BUDGET.get(cat, {})
        budget_usd = info.get("usd", 0)
        icon = info.get("icon", "ð¦")
        label = info.get("label", cat)
        cat_pct = cat_usd / budget_usd if budget_usd > 0 else 0
        cats_text += f"  {traffic(cat_pct)} {icon} {label}: ${cat_usd:.0f}/${budget_usd}\n"

    # Footer
    available = BUDGET_LIMIT_USD - total_usd
    footer = (
        f"\n  ââ Disponible ââ\n"
        f"  ð° ${available:,.0f} USD ({fmt(available * TRM)} COP)\n"
    )
    if days_left > 0:
        footer += f"  ð {days_left} dÃ­as â ${available / days_left:,.0f} USD/dÃ­a\n"

    return header + gauge + pace + cats_text + footer

# ââââââââââââââââââââââââââââââââââââââââ
# INLINE KEYBOARDS
# ââââââââââââââââââââââââââââââââââââââââ
def make_category_keyboard(monto_cop, nota=""):
    """Build grouped category selection keyboard."""
    keyboard = []
    for group_name, cats in CAT_GROUPS.items():
        keyboard.append([InlineKeyboardButton(f"ââ {group_name} ââ", callback_data="noop")])
        row = []
        for cat in cats:
            info = BUDGET[cat]
            btn_text = f"{info['icon']} {info['label']}"
            # Truncate nota to fit 64-byte callback_data limit
            short_nota = nota[:20] if nota else ""
            cb_data = f"cat:{cat}:{monto_cop:.0f}:{short_nota}"
            if len(cb_data.encode('utf-8')) > 64:
                cb_data = f"cat:{cat}:{monto_cop:.0f}:"
            row.append(InlineKeyboardButton(btn_text, callback_data=cb_data))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
    keyboard.append([InlineKeyboardButton("â Cancelar", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def make_main_menu():
    """Main action keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("ð¸ Registrar gasto", callback_data="action:gasto"),
            InlineKeyboardButton("ð Estado del mes", callback_data="action:status"),
        ],
        [
            InlineKeyboardButton("ð Esta semana", callback_data="action:semana"),
            InlineKeyboardButton("ð Budget vs Real", callback_data="action:budget"),
        ],
        [
            InlineKeyboardButton("ð Ãltimos gastos", callback_data="action:historial"),
            InlineKeyboardButton("ð Exportar CSV", callback_data="action:exportar"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def make_confirm_keyboard(expense_id):
    keyboard = [
        [
            InlineKeyboardButton("ð Ver estado", callback_data="action:status"),
            InlineKeyboardButton("ð¸ Otro gasto", callback_data="action:gasto"),
        ],
        [InlineKeyboardButton(f"ðï¸ Borrar #{expense_id}", callback_data=f"del:{expense_id}")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ââââââââââââââââââââââââââââââââââââââââ
# HANDLERS
# ââââââââââââââââââââââââââââââââââââââââ
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("â No autorizado. Pide a Daniel que agregue tu ID.")
        return

    name = update.effective_user.first_name
    welcome = (
        f"ââââââââââââââââââââââââââââââââ\n"
        f"  ð Â¡Hola {name}!\n"
        f"ââââââââââââââââââââââââââââââââ\n\n"
        f"Soy tu asistente de gastos GG\n"
        f"Budget: ${BUDGET_LIMIT_USD:,} USD/mes\n"
        f"TRM: {fmt(TRM)} COP/USD\n"
        f"BOB: {BOB_RATE:.0f} BOB/USD\n\n"
        f"ââ Formas de registrar ââ\n\n"
        f"1ï¸â£ BotÃ³n â selecciona categorÃ­a\n"
        f"2ï¸â£ Texto rÃ¡pido:\n"
        f"     50000 restaurante\n"
        f"     100usd hotel miami\n"
        f"     350bob almuerzo\n"
        f"     240000cop gas\n\n"
        f"ââ MenÃº ââ"
    )
    await update.message.reply_text(welcome, reply_markup=make_main_menu())

async def cmd_gasto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "ð¸ Â¿CuÃ¡nto gastaste?\n\n"
            "Escribe el monto (COP por defecto):\n"
            "  /gasto 50000\n"
            "  /gasto 100usd hotel\n"
            "  /gasto 350bob almuerzo\n"
            "  /gasto 240000cop nota"
        )
        return

    monto_cop, display, currency, rest = smart_parse(list(args))

    if monto_cop is None:
        await update.message.reply_text(f"â '{args[0]}' no es un monto vÃ¡lido\n\nFormatos: 50000, 100usd, 350bob, bob 45")
        return

    # If category provided, register directly
    if rest:
        cat = resolve_category(rest[0])
        if cat:
            nota = " ".join(rest[1:]) if len(rest) > 1 else ""
            await register_and_confirm(update.message, update.effective_user, monto_cop, cat, nota, display)
            return

    # Otherwise show category menu
    nota = " ".join(rest) if rest else ""
    monto_usd = monto_cop / TRM
    await update.message.reply_text(
        f"ð¸ Monto: **{display}** (${monto_usd:.0f} USD)\n\n"
        f"Selecciona la categorÃ­a:",
        reply_markup=make_category_keyboard(monto_cop, nota),
        parse_mode="Markdown"
    )

async def register_and_confirm(message, user, monto_cop, categoria, nota="", display=None):
    """Register expense and send aesthetic confirmation."""
    user_name = user.first_name or "Unknown"
    exp_id, monto_usd = add_expense(user.id, user_name, monto_cop, categoria, nota)

    if not display:
        display = fmt(monto_cop) + " COP"

    info = BUDGET[categoria]
    month_rows = get_month_expenses()
    cat_total = sum(r[4] for r in month_rows if r[5] == categoria)
    cat_budget = info["usd"]
    cat_pct = cat_total / cat_budget if cat_budget > 0 else 0
    total_usd = sum(r[4] for r in month_rows)
    global_pct = total_usd / BUDGET_LIMIT_USD

    msg = (
        f"ââââââââââââââââââââââââââââââââ\n"
        f"  â  GASTO #{exp_id} REGISTRADO\n"
        f"ââââââââââââââââââââââââââââââââ\n\n"
        f"  {info['icon']} {info['label']}\n"
        f"  ð° {display}  (${monto_usd:.0f} USD)\n"
    )
    if nota:
        msg += f"  ð {nota}\n"

    msg += (
        f"\n  ââ {info['label']} ââ\n"
        f"  {traffic(cat_pct)} {bar(cat_pct)} {cat_pct:.0%}\n"
        f"  ${cat_total:.0f} / ${cat_budget} USD\n"
        f"\n  ââ Mes total ââ\n"
        f"  {traffic(global_pct)} {bar(global_pct)} {global_pct:.0%}\n"
        f"  ${total_usd:,.0f} / ${BUDGET_LIMIT_USD:,} USD\n"
    )

    if cat_pct >= 1.0:
        msg += f"\n  â ï¸ Â¡{info['label']} al lÃ­mite!"
    if global_pct >= 0.9:
        msg += f"\n  ð¨ Â¡{global_pct:.0%} del tope mensual!"

    await message.reply_text(msg, reply_markup=make_confirm_keyboard(exp_id))

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_allowed(query.from_user.id):
        return

    data = query.data

    if data == "noop":
        return

    if data == "cancel":
        await query.edit_message_text("â Cancelado")
        return

    # Category selection: cat:CATEGORY:MONTO_COP:NOTA
    if data.startswith("cat:"):
        parts = data.split(":", 3)
        cat = parts[1]
        monto_cop = float(parts[2])
        nota = parts[3] if len(parts) > 3 else ""
        await query.edit_message_text(f"â³ Registrando...")
        await register_and_confirm(query.message, query.from_user, monto_cop, cat, nota)
        return

    # Delete
    if data.startswith("del:"):
        eid = int(data.split(":")[1])
        if delete_expense(eid):
            await query.edit_message_text(f"ðï¸ Gasto #{eid} eliminado")
        else:
            await query.edit_message_text(f"â Gasto #{eid} no encontrado")
        return

    # Actions
    if data.startswith("action:"):
        action = data.split(":")[1]

        if action == "gasto":
            await query.edit_message_text(
                "ð¸ Escribe el monto:\n\n"
                "  50000 restaurante\n"
                "  100usd hotel\n"
                "  350bob almuerzo\n\n"
                "O solo el monto â menÃº de categorÃ­as"
            )
            return

        if action == "status":
            text = month_summary_text()
            await query.message.reply_text(text, reply_markup=make_main_menu())
            return

        if action == "semana":
            rows = get_week_expenses()
            if not rows:
                await query.message.reply_text("ð No hay gastos esta semana", reply_markup=make_main_menu())
                return
            total_usd = sum(r[4] for r in rows)
            weekly_budget = BUDGET_LIMIT_USD / 4.33
            pct = total_usd / weekly_budget

            msg = (
                f"ââââââââââââââââââââââââââââââââ\n"
                f"  ð  ESTA SEMANA\n"
                f"ââââââââââââââââââââââââââââââââ\n\n"
                f"  {traffic(pct)} {bar(pct)} {pct:.0%}\n"
                f"  ${total_usd:,.0f} / ~${weekly_budget:,.0f} USD\n\n"
                f"  ââ Detalle ââ\n"
            )
            for _, user, fecha, cop, usd, cat, nota in rows[:12]:
                info = BUDGET.get(cat, {})
                icon = info.get("icon", "ð¦")
                label = info.get("label", cat)
                msg += f"  {icon} {fecha[5:]} Â· {fmt(cop)} Â· {label}"
                if nota:
                    msg += f" ({nota})"
                msg += "\n"
            await query.message.reply_text(msg, reply_markup=make_main_menu())
            return

        if action == "budget":
            rows = get_month_expenses()
            by_cat = {}
            for _, _, _, _, usd, cat, _ in rows:
                by_cat.setdefault(cat, 0)
                by_cat[cat] += usd

            msg = (
                f"ââââââââââââââââââââââââââââââââ\n"
                f"  ð  BUDGET vs REAL\n"
                f"ââââââââââââââââââââââââââââââââ\n\n"
            )
            total_real = 0
            for cat, info in sorted(BUDGET.items(), key=lambda x: x[1]["usd"], reverse=True):
                if info["usd"] == 0 and cat not in by_cat:
                    continue
                real = by_cat.get(cat, 0)
                disp = info["usd"] - real
                total_real += real
                icon = info["icon"]
                s = "â" if disp > 0 else "ð´"
                msg += f"  {s} {icon} {info['label'][:14]:<14} ${info['usd']:>4} â ${real:>4.0f}\n"

            msg += (
                f"\n  ââââââââââââââââââââââââââââ\n"
                f"  ð° Gastado: ${total_real:,.0f} USD\n"
                f"  ð¯ Libre: ${BUDGET_LIMIT_USD - total_real:,.0f} USD\n"
            )
            await query.message.reply_text(msg, reply_markup=make_main_menu())
            return

        if action == "historial":
            rows = get_month_expenses()[:15]
            if not rows:
                await query.message.reply_text("ð No hay gastos este mes", reply_markup=make_main_menu())
                return
            msg = (
                f"ââââââââââââââââââââââââââââââââ\n"
                f"  ð  ÃLTIMOS GASTOS\n"
                f"ââââââââââââââââââââââââââââââââ\n\n"
            )
            for eid, user, fecha, cop, usd, cat, nota in rows:
                info = BUDGET.get(cat, {})
                icon = info.get("icon", "ð¦")
                msg += f"  #{eid} {icon} {fecha[5:]} Â· {fmt(cop)} Â· {user}"
                if nota:
                    msg += f"\n       ð {nota}"
                msg += "\n"
            await query.message.reply_text(msg, reply_markup=make_main_menu())
            return

        if action == "exportar":
            rows = get_month_expenses()
            if not rows:
                await query.message.reply_text("ð No hay gastos este mes")
                return
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "Usuario", "Fecha", "Monto_COP", "Monto_USD", "CategorÃ­a", "Nota"])
            for row in rows:
                writer.writerow(row)
            output.seek(0)
            now = datetime.now()
            await query.message.reply_document(
                document=io.BytesIO(output.getvalue().encode("utf-8")),
                filename=f"gastos_{now.strftime('%Y_%m')}.csv",
                caption=f"ð {now.strftime('%B %Y')} Â· {len(rows)} gastos"
            )
            return

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = month_summary_text()
    await update.message.reply_text(text, reply_markup=make_main_menu())

async def cmd_borrar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /borrar ID")
        return
    try:
        eid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("â ID debe ser nÃºmero")
        return
    if delete_expense(eid):
        await update.message.reply_text(f"ðï¸ Gasto #{eid} eliminado", reply_markup=make_main_menu())
    else:
        await update.message.reply_text(f"â #{eid} no encontrado")

async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("ââ MenÃº GG ââ", reply_markup=make_main_menu())

# Quick expense: "50000 restaurante almuerzo" or "100usd hotel miami" or "bob 45 restaurante"
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = update.message.text.strip()
    parts = text.split()

    if not parts:
        return

    monto_cop, display, currency, rest = smart_parse(parts)

    if monto_cop is not None:
        # If category provided
        if rest:
            cat = resolve_category(rest[0])
            if cat:
                nota = " ".join(rest[1:]) if len(rest) > 1 else ""
                await register_and_confirm(update.message, update.effective_user, monto_cop, cat, nota, display)
                return

        # Only amount â show category keyboard (if reasonable amount)
        if monto_cop > 100:
            nota = " ".join(rest) if rest else ""
            monto_usd = monto_cop / TRM
            await update.message.reply_text(
                f"ð¸ **{display}** (${monto_usd:.0f} USD)\n\n"
                f"Selecciona categorÃ­a:",
                reply_markup=make_category_keyboard(monto_cop, nota),
                parse_mode="Markdown"
            )
            return

# ââââââââââââââââââââââââââââââââââââââââ
# MONTHLY RESET JOB (1ro de cada mes 00:01 COL)
# ââââââââââââââââââââââââââââââââââââââââ
async def monthly_reset(context: ContextTypes.DEFAULT_TYPE):
    """Runs daily at 00:01 COL. On the 1st, sends month summary to all users."""
    now_col = datetime.now(COL_TZ)
    if now_col.day != 1:
        return  # Only act on the 1st

    # Get PREVIOUS month's data
    if now_col.month == 1:
        prev_year, prev_month = now_col.year - 1, 12
    else:
        prev_year, prev_month = now_col.year, now_col.month - 1

    rows = get_month_expenses(prev_year, prev_month)
    total_usd = sum(r[4] for r in rows)
    total_cop = total_usd * TRM
    pct = total_usd / BUDGET_LIMIT_USD if BUDGET_LIMIT_USD > 0 else 0
    available = BUDGET_LIMIT_USD - total_usd

    by_cat = {}
    for _, _, _, _, usd, cat, _ in rows:
        by_cat.setdefault(cat, 0)
        by_cat[cat] += usd

    month_name = datetime(prev_year, prev_month, 1).strftime("%B %Y")

    msg = (
        f"ââââââââââââââââââââââââââââââââ\n"
        f"  ð  CIERRE DE MES\n"
        f"  {month_name.upper()}\n"
        f"ââââââââââââââââââââââââââââââââ\n\n"
        f"  {traffic(pct)} Total: ${total_usd:,.0f} / ${BUDGET_LIMIT_USD:,} USD\n"
        f"  {bar(pct)} {pct:.0%}\n\n"
    )

    if available > 0:
        msg += f"  â Ahorraste ${available:,.0f} USD\n\n"
    else:
        msg += f"  ð´ Excediste ${abs(available):,.0f} USD\n\n"

    # Top 5 categories
    top_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:5]
    if top_cats:
        msg += "  ââ Top categorÃ­as ââ\n"
        for cat, usd in top_cats:
            info = BUDGET.get(cat, {})
            icon = info.get("icon", "ð¦")
            label = info.get("label", cat)
            msg += f"  {icon} {label}: ${usd:,.0f} USD\n"

    msg += (
        f"\n  ââââââââââââââââââââââââââââ\n"
        f"  ð Â¡Nuevo mes! Budget reiniciado.\n"
        f"  ð° ${BUDGET_LIMIT_USD:,} USD disponibles\n"
    )

    # Send to all allowed users
    for uid in ALLOWED_USERS:
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
        except Exception as e:
            print(f"â ï¸ No pude enviar reset a {uid}: {e}")

# ââââââââââââââââââââââââââââââââââââââââ
# MAIN
# ââââââââââââââââââââââââââââââââââââââââ
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("gasto", cmd_gasto))
    app.add_handler(CommandHandler("resumen", cmd_status))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("semana", cmd_menu))
    app.add_handler(CommandHandler("presupuesto", cmd_menu))
    app.add_handler(CommandHandler("historial", cmd_menu))
    app.add_handler(CommandHandler("exportar", cmd_menu))
    app.add_handler(CommandHandler("borrar", cmd_borrar))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("ayuda", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Schedule monthly reset: runs daily at 00:01 COL (05:01 UTC)
    job_queue = app.job_queue
    reset_time = dtime(hour=5, minute=1, second=0)  # 00:01 COL = 05:01 UTC
    job_queue.run_daily(monthly_reset, time=reset_time, name="monthly_reset")

    print(f"ð¤ Bot v5 iniciado | TRM: {TRM} | BOB: {BOB_RATE} | Budget: ${BUDGET_LIMIT_USD} USD | Reset: 1ro 00:01 COL")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
