# Expense Tracker Bot · González-Guevara

> **Nota 2026-04-15:** Este archivo es el setup histórico del bot (Feb 2026).
> Para la arquitectura actual del sistema — stack, schema, API, migraciones,
> dashboard, métodos de pago, estrategia de ingresos/presupuesto — ver
> [`docs/SYSTEM.md`](docs/SYSTEM.md), que es el documento vivo.

Bot de Telegram para tracking de gastos contra presupuesto de $5,000 USD/mes.

## Setup en 5 minutos

### Paso 1: Crear el bot en Telegram (2 min)

1. Abre Telegram y busca **@BotFather**
2. Envía `/newbot`
3. Nombre: `Gastos GG` (o lo que quieras)
4. Username: `gastos_gg_bot` (debe ser único, agrega números si está tomado)
5. BotFather te dará un **token** tipo `7123456789:AAHxxxxx...` → **guárdalo**

### Paso 2: Obtener tu Telegram User ID (1 min)

1. Busca **@userinfobot** en Telegram
2. Envíale cualquier mensaje
3. Te responde con tu **ID** (número tipo `123456789`)
4. Haz lo mismo desde el Telegram de Mado para obtener su ID

### Paso 3: Deploy en Railway (2 min)

1. Ve a [railway.app](https://railway.app) → Sign up con GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Sube este folder como repo en GitHub (o usa "Empty Project" → "Add Service" → "GitHub Repo")
4. En el servicio desplegado, ve a **Variables** y agrega:

   | Variable | Valor |
   |----------|-------|
   | `TELEGRAM_BOT_TOKEN` | El token de BotFather |
   | `ALLOWED_USER_IDS` | `TU_ID,ID_DE_MADO` |
   | `TRM` | `3700` |

5. Click **Deploy** → en 30 segundos está corriendo

### Alternativa: Deploy en Render

1. Ve a [render.com](https://render.com) → Sign up
2. New → **Background Worker** → Connect GitHub repo
3. Runtime: **Docker**
4. Agrega las mismas variables de entorno
5. Deploy

## Uso diario

### Registrar gastos (dos formas)

```
/gasto 50000 restaurante almuerzo con amigos
/gasto 240000 gasolina
/gasto 1400000 supermercado pricesmart mensual
```

O simplemente escribe sin el `/gasto`:
```
50000 restaurante almuerzo
240000 gas
```

### Consultas

| Comando | Qué hace |
|---------|----------|
| `/resumen` | Resumen del mes con % de cada categoría |
| `/semana` | Gastos de esta semana |
| `/presupuesto` | Tabla budget vs real por categoría |
| `/historial` | Últimos 20 gastos |
| `/exportar` | Descarga CSV del mes |
| `/categorias` | Lista de categorías y aliases |
| `/borrar 15` | Elimina gasto #15 |

### Categorías y aliases

No necesitas recordar el nombre exacto. Puedes usar:

- `rest`, `restaurante`, `comida` → Restaurantes
- `super`, `mercado`, `pricesmart`, `exito` → Supermercado
- `rappi`, `domicilio`, `delivery` → Domicilios
- `gas`, `tanqueo` → Gasolina
- `taxi`, `didi` → Uber
- `cafe`, `starbucks`, `coffee` → Cafeterías
- `gym`, `entreno` → Trainer
- `medico`, `farmacia` → Salud
- Y muchos más... usa `/categorias` para ver todos

### Alertas automáticas

El bot te avisa con colores:
- 🟢 Vas bien (< 70% del presupuesto)
- 🟡 Cuidado (70-100%)
- 🔴 Te pasaste (> 100%)

## Notas técnicas

- Base de datos: SQLite (archivo `expenses.db`)
- El bot corre 24/7 en Railway/Render
- Tier gratis de Railway: 500 horas/mes (suficiente)
- Para backup: usa `/exportar` cada fin de mes
