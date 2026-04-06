"""
Expense Tracker Bot v7 · González-Guevara
Telegram bot con menú interactivo · $5,000 USD/mes · Multi-moneda (COP/USD/BOB)
Reset automático el 1ro de cada mes 00:01 COL
"""

import os, re, json, sqlite3, csv, io, traceback
from datetime import datetime, timedelta, time as dtime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Zona horaria Colombia (UTC-5)
COL_TZ = timezone(timedelta(hours=-5))

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]
TRM = int(os.environ.get("TRM", "3700"))
BOB_RATE = float(os.environ.get("BOB_RATE", "9.20"))
DB_PATH = os.environ.get("DB_PATH", "expenses.db")

# ════════════════════════════════════════
# PRESUPUESTO (USD/mes) · $5,000
# ════════════════════════════════════════
BUDGET = {
    "hipoteca":       {"usd": 1486, "tipo": "fijo",      "icon": "🏠", "label": "Hipoteca"},
    "admin":          {"usd": 446,  "tipo": "fijo",      "icon": "🏢", "label": "Admin Nuvó"},
    "empleada":       {"usd": 659,  "tipo": "fijo",      "icon": "🧹", "label": "Empleada"},
    "mado":           {"usd": 400,  "tipo": "fijo",      "icon": "👩", "label": "Mado USDT"},
    "supermercado":   {"usd": 350,  "tipo": "variable",  "icon": "🛒", "label": "Supermercado"},
    "restaurante":    {"usd": 300,  "tipo": "variable",  "icon": "🍽️", "label": "Restaurante"},
    "gasolina":       {"usd": 270,  "tipo": "variable",  "icon": "⛽", "label": "Gasolina"},
    "servicios":      {"usd": 212,  "tipo": "fijo",      "icon": "💡", "label": "Servicios Básicos"},
    "viaje":          {"usd": 200,  "tipo": "variable",  "icon": "✈️", "label": "Viaje"},
    "telecom":        {"usd": 181,  "tipo": "fijo",      "icon": "📱", "label": "Telecom"},
    "salud":          {"usd": 150,  "tipo": "variable",  "icon": "💊", "label": "Salud"},
    "trainer":        {"usd": 130,  "tipo": "semi-fijo", "icon": "🏋️", "label": "Trainer"},
    "claude":         {"usd": 100,  "tipo": "fijo",      "icon": "🤖", "label": "Claude Pro"},
    "suscripciones":  {"usd": 92,   "tipo": "fijo",      "icon": "📺", "label": "Suscripciones"},
    "cafe":           {"usd": 60,   "tipo": "variable",  "icon": "☕", "label": "Café"},
    "uber":           {"usd": 50,   "tipo": "variable",  "icon": "🚕", "label": "Uber/Taxi"},
    "mascotas":       {"usd": 25,   "tipo": "variable",  "icon": "🐾", "label": "Mascotas"},
    "rappi":          {"usd": 20,   "tipo": "variable",  "icon": "📦", "label": "Rappi/Domicilio"},
    "parqueadero":    {"usd": 20,   "tipo": "variable",  "icon": "🅿️", "label": "Parqueadero"},
    "comisiones":     {"usd": 15,   "tipo": "fijo",      "icon": "💳", "label": "Comisiones"},
    "peajes":         {"usd": 15,   "tipo": "variable",  "icon": "🛣️", "label": "Peajes"},
    "mantenimiento":  {"usd": 15,   "tipo": "variable",  "icon": "🔧", "label": "Mant. Vehículo"},
    "seguros":        {"usd": 12,   "tipo": "fijo",      "icon": "🛡️", "label": "Seguros"},
    "tecnologia":     {"usd": 0,    "tipo": "variable",  "icon": "💻", "label": "Tecnología"},
    "muebles":        {"usd": 0,    "tipo": "variable",  "icon": "🪑", "label": "Muebles"},
    "ropa":           {"usd": 0,    "tipo": "variable",  "icon": "👕", "label": "Ropa"},
    "obsequio":       {"usd": 0,    "tipo": "variable",  "icon": "🎁", "label": "Obsequio"},
    "otro":           {"usd": 0,    "tipo": "variable",  "icon": "📌", "label": "Otro"},
}

