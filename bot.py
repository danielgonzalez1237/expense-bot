"""
Expense Tracker Bot · González-Guevara
Telegram bot para tracking de gastos diarios contra presupuesto de $4,000 USD/mes

Uso:
  /gasto 50000 restaurante         → Registra gasto
  /gasto 240000 gasolina semanal   → Con nota
  /resumen                         → Resumen del mes actual
  /semana                          → Resumen de la semana
  /presupuesto                     → Estado vs presupuesto
  /historial                       → Últimos 20 gastos
  /exportar                        → Exporta CSV del mes
  /borrar ID                       → Borra un gasto por ID
  /categorias                      → Lista de categorías válidas
  /ayuda                           → Ayuda
"""

import os, json, sqlite3, csv, io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]
TRM = int(os.environ.get("TRM", "3700"))
DB_PATH = os.environ.get("DB_PATH", "expenses.db")

# === PRESUPUESTO (USD/mes) ===
BUDGET = {
    "hipoteca":       {"usd": 1000, "tipo": "fijo",     "label": "Hipoteca"},
    "admin":          {"usd": 371,  "tipo": "fijo",     "label": "Admin Nuvó Medellín"},
    "empleada":       {"usd": 774,  "tipo": "fijo",     "label": "Empleada doméstica"},
    "telecom":        {"usd": 106,  "tipo": "fijo",     "label": "Telecom (UNE+UAE)"},
    "seguros":        {"usd": 12,   "tipo": "fijo",     "label": "Seguros mascotas"},
    "trainer":        {"usd": 130,  "tipo": "semi-fijo","label": "Trainer personal"},
    "salud":          {"usd": 115,  "tipo": "variable", "label": "Salud"},
    "claude":         {"usd": 100,  "tipo": "fijo",     "label": "Claude Pro"},
    "suscripciones":  {"usd": 92,   "tipo": "fijo",     "label": "Apps (Apple+Netflix+Amazon+Mobi)"},
    "gasolina":       {"usd": 259,  "tipo": "variable", "label": "Gasolina"},
    "peajes":         {"usd": 41,   "tipo": "variable", "label": "Peajes GOPASS"},
    "uber":           {"usd": 27,   "tipo": "variable", "label": "Uber/Taxi"},
    "parqueadero":    {"usd": 8,    "tipo": "variable", "label": "Parqueadero"},
    "mantenimiento":  {"usd": 27,   "tipo": "variable", "label": "Mantenimiento vehículo"},
    "supermercado":   {"usd": 378,  "tipo": "variable", "label": "Supermercado"},
    "restaurante":    {"usd": 149,  "tipo": "variable", "label": "Restaurantes"},
    "rappi":          {"usd": 122,  "tipo": "variable", "label": "Domicilios/Rappi"},
    "cafe":           {"usd": 27,   "tipo": "variable", "label": "Cafeterías"},
    "viaje":          {"usd": 150,  "tipo": "variable", "label": "Fondo viaje"},
    "comisiones":     {"usd": 15,   "tipo": "fijo",     "label": "Comisiones bancarias"},
    "mascotas":       {"usd": 27,   "tipo": "variable", "label": "Mascotas"},
    "otro":           {"usd": 0,    "tipo": "variable", "label": "Otros / Sin categoría"},
}

TOTAL_BUDGET_USD = sum(v["usd"] for v in BUDGET.values())
BUDGET_LIMIT_USD = 4000

# Aliases para reconocimiento rápido
ALIASES = {
    "rest": "restaurante", "restaurantes": "restaurante", "comida": "restaurante",
    "super": "supermercado", "mercado": "supermercado", "pricesmart": "supermercado",
    "exito": "supermercado", "jumbo": "supermercado",
    "domicilio": "rappi", "domicilios": "rappi", "delivery": "rappi",
    "gas": "gasolina", "tanqueo": "gasolina", "combustible": "gasolina",
    "taxi": "uber", "didi": "uber",
    "gym": "trainer", "entreno": "trainer", "entrenamiento": "trainer",
    "medico": "salud", "medicina": "salud", "farmacia": "salud", "drogueria": "salud",
    "netflix": "suscripciones", "spotify": "suscripciones", "apple": "suscripciones",
    "amazon": "suscripciones", "streaming": "suscripciones",
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
}

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

def is_allowed(user_id):
    return not ALLOWED_USERS or user_id in ALLOWED_USERS

def resolve_category(text):
    t = text.lower().strip()
    if t in BUDGET:
        return t
    return ALIASES.get(t, None)

def format_cop(n):
    return f"${n:,.0f}".replace(",", ".")

def bar(pct):
    filled = int(min(pct, 1.0) * 10)
    return "█" * filled + "░" * (10 - filled)

# === HANDLERS ===

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("No autorizado. Pide a Daniel que agregue tu user ID.")
        return
    await update.message.reply_text(
        f"👋 ¡Hola {update.effective_user.first_name}!\n\n"
        "Soy el bot de gastos González-Guevara.\n\n"
        "Para registrar un gasto:\n"
        "  /gasto MONTO CATEGORÍA [nota]\n\n"
        "Ejemplo:\n"
        "  /gasto 50000 restaurante almuerzo\n"
        "  /gasto 240000 gasolina\n\n"
        "Comandos:\n"
        "  /resumen — mes actual\n"
        "  /semana — esta semana\n"
        "  /presupuesto — estado vs budget\n"
        "  /categorias — categorías válidas\n"
        "  /historial — últimos 20\n"
        "  /exportar — CSV del mes\n"
        "  /borrar ID — eliminar gasto\n"
    )

async def cmd_gasto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    args = ctx.args
    if not args or len(args) < 2:
        await update.message.reply_text("Uso: /gasto MONTO CATEGORÍA [nota]\nEjemplo: /gasto 50000 restaurante almuerzo")
        return

    try:
        monto_str = args[0].replace(".", "").replace(",", "")
        monto = float(monto_str)
    except ValueError:
        await update.message.reply_text(f"'{args[0]}' no es un monto válido. Usa números sin puntos.\nEjemplo: /gasto 50000 restaurante")
        return

    cat_input = args[1]
    categoria = resolve_category(cat_input)

    if not categoria:
        cats = ", ".join(sorted(BUDGET.keys()))
        await update.message.reply_text(f"Categoría '{cat_input}' no reconocida.\n\nCategorías válidas:\n{cats}\n\nUsa /categorias para ver todas con aliases.")
        return

    nota = " ".join(args[2:]) if len(args) > 2 else ""
    user_name = update.effective_user.first_name or "Unknown"

    exp_id, monto_usd = add_expense(update.effective_user.id, user_name, monto, categoria, nota)

    # Check budget status for this category
    month_rows = get_month_expenses()
    cat_total_usd = sum(r[4] for r in month_rows if r[5] == categoria)
    cat_budget = BUDGET[categoria]["usd"]

    pct = cat_total_usd / cat_budget if cat_budget > 0 else 0
    status = "🟢" if pct < 0.7 else "🟡" if pct < 1.0 else "🔴"

    total_month_usd = sum(r[4] for r in month_rows)
    global_pct = total_month_usd / BUDGET_LIMIT_USD
    global_status = "🟢" if global_pct < 0.7 else "🟡" if global_pct < 1.0 else "🔴"

    msg = (
        f"✅ Gasto #{exp_id} registrado\n\n"
        f"💰 {format_cop(monto)} COP (${monto_usd:.0f} USD)\n"
        f"📂 {BUDGET[categoria]['label']}\n"
    )
    if nota:
        msg += f"📝 {nota}\n"
    msg += (
        f"\n{status} {BUDGET[categoria]['label']}: ${cat_total_usd:.0f}/${cat_budget} USD ({pct:.0%})\n"
        f"{bar(pct)}\n"
        f"\n{global_status} Total mes: ${total_month_usd:.0f}/${BUDGET_LIMIT_USD} USD ({global_pct:.0%})\n"
        f"{bar(global_pct)}"
    )

    if pct >= 1.0:
        msg += f"\n\n⚠️ ¡Pasaste el presupuesto de {BUDGET[categoria]['label']}!"
    if global_pct >= 0.9:
        msg += f"\n\n🚨 ¡Vas al {global_pct:.0%} del tope mensual!"

    await update.message.reply_text(msg)