TOTAL_BUDGET_USD = sum(v["usd"] for v in BUDGET.values())
BUDGET_LIMIT_USD = 5000
# Categorías agrupadas para el menú
CAT_GROUPS = {
    "🏠 Hogar": ["hipoteca", "admin", "empleada", "telecom", "servicios"],
    "🍽️ Comida": ["supermercado", "restaurante", "rappi", "cafe"],
    "🚗 Transporte": ["gasolina", "peajes", "uber", "parqueadero", "mantenimiento"],
    "💊 Personal": ["salud", "trainer", "mascotas", "seguros", "ropa"],
    "💻 Digital": ["claude", "suscripciones", "comisiones", "tecnologia"],
    "👪 Familia": ["mado"],
    "📌 Otro": ["viaje", "muebles", "obsequio", "otro"],
}

# Aliases para texto rápido
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
    "cafeteria": "cafe", "café": "cafe", "coffee": "cafe", "starbucks": "cafe",
    "veterinario": "mascotas", "vet": "mascotas", "perro": "mascotas", "gato": "mascotas",
    "vuelo": "viaje", "hotel": "viaje", "airbnb": "viaje", "avion": "viaje",
    "ropa": "otro", "tech": "otro", "compras": "otro",
    "hipoteca": "hipoteca", "mortgage": "hipoteca",
    "administracion": "admin", "administración": "admin",
    "internet": "telecom", "une": "telecom", "du": "telecom",
    "seguro": "seguros",
    "mado": "mado", "madeline": "mado", "mesada": "mado", "usdt": "mado", "wio": "mado",
    "servicios": "servicios", "agua": "servicios", "luz": "servicios", "basura": "servicios", "internet": "servicios", "acueducto": "servicios",
    "tech": "tecnologia", "tecnologia": "tecnologia", "electronica": "tecnologia", "computador": "tecnologia",
    "mueble": "muebles", "muebles": "muebles", "decoracion": "muebles",
    "regalo": "obsequio", "obsequio": "obsequio", "gift": "obsequio",
    "ropa": "ropa", "vestimenta": "ropa", "zapatos": "ropa", "clothing": "ropa"
}

# ════════════════════════════════════════
# MULTI-CURRENCY PARSER
# ════════════════════════════════════════
# ── Parse amount: supports 50000, 100usd, 50bob, usd100, bob50, "bob 45", "usd 100"
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
    elif currency == "cop":
        display = f"${amount:,.0f}".replace(",", ".") + " COP"
        return amount, display, "cop"
    else:
        # Default: USD
        monto_cop = amount * TRM
        display = f"${amount:,.0f} USD"
        return monto_cop, display, "usd"

def smart_parse(parts):
    """
    Try to parse amount from parts list. Handles:
    - ["50000", ...] -> USD (default)
    - ["100usd", ...] -> USD
    - ["bob", "45", ...] -> BOB (space separated)
    - ["bob45", ...] -> BOB (concatenated)
    Returns (monto_cop, display, currency, remaining_parts) or (None,None,None,parts)
    """
    if not parts:
        return None, None, None, parts

    # Scan for currency keyword (cop/bob) anywhere in parts
    currency_override = None
    clean_parts = []
    for p in parts:
        if p.lower().strip() in ("cop", "bob") and currency_override is None:
            currency_override = p.lower().strip()
        else:
            clean_parts.append(p)

    if not clean_parts:
        return None, None, None, parts

    # If currency found in message, prepend to amount token
    token = clean_parts[0]
    if currency_override:
        token = currency_override + token

    monto_cop, display, currency = parse_amount(token)
    if monto_cop is not None:
        return monto_cop, display, currency, clean_parts[1:]

    # Try "bob 45" / "usd 100" / "cop 50000" (space-separated currency prefix)
    if len(parts) >= 2 and parts[0].lower() in ("bob", "usd", "cop"):
        combined = parts[0] + parts[1]
        monto_cop, display, currency = parse_amount(combined)
        if monto_cop is not None:
            return monto_cop, display, currency, parts[2:]

    return None, None, None, parts

# ════════════════════════════════════════
# DATABASE
# ════════════════════════════════════════
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
    conn.execute("""CREATE TABLE IF NOT EXISTS custom_categories (
        name TEXT PRIMARY KEY,
        icon TEXT DEFAULT '📌',
        label TEXT,
        created_at TEXT
    )""")
    conn.commit()
    # Load custom categories into BUDGET at startup
    for row in conn.execute("SELECT name, icon, label FROM custom_categories").fetchall():
        if row[0] not in BUDGET:
            BUDGET[row[0]] = {"usd": 0, "tipo": "variable", "icon": row[1], "label": row[2]}
            if row[0] not in ALIASES:
                ALIASES[row[0]] = row[0]
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

# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════
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
    return "▓" * filled + "░" * (length - filled)

def traffic(pct):
    if pct < 0.5: return "🟢"
    if pct < 0.75: return "🟡"
    if pct < 1.0: return "🟠"
    return "🔴"

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
        f"╔══════════════════════════════╗\n"
        f"  📊  {now.strftime('%B %Y').upper()}\n"
        f"╚══════════════════════════════╝\n\n"
    )

    # Main gauge
    gauge = (
        f"  {traffic(pct)} {fmt(total_usd * TRM)} COP\n"
        f"  {bar(pct)} {pct:.0%}\n"
        f"  ${total_usd:,.0f} / ${BUDGET_LIMIT_USD:,} USD\n\n"
    )

    # Pace check
    if pct > ideal_pct + 0.1:
        pace = f"  ⚡ Vas rápido — llevas {pct:.0%} del budget en día {day_of_month}/{days_in_month}\n\n"
    elif pct < ideal_pct - 0.1:
        pace = f"  ✨ Buen ritmo — vas por debajo del ideal\n\n"
    else:
        pace = f"  👌 En línea con el ritmo esperado\n\n"

    # Category breakdown
    cats_text = "  ── Categorías con gasto ──\n"
    for cat in sorted(by_cat.keys(), key=lambda c: by_cat[c], reverse=True):
        cat_usd = by_cat[cat]
        info = BUDGET.get(cat, {})
        budget_usd = info.get("usd", 0)
        icon = info.get("icon", "📦")
        label = info.get("label", cat)
        cat_pct = cat_usd / budget_usd if budget_usd > 0 else 0
        cats_text += f"  {traffic(cat_pct)} {icon} {label}: ${cat_usd:.0f}/${budget_usd}\n"

    # Footer
    available = BUDGET_LIMIT_USD - total_usd
    footer = (
        f"\n  ── Disponible ──\n"
        f"  💰 ${available:,.0f} USD ({fmt(available * TRM)} COP)\n"
    )
    if days_left > 0:
        footer += f"  📅 {days_left} días → ${available / days_left:,.0f} USD/día\n"

    return header + gauge + pace + cats_text + footer