async def cmd_resumen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_month_expenses()
    if not rows:
        await update.message.reply_text("No hay gastos registrados este mes.")
        return

    now = datetime.now()
    by_cat = {}
    total_usd = 0
    for _, user, fecha, cop, usd, cat, nota in rows:
        by_cat.setdefault(cat, 0)
        by_cat[cat] += usd
        total_usd += usd

    total_cop = total_usd * TRM
    pct = total_usd / BUDGET_LIMIT_USD
    status = "🟢" if pct < 0.7 else "🟡" if pct < 1.0 else "🔴"

    msg = f"📊 Resumen {now.strftime('%B %Y')}\n\n"
    msg += f"{status} Total: ${total_usd:,.0f} / ${BUDGET_LIMIT_USD:,} USD ({pct:.0%})\n"
    msg += f"{bar(pct)}\n"
    msg += f"({format_cop(total_cop)} COP)\n\n"

    for cat in sorted(by_cat.keys(), key=lambda c: by_cat[c], reverse=True):
        cat_usd = by_cat[cat]
        budget_usd = BUDGET.get(cat, {}).get("usd", 0)
        cat_pct = cat_usd / budget_usd if budget_usd > 0 else 0
        s = "🟢" if cat_pct < 0.7 else "🟡" if cat_pct < 1.0 else "🔴"
        label = BUDGET.get(cat, {}).get("label", cat)
        msg += f"{s} {label}: ${cat_usd:.0f}/${budget_usd} USD\n"

    msg += f"\n💡 Disponible: ${BUDGET_LIMIT_USD - total_usd:,.0f} USD"
    days_left = (datetime(now.year, now.month % 12 + 1, 1) - now).days if now.month < 12 else (datetime(now.year + 1, 1, 1) - now).days
    if days_left > 0:
        msg += f"\n📅 {days_left} días restantes → ${(BUDGET_LIMIT_USD - total_usd) / days_left:,.0f} USD/día"

    await update.message.reply_text(msg)

async def cmd_semana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_week_expenses()
    if not rows:
        await update.message.reply_text("No hay gastos esta semana.")
        return

    total_usd = sum(r[4] for r in rows)
    weekly_budget = BUDGET_LIMIT_USD / 4.33
    pct = total_usd / weekly_budget

    msg = f"📅 Esta semana\n\n"
    msg += f"Total: ${total_usd:,.0f} USD ({format_cop(total_usd * TRM)} COP)\n"
    msg += f"Referencia semanal: ~${weekly_budget:,.0f} USD\n"
    msg += f"{bar(pct)} ({pct:.0%})\n\n"

    for _, user, fecha, cop, usd, cat, nota in rows[:15]:
        label = BUDGET.get(cat, {}).get("label", cat)
        msg += f"• {fecha} | {format_cop(cop)} | {label}"
        if nota:
            msg += f" ({nota})"
        msg += "\n"

    await update.message.reply_text(msg)

async def cmd_presupuesto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_month_expenses()
    by_cat = {}
    for _, _, _, _, usd, cat, _ in rows:
        by_cat.setdefault(cat, 0)
        by_cat[cat] += usd

    msg = "📋 Presupuesto vs Real\n\n"
    msg += f"{'Categoría':<20} {'Budget':>7} {'Real':>7} {'Disp':>7}\n"
    msg += "─" * 44 + "\n"

    total_budget = 0
    total_real = 0
    for cat, info in sorted(BUDGET.items(), key=lambda x: x[1]["usd"], reverse=True):
        if info["usd"] == 0 and cat not in by_cat:
            continue
        real = by_cat.get(cat, 0)
        disp = info["usd"] - real
        total_budget += info["usd"]
        total_real += real
        s = "🟢" if disp > 0 else "🔴"
        msg += f"{s} {info['label'][:18]:<18} ${info['usd']:>5} ${real:>5.0f} ${disp:>5.0f}\n"

    msg += "─" * 44 + "\n"
    msg += f"{'TOTAL':<20} ${total_budget:>5} ${total_real:>5.0f} ${total_budget - total_real:>5.0f}\n"
    msg += f"\n🎯 Tope: ${BUDGET_LIMIT_USD:,} USD | Gastado: ${total_real:,.0f} | Libre: ${BUDGET_LIMIT_USD - total_real:,.0f}"

    await update.message.reply_text(msg, parse_mode=None)

async def cmd_historial(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_month_expenses()[:20]
    if not rows:
        await update.message.reply_text("No hay gastos este mes.")
        return

    msg = "📜 Últimos 20 gastos\n\n"
    for eid, user, fecha, cop, usd, cat, nota in rows:
        label = BUDGET.get(cat, {}).get("label", cat)
        msg += f"#{eid} | {fecha} | {format_cop(cop)} (${usd:.0f}) | {label}"
        if nota:
            msg += f" | {nota}"
        msg += f" | {user}\n"

    await update.message.reply_text(msg)

async def cmd_borrar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /borrar ID\nEjemplo: /borrar 15")
        return
    try:
        eid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("ID debe ser un número.")
        return

    if delete_expense(eid):
        await update.message.reply_text(f"🗑️ Gasto #{eid} eliminado.")
    else:
        await update.message.reply_text(f"Gasto #{eid} no encontrado.")

async def cmd_exportar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rows = get_month_expenses()
    if not rows:
        await update.message.reply_text("No hay gastos este mes.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Usuario", "Fecha", "Monto_COP", "Monto_USD", "Categoría", "Nota"])
    for row in rows:
        writer.writerow(row)

    output.seek(0)
    now = datetime.now()
    filename = f"gastos_{now.strftime('%Y_%m')}.csv"

    await update.message.reply_document(
        document=io.BytesIO(output.getvalue().encode("utf-8")),
        filename=filename,
        caption=f"📁 Exportación {now.strftime('%B %Y')} ({len(rows)} gastos)"
    )

async def cmd_categorias(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    msg = "📂 Categorías válidas\n\n"
    for cat, info in sorted(BUDGET.items(), key=lambda x: x[1]["usd"], reverse=True):
        aliases = [k for k, v in ALIASES.items() if v == cat]
        alias_str = f" ({', '.join(aliases[:4])})" if aliases else ""
        msg += f"• {cat} → {info['label']} [${info['usd']} USD]{alias_str}\n"

    msg += "\n💡 Puedes usar el nombre de la categoría o cualquier alias."
    await update.message.reply_text(msg)

async def cmd_ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

# Quick expense via plain text: "50000 restaurante"
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) >= 2:
        try:
            monto = float(parts[0].replace(".", "").replace(",", ""))
            cat = resolve_category(parts[1])
            if cat:
                nota = " ".join(parts[2:]) if len(parts) > 2 else ""
                user_name = update.effective_user.first_name or "Unknown"
                exp_id, monto_usd = add_expense(update.effective_user.id, user_name, monto, cat, nota)

                month_rows = get_month_expenses()
                total_month_usd = sum(r[4] for r in month_rows)
                global_pct = total_month_usd / BUDGET_LIMIT_USD
                global_status = "🟢" if global_pct < 0.7 else "🟡" if global_pct < 1.0 else "🔴"

                await update.message.reply_text(
                    f"✅ #{exp_id} | {format_cop(monto)} (${monto_usd:.0f} USD) → {BUDGET[cat]['label']}\n"
                    f"{global_status} Mes: ${total_month_usd:.0f}/${BUDGET_LIMIT_USD} ({global_pct:.0%})"
                )
                return
        except (ValueError, IndexError):
            pass

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("gasto", cmd_gasto))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("semana", cmd_semana))
    app.add_handler(CommandHandler("presupuesto", cmd_presupuesto))
    app.add_handler(CommandHandler("historial", cmd_historial))
    app.add_handler(CommandHandler("borrar", cmd_borrar))
    app.add_handler(CommandHandler("exportar", cmd_exportar))
    app.add_handler(CommandHandler("categorias", cmd_categorias))
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    app.add_handler(CommandHandler("help", cmd_ayuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"🤖 Bot iniciado | TRM: {TRM} | Budget: ${BUDGET_LIMIT_USD} USD | DB: {DB_PATH}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