# ════════════════════════════════════════
# INLINE KEYBOARDS
# ════════════════════════════════════════
def make_category_keyboard(monto_cop, nota=""):
    """Build grouped category selection keyboard."""
    keyboard = []
    for group_name, cats in CAT_GROUPS.items():
        keyboard.append([InlineKeyboardButton(f"── {group_name} ──", callback_data="noop")])
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
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def make_main_menu():
    """Main action keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("💸 Registrar gasto", callback_data="action:gasto"),
            InlineKeyboardButton("📊 Estado del mes", callback_data="action:status"),
        ],
        [
            InlineKeyboardButton("📅 Esta semana", callback_data="action:semana"),
            InlineKeyboardButton("📋 Budget vs Real", callback_data="action:budget"),
        ],
        [
            InlineKeyboardButton("📜 Últimos gastos", callback_data="action:historial"),
            InlineKeyboardButton("📁 Exportar CSV", callback_data="action:exportar"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def make_confirm_keyboard(expense_id):
    keyboard = [
        [
            InlineKeyboardButton("📊 Ver estado", callback_data="action:status"),
            InlineKeyboardButton("💸 Otro gasto", callback_data="action:gasto"),
        ],
        [InlineKeyboardButton(f"🗑️ Borrar #{expense_id}", callback_data=f"del:{expense_id}")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ════════════════════════════════════════
# HANDLERS
# ════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ No autorizado. Pide a Daniel que agregue tu ID.")
        return

    name = update.effective_user.first_name
    welcome = (
        f"╔══════════════════════════════╗\n"
        f"  👋 ¡Hola {name}!\n"
        f"╚══════════════════════════════╝\n\n"
        f"Soy tu asistente de gastos GG\n"
        f"Budget: ${BUDGET_LIMIT_USD:,} USD/mes\n"
        f"TRM: {fmt(TRM)} COP/USD\n"
        f"BOB: {BOB_RATE:.0f} BOB/USD\n\n"
        f"── Formas de registrar ──\n\n"
        f"1️⃣ Botón → selecciona categoría\n"
        f"2️⃣ Texto rápido:\n"
        f"     50000 restaurante\n"
        f"     100usd hotel miami\n"
        f"     350bob almuerzo\n"
        f"     240000cop gas\n\n"
        f"── Menú ──"
    )
    await update.message.reply_text(welcome, reply_markup=make_main_menu())

async def cmd_gasto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "💸 ¿Cuánto gastaste?\n\n"
            "Escribe el monto (USD por defecto):\n"
            "  /gasto 50000\n"
            "  /gasto 100usd hotel\n"
            "  /gasto 350bob almuerzo\n"
            "  /gasto 240000cop nota"
        )
        return

    monto_cop, display, currency, rest = smart_parse(list(args))

    if monto_cop is None:
        await update.message.reply_text(f"❌ '{args[0]}' no es un monto válido\n\nFormatos: 50000, 100usd, 350bob, bob 45")
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
        f"💸 Monto: **{display}** (${monto_usd:.0f} USD)\n\n"
        f"Selecciona la categoría:",
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
        f"╔══════════════════════════════╗\n"
        f"  ✅  GASTO #{exp_id} REGISTRADO\n"
        f"╚══════════════════════════════╝\n\n"
        f"  {info['icon']} {info['label']}\n"
        f"  💰 {display}  (${monto_usd:.0f} USD)\n"
    )
    if nota:
        msg += f"  📝 {nota}\n"

    msg += (
        f"\n  ── {info['label']} ──\n"
        f"  {traffic(cat_pct)} {bar(cat_pct)} {cat_pct:.0%}\n"
        f"  ${cat_total:.0f} / ${cat_budget} USD\n"
        f"\n  ── Mes total ──\n"
        f"  {traffic(global_pct)} {bar(global_pct)} {global_pct:.0%}\n"
        f"  ${total_usd:,.0f} / ${BUDGET_LIMIT_USD:,} USD\n"
    )

    if cat_pct >= 1.0:
        msg += f"\n  ⚠️ ¡{info['label']} al límite!"
    if global_pct >= 0.9:
        msg += f"\n  🚨 ¡{global_pct:.0%} del tope mensual!"

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
        await query.edit_message_text("❌ Cancelado")
        return

    # Category selection: cat:CATEGORY:MONTO_COP:NOTA
    if data.startswith("cat:"):
        parts = data.split(":", 3)
        cat = parts[1]
        monto_cop = float(parts[2])
        nota = parts[3] if len(parts) > 3 else ""
        await query.edit_message_text(f"⏳ Registrando...")
        await register_and_confirm(query.message, query.from_user, monto_cop, cat, nota)
        return

    # Create new category from unknown input
    if data.startswith("newcat|"):
        parts = data.split("|", 4)
        cat_name = parts[1].lower().strip()
        monto_cop = float(parts[2])
        nota = parts[3] if len(parts) > 3 else ""
        display = parts[4] if len(parts) > 4 else ""
        icon = "\U0001f4cc"
        label = cat_name.capitalize()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO custom_categories (name, icon, label, created_at) VALUES (?, ?, ?, ?)", (cat_name, icon, label, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        if cat_name not in BUDGET:
            BUDGET[cat_name] = {"usd": 0, "tipo": "variable", "icon": icon, "label": label}
        if cat_name not in ALIASES:
            ALIASES[cat_name] = cat_name
        in_group = any(cat_name in cats for cats in CAT_GROUPS.values())
        if not in_group:
            CAT_GROUPS["\U0001f4cc Otro"].append(cat_name)
        await query.edit_message_text(f"\u2705 Categor\u00eda \"{label}\" creada. Registrando gasto...")
        await register_and_confirm(query.message, query.from_user, monto_cop, cat_name, nota, display)
        return

    # Re-categorize: show keyboard for unknown category expense
    if data.startswith("recat|"):
        parts = data.split("|", 3)
        monto_cop = float(parts[1])
        nota = parts[2] if len(parts) > 2 else ""
        await query.edit_message_text(
            "Selecciona categor\u00eda:",
            reply_markup=make_category_keyboard(monto_cop, nota),
            parse_mode="Markdown"
        )
        return

        # Delete
    if data.startswith("del:"):
        eid = int(data.split(":")[1])
        if delete_expense(eid):
            await query.edit_message_text(f"🗑️ Gasto #{eid} eliminado")
        else:
            await query.edit_message_text(f"❌ Gasto #{eid} no encontrado")
        return

    # Actions
    if data.startswith("action:"):
        action = data.split(":")[1]

        if action == "gasto":
            await query.edit_message_text(
                "💸 Escribe el monto:\n\n"
                "  50000 restaurante\n"
                "  100usd hotel\n"
                "  350bob almuerzo\n\n"
                "O solo el monto → menú de categorías"
            )
            return

        if action == "status":
            text = month_summary_text()
            await query.message.reply_text(text, reply_markup=make_main_menu())
            return

        if action == "semana":
            rows = get_week_expenses()
            if not rows:
                await query.message.reply_text("📅 No hay gastos esta semana", reply_markup=make_main_menu())
                return
            total_usd = sum(r[4] for r in rows)
            weekly_budget = BUDGET_LIMIT_USD / 4.33
            pct = total_usd / weekly_budget

            msg = (
                f"╔══════════════════════════════╗\n"
                f"  📅  ESTA SEMANA\n"
                f"╚══════════════════════════════╝\n\n"
                f"  {traffic(pct)} {bar(pct)} {pct:.0%}\n"
                f"  ${total_usd:,.0f} / ~${weekly_budget:,.0f} USD\n\n"
                f"  ── Detalle ──\n"
            )
            for _, user, fecha, cop, usd, cat, nota in rows[:12]:
                info = BUDGET.get(cat, {})
                icon = info.get("icon", "📦")
                label = info.get("label", cat)
                msg += f"  {icon} {fecha[5:]} · {fmt(cop)} · {label}"
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
                f"╔══════════════════════════════╗\n"
                f"  📋  BUDGET vs REAL\n"
                f"╚══════════════════════════════╝\n\n"
            )
            total_real = 0
            for cat, info in sorted(BUDGET.items(), key=lambda x: x[1]["usd"], reverse=True):
                if info["usd"] == 0 and cat not in by_cat:
                    continue
                real = by_cat.get(cat, 0)
                disp = info["usd"] - real
                total_real += real
                icon = info["icon"]
                s = "✅" if disp > 0 else "🔴"
                msg += f"  {s} {icon} {info['label'][:14]:<14} ${info['usd']:>4} → ${real:>4.0f}\n"

            msg += (
                f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  💰 Gastado: ${total_real:,.0f} USD\n"
                f"  🎯 Libre: ${BUDGET_LIMIT_USD - total_real:,.0f} USD\n"
            )
            await query.message.reply_text(msg, reply_markup=make_main_menu())
            return

        if action == "historial":
            rows = get_month_expenses()[:15]
            if not rows:
                await query.message.reply_text("📜 No hay gastos este mes", reply_markup=make_main_menu())
                return
            msg = (
                f"╔══════════════════════════════╗\n"
                f"  📜  ÚLTIMOS GASTOS\n"
                f"╚══════════════════════════════╝\n\n"
            )
            for eid, user, fecha, cop, usd, cat, nota in rows:
                info = BUDGET.get(cat, {})
                icon = info.get("icon", "📦")
                msg += f"  #{eid} {icon} {fecha[5:]} · {fmt(cop)} · {user}"
                if nota:
                    msg += f"\n       📝 {nota}"
                msg += "\n"
            await query.message.reply_text(msg, reply_markup=make_main_menu())
            return

        if action == "exportar":
            rows = get_month_expenses()
            if not rows:
                await query.message.reply_text("📁 No hay gastos este mes")
                return
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "Usuario", "Fecha", "Monto_COP", "Monto_USD", "Categoría", "Nota"])
            for row in rows:
                writer.writerow(row)
            output.seek(0)
            now = datetime.now()
            await query.message.reply_document(
                document=io.BytesIO(output.getvalue().encode("utf-8")),
                filename=f"gastos_{now.strftime('%Y_%m')}.csv",
                caption=f"📁 {now.strftime('%B %Y')} · {len(rows)} gastos"
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
        await update.message.reply_text("❌ ID debe ser número")
        return
    if delete_expense(eid):
        await update.message.reply_text(f"🗑️ Gasto #{eid} eliminado", reply_markup=make_main_menu())
    else:
        await update.message.reply_text(f"❌ #{eid} no encontrado")

async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("── Menú GG ──", reply_markup=make_main_menu())

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
            else:
                # Unknown category - offer to create or reassign
                unknown = rest[0]
                nota = " ".join(rest[1:]) if len(rest) > 1 else ""
                monto_usd = monto_cop / TRM
                keyboard = [
                    [InlineKeyboardButton(
                        f"\u2795 Crear \"{unknown}\" como categor\u00eda",
                        callback_data=f"newcat|{unknown}|{monto_cop}|{nota}|{display}"
                    )],
                    [InlineKeyboardButton(
                        "\U0001f504 Elegir categor\u00eda existente",
                        callback_data=f"recat|{monto_cop}|{nota}|{display}"
                    )]
                ]
                await update.message.reply_text(
                    f"\u2753 No conozco la categor\u00eda *\"{unknown}\"*\n\n"
                    f"Monto: {display} (${monto_usd:.0f} USD)\n\n"
                    f"\u00bfQu\u00e9 deseas hacer?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                return

        # Only amount → show category keyboard (if reasonable amount)
        if monto_cop > 100:
            nota = " ".join(rest) if rest else ""
            monto_usd = monto_cop / TRM
            await update.message.reply_text(
                f"💸 **{display}** (${monto_usd:.0f} USD)\n\n"
                f"Selecciona categoría:",
                reply_markup=make_category_keyboard(monto_cop, nota),
                parse_mode="Markdown"
            )
            return

# ════════════════════════════════════════
# MONTHLY RESET JOB (1ro de cada mes 00:01 COL)
# ════════════════════════════════════════
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
        f"╔══════════════════════════════╗\n"
        f"  🔄  CIERRE DE MES\n"
        f"  {month_name.upper()}\n"
        f"╚══════════════════════════════╝\n\n"
        f"  {traffic(pct)} Total: ${total_usd:,.0f} / ${BUDGET_LIMIT_USD:,} USD\n"
        f"  {bar(pct)} {pct:.0%}\n\n"
    )

    if available > 0:
        msg += f"  ✅ Ahorraste ${available:,.0f} USD\n\n"
    else:
        msg += f"  🔴 Excediste ${abs(available):,.0f} USD\n\n"

    # Top 5 categories
    top_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:5]
    if top_cats:
        msg += "  ── Top categorías ──\n"
        for cat, usd in top_cats:
            info = BUDGET.get(cat, {})
            icon = info.get("icon", "📦")
            label = info.get("label", cat)
            msg += f"  {icon} {label}: ${usd:,.0f} USD\n"

    msg += (
        f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  🆕 ¡Nuevo mes! Budget reiniciado.\n"
        f"  💰 ${BUDGET_LIMIT_USD:,} USD disponibles\n"
    )

    # Send to all allowed users
    for uid in ALLOWED_USERS:
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
        except Exception as e:
            print(f"⚠️ No pude enviar reset a {uid}: {e}")

# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════
# ════════════════════════════════════════
# CSV MENSUAL AUTOMÁTICO (1ro de cada mes)
# ════════════════════════════════════════

async def send_monthly_csv(context: ContextTypes.DEFAULT_TYPE):
    """Send monthly expense CSV report via Telegram on the 1st."""
    import calendar
    now = datetime.now()
    if now.day != 1:
        return
    if now.month == 1:
        year, month = now.year - 1, 12
    else:
        year, month = now.year, now.month - 1
    month_name = calendar.month_name[month]
    first_day = f"{year}-{month:02d}-01"
    last_day = f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT fecha, monto_cop, monto_usd, categoria, nota, usuario "
        "FROM gastos WHERE fecha BETWEEN ? AND ? ORDER BY fecha",
        (first_day, last_day)
    ).fetchall()
    conn.close()

    if not rows:
        for cid in ALLOWED:
            await context.bot.send_message(cid, f"No hay gastos registrados en {month_name} {year}.")
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Fecha", "Usuario", "Categoría", "Monto_COP", "Monto_USD", "Nota"])
    total_usd = 0
    totals_by_user = {}
    totals_by_cat = {}
    for fecha, cop, usd, cat, nota, usuario in rows:
        user_label = "Dani" if usuario == "Daniel" else usuario
        writer.writerow([fecha, user_label, cat, f"{cop:.0f}", f"{usd:.2f}", nota or ""])
        total_usd += usd
        totals_by_user[user_label] = totals_by_user.get(user_label, 0) + usd
        totals_by_cat[cat] = totals_by_cat.get(cat, 0) + usd

    writer.writerow([])
    writer.writerow(["=== RESUMEN PRESUPUESTO vs REAL ==="])
    writer.writerow(["Categoría", "Presupuesto USD", "Gastado USD", "Diferencia", "Estado"])
    budget_total = 0
    for cat_key, info in sorted(BUDGET.items(), key=lambda x: -x[1]["usd"]):
        budgeted = info["usd"]
        spent = totals_by_cat.get(cat_key, 0)
        diff = budgeted - spent
        status = "✅" if diff >= 0 else "⚠️"
        if budgeted > 0 or spent > 0:
            writer.writerow([info["label"], f"{budgeted:.0f}", f"{spent:.2f}", f"{diff:.2f}", status])
            budget_total += budgeted

    writer.writerow([])
    writer.writerow(["TOTAL", f"{budget_total:.0f}", f"{total_usd:.2f}", f"{budget_total - total_usd:.2f}"])
    writer.writerow([])
    writer.writerow(["=== POR USUARIO ==="])
    for u, t in sorted(totals_by_user.items()):
        writer.writerow([u, "", f"{t:.2f}"])

    csv_bytes = buf.getvalue().encode("utf-8-sig")
    buf.close()

    summary = f"📊 *Resumen {month_name} {year}*\nTotal: *${total_usd:,.2f} USD*\nPresupuesto: *${budget_total:,.0f} USD*"
    diff = budget_total - total_usd
    summary += f"\n{'✅ Ahorro' if diff >= 0 else '⚠️ Exceso'}: *${abs(diff):,.2f} USD*"
    for u, t in sorted(totals_by_user.items()):
        summary += f"\n  {u}: ${t:,.2f}"

    for cid in ALLOWED:
        await context.bot.send_message(cid, summary, parse_mode="Markdown")
        await context.bot.send_document(cid, document=io.BytesIO(csv_bytes), filename=f"gastos_{year}_{month:02d}.csv", caption=f"Detalle gastos {month_name} {year}")

# ════════════════════════════════════════
# CREAR CATEGORÍA CUSTOM: /nuevacat
# ════════════════════════════════════════

async def cmd_nuevacat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Create custom category: /nuevacat nombre [emoji] [label]"""
    if not is_allowed(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("❓ Uso: /nuevacat nombre [emoji] [label]\nEjemplo: /nuevacat gimnasio 🏋️ Gimnasio")
        return
    name = args[0].lower().strip()
    if name in BUDGET:
        info = BUDGET[name]
        await update.message.reply_text(f"⚠️ La categoría \"{name}\" ya existe: {info['icon']} {info['label']}")
        return
    icon = args[1] if len(args) > 1 and len(args[1]) <= 4 else "📌"
    label = " ".join(args[2:]) if len(args) > 2 else name.capitalize()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO custom_categories (name, icon, label, created_at) VALUES (?, ?, ?, ?)", (name, icon, label, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    BUDGET[name] = {"usd": 0, "tipo": "variable", "icon": icon, "label": label}
    ALIASES[name] = name
    in_group = any(name in cats for cats in CAT_GROUPS.values())
    if not in_group:
        CAT_GROUPS["📌 Otro"].append(name)
    await update.message.reply_text(f"✅ Categoría creada: {icon} *{label}* (\"{name}\")\nYa puedes usarla: `50 {name}`", parse_mode="Markdown")

# ════════════════════════════════════════
# DASHBOARD HTML INTERACTIVO: /dashboard
# ════════════════════════════════════════

async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Generate interactive HTML dashboard: /dashboard [mes]"""
    if not is_allowed(update.effective_user.id):
        return
    try:
        import calendar
        now = datetime.now(COL_TZ)
        month, year = now.month, now.year
        if ctx.args:
            try:
                month = int(ctx.args[0])
            except ValueError:
                mn = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12}
                month = mn.get(ctx.args[0].lower(), now.month)
        month_label = calendar.month_name[month]
        first_day = f"{year}-{month:02d}-01"
        last_day = f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT id, fecha, monto_cop, monto_usd, categoria, nota, usuario FROM expenses WHERE fecha BETWEEN ? AND ?", (first_day, last_day)).fetchall()
        conn.close()
        total_usd = sum(r[3] for r in rows)
        budget_total = sum(v["usd"] for v in BUDGET.values())
        diff = budget_total - total_usd
        by_cat = {}
        by_user = {}
        for eid, fecha, cop, usd, cat, nota, usuario in rows:
            by_cat.setdefault(cat, []).append({"fecha": fecha, "cop": cop, "usd": usd, "nota": nota or "", "usuario": usuario})
            by_user[usuario] = by_user.get(usuario, 0) + usd
        cat_html = ""
        for ck in sorted(by_cat, key=lambda k: -sum(e["usd"] for e in by_cat[k])):
            info = BUDGET.get(ck, {"icon": "\U0001f4cc", "label": ck, "usd": 0})
            sp = sum(e["usd"] for e in by_cat[ck])
            bd = info["usd"]
            pct = (sp/bd*100) if bd > 0 else 0
            color = "#238636" if pct < 70 else "#d29922" if pct < 100 else "#f85149"
            det = ""
            for e in by_cat[ck]:
                det += f'<tr><td>{e["fecha"]}</td><td>{e["usuario"]}</td><td>${e["usd"]:.2f}</td><td>{e["cop"]:,.0f}</td><td>{e["nota"]}</td></tr>'
            cat_html += f'<details style="margin:8px 0;background:#161b22;border-radius:8px;padding:8px"><summary style="cursor:pointer;font-weight:bold">{info["icon"]} {info["label"]} — ${sp:,.2f} / ${bd:,.0f} ({pct:.0f}%)</summary><div style="background:#0d1117;border-radius:4px;height:6px;margin:6px 0"><div style="background:{color};height:6px;border-radius:4px;width:{min(pct,100):.0f}%"></div></div><table style="width:100%;font-size:12px;border-collapse:collapse"><tr style="color:#8b949e"><th>Fecha</th><th>User</th><th>USD</th><th>COP</th><th>Nota</th></tr>{det}</table></details>'
        user_html = ""
        for u, t in sorted(by_user.items(), key=lambda x: -x[1]):
            user_html += f'<div style="display:flex;justify-content:space-between;padding:4px 0"><span>{u}</span><span>${t:,.2f}</span></div>'
        dc = "color:#238636" if diff >= 0 else "color:#f85149"
        dl = "Ahorro" if diff >= 0 else "Exceso"
        page = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dashboard {month_label} {year}</title>'''
        page += '''<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:system-ui;background:#0d1117;color:#c9d1d9;padding:16px;max-width:600px;margin:auto}h1{font-size:18px;margin-bottom:12px}h2{font-size:14px;color:#8b949e;margin:16px 0 8px}.card{background:#161b22;border-radius:8px;padding:12px;margin:8px 0}</style>'''
        page += f'''</head><body><h1>Dashboard {month_label} {year}</h1>'''
        page += f'''<div class="card"><div style="display:flex;justify-content:space-between"><span>Total gastado</span><span style="font-size:20px;font-weight:bold">${total_usd:,.2f}</span></div><div style="display:flex;justify-content:space-between;margin-top:4px"><span>Presupuesto</span><span>${budget_total:,.0f}</span></div><div style="display:flex;justify-content:space-between;margin-top:4px"><span>{dl}</span><span style="{dc};font-weight:bold">${abs(diff):,.2f}</span></div><div style="margin-top:8px;font-size:12px;color:#8b949e">{len(rows)} gastos registrados</div></div>'''
        page += f'''<h2>Por usuario</h2><div class="card">{user_html}</div>'''
        page += f'''<h2>Por categoria</h2>{cat_html}'''
        page += '''</body></html>'''
        html_bytes = page.encode("utf-8")
        await update.message.reply_document(
            document=io.BytesIO(html_bytes),
            filename=f"dashboard_{year}_{month:02d}.html",
            caption=f"Dashboard {month_label} {year} - ${total_usd:,.2f} / ${budget_total:,.0f}"
        )
    except Exception as ex:
        await update.message.reply_text(f"Error en /dashboard: {type(ex).__name__}: {ex}\n{traceback.format_exc()[-500:]}")
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
    app.add_handler(CommandHandler("nuevacat", cmd_nuevacat))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Schedule monthly reset: runs daily at 00:01 COL (05:01 UTC)
    job_queue = app.job_queue
    reset_time = dtime(hour=5, minute=1, second=0)  # 00:01 COL = 05:01 UTC
    job_queue.run_daily(monthly_reset, time=reset_time, name="monthly_reset")
    # Monthly CSV report: 1st of each month at 08:00 COL (13:00 UTC)
    csv_time = dtime(hour=13, minute=0, second=0)
    job_queue.run_daily(send_monthly_csv, time=csv_time, name="monthly_csv")

    print(f"🤖 Bot v7 iniciado | TRM: {TRM} | BOB: {BOB_RATE} | Budget: ${TOTAL_BUDGET_USD} USD | Reset: 1ro 00:01 COL")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
