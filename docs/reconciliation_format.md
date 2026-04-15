# Formato de importación de estados de cuenta (para Sonnet → dashboard)

Este documento define el JSON que el dashboard va a aceptar como input cuando
Daniel suba un estado de cuenta mensual. Daniel extrae el PDF del banco vía
Claude chat (Sonnet) con un prompt que produce este formato, lo copia/descarga,
y lo sube al dashboard. El dashboard ejecuta matching contra la tabla
`expenses` y le muestra las divergencias.

El formato está diseñado para ser:

- **Fácil de generar por un LLM** a partir de un PDF bancario
- **Robusto a las imprecisiones** del bot (Daniel no registra centavos ni el
  último peso exacto en Telegram)
- **Auditable** — cada movimiento trae línea/página del PDF original para que
  Daniel pueda verificar en caso de duda

## JSON schema

```json
{
  "version": 1,
  "source": {
    "bank": "BDB",                              // BDB | BBVA | WIO | ENBD | BANCOLOMBIA | CHASE | ...
    "instrument": "Visa Latam Dani",            // debe coincidir con algún método en PAYMENT_METHODS
    "statement_period": {
      "from": "2026-03-16",                     // ISO date (YYYY-MM-DD)
      "to":   "2026-04-15"                      // ISO date, inclusive
    },
    "currency": "COP",                          // moneda del statement — COP | USD | BOB | AED
    "pdf_file": "BDB_VisaLatamDani_2026-03.pdf",// nombre del archivo original, para trazabilidad
    "pdf_pages": 4                              // número total de páginas del PDF
  },
  "summary": {
    "opening_balance": 0,                       // saldo inicial (no usado para reconciliación, solo metadata)
    "closing_balance": 4820130,                 // saldo final
    "total_charges": 4820130,                   // total cargos del periodo
    "total_payments": 0                         // total pagos/abonos
  },
  "movements": [
    {
      "date": "2026-04-13",                     // ISO date. Usar la fecha de CAUSACIÓN (cuando el comercio hizo el charge), no la de corte ni la de pago
      "description": "PRICESMART BOGOTA",       // texto tal como aparece en el PDF
      "merchant_hint": "supermercado",          // categoría sugerida (opcional, si Sonnet puede inferirla — tabla de aliases abajo)
      "amount": 43400,                          // monto BRUTO del movimiento, SIN signo (siempre positivo)
      "currency": "COP",                        // por si hay mezcla en el mismo statement (ej. CHASE con COP+USD)
      "amount_rounded_tens": 43400,             // redondeado a la decena más cercana — Daniel suele registrar así en Telegram
      "type": "charge",                         // charge | payment | refund | fee | interest
      "foreign": false,                         // true si es transacción en otra moneda que el statement
      "original_amount": null,                  // si foreign=true: monto en moneda original (ej. 10 USD)
      "original_currency": null,                // si foreign=true: currency code (ej. "USD")
      "reference": null,                        // autorización / referencia bancaria si existe
      "pdf_page": 2,                            // página donde aparece
      "pdf_line": 18                            // línea dentro de la página (aproximado, para auditoría)
    }
  ]
}
```

### Campos obligatorios (mínimo viable para matching)

`source.bank`, `source.instrument`, `source.statement_period.from`,
`source.statement_period.to`, `source.currency`, y para cada movimiento:
`date`, `description`, `amount`, `amount_rounded_tens`, `type`.

Todo lo demás es opcional pero ayuda al matching/auditoría. Sonnet puede
omitir los campos que no pueda extraer con alta confianza — mejor un valor
`null` que uno inventado.

## Tabla de aliases para `merchant_hint`

Cuando Sonnet detecta texto en la descripción del PDF, puede mapearlo a una
categoría del bot. Ejemplos:

| Patrón en descripción                      | Categoría sugerida |
|--------------------------------------------|--------------------|
| `PRICESMART`, `EXITO`, `JUMBO`, `OLIMPICA` | `supermercado`     |
| `STARBUCKS`, `JUAN VALDEZ`, `TOSTAO`       | `cafe`             |
| `TERPEL`, `MOBIL`, `PETROBRAS`, `ESSO`     | `gasolina`         |
| `UBER`, `DIDI`, `INDRIVE`, `CABIFY`        | `uber`             |
| `RAPPI`, `IFOOD`, `DIDIFOOD`               | `rappi`            |
| `NETFLIX`, `SPOTIFY`, `APPLE.COM/BILL`     | `suscripciones`    |
| `CLARO`, `MOVISTAR`, `ETB`, `UNE`, `DU`    | `telecom`          |
| `PARQUE`, `PARKING`, `CITYPARKING`         | `parqueadero`      |
| `COLSANITAS`, `CRUZ VERDE`, `FARMATODO`    | `salud`            |
| Nombres de restaurantes                    | `restaurante`      |
| Hoteles, aerolíneas (`AVIANCA`, `LATAM`)   | `viaje`            |

Si Sonnet no puede inferir con confianza, debe dejar `merchant_hint: null` —
**NO inventar**. Mejor que el dashboard deje la categoría sin asignar y
Daniel la clasifique manualmente en el UI de reconciliación.

## Prompt sugerido para Sonnet

Cuando esté listo Fase 3, Daniel puede pegarle a Sonnet algo así junto con
el PDF:

> Extrae todos los movimientos del estado de cuenta adjunto al formato JSON
> que está en este repo:
> `https://github.com/danielgonzalez1237/expense-bot/blob/main/docs/reconciliation_format.md`
>
> - Banco: BDB
> - Instrumento: Visa Latam Dani
>
> Importante:
>
> - Usa la fecha de causación (transacción), no la de corte ni la de pago
> - No inventes `merchant_hint`: si no estás seguro, déjalo null
> - `amount` siempre positivo; usa `type` para distinguir cargo vs pago
> - Si hay transacciones en otra moneda, usa `foreign: true` con el monto
>   original
> - Si el PDF tiene páginas de publicidad o resumen de puntos, ignóralas
>
> Devuelve el JSON crudo, sin bloques de código ni prosa.

## Algoritmo de matching (draft, para cuando implemente Fase 3)

Para cada `movement` del statement cuyo `type == "charge"`:

1. Calcular `(date_window, amount_window, category_window)` tolerancias:
   - **Fecha:** ±2 días (delay entre charge y fecha en Telegram)
   - **Monto:** exacto O dentro de `abs(amount_rounded_tens - expense.monto_cop) < 10`
   - **Instrumento:** exacto si el gasto ya tiene `metodo_pago` seteado; ignorado si es "Sin especificar"

2. Buscar candidatos en `expenses`:
   ```sql
   SELECT id, fecha, monto_cop, categoria, metodo_pago, nota
   FROM expenses
   WHERE fecha BETWEEN ? AND ?
     AND ABS(monto_cop - ?) < 10
     AND (metodo_pago = ? OR metodo_pago = 'Sin especificar')
   ```

3. Clasificar el movimiento:
   - **MATCHED** — 1 sola coincidencia: auto-linkear, marcar gasto con
     `statement_ref` y `statement_matched_at`
   - **AMBIGUOUS** — 2+ coincidencias: mostrar al usuario para que elija
   - **MISSING** — 0 coincidencias: el gasto NO está en el bot, Daniel tiene
     que agregarlo (o decidir que fue un charge que no quiere trackear, ej.
     pago de tarjeta)
   - **EXTRA** — (caso inverso) hay un gasto en `expenses` que no tiene
     contraparte en el statement → posible registro duplicado o fecha mal
     puesta

4. Presentar UI:
   ```
   ┌─ BDB Visa Latam Dani · marzo 2026 ────────────┐
   │  Reconciliación: 28 movimientos               │
   │                                               │
   │  ✅ 22 conciliados automáticamente            │
   │  ⚠️  3 ambiguos (click para resolver)          │
   │  ❌ 2 faltan en el bot (click para agregar)   │
   │  📌 1 extra en el bot (click para revisar)    │
   └───────────────────────────────────────────────┘
   ```

## Schema SQL (para cuando implemente Fase 3)

```sql
CREATE TABLE reconciliation_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank TEXT NOT NULL,
    instrument TEXT NOT NULL,
    period_from TEXT NOT NULL,
    period_to TEXT NOT NULL,
    currency TEXT NOT NULL,
    pdf_filename TEXT,
    imported_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,   -- full JSON as uploaded, for audit
    status TEXT NOT NULL       -- pending | reviewed | finalized
);

CREATE TABLE reconciliation_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL REFERENCES reconciliation_imports(id),
    date TEXT NOT NULL,
    description TEXT,
    amount REAL NOT NULL,
    amount_rounded_tens REAL,
    currency TEXT,
    movement_type TEXT,                        -- charge | payment | refund | fee | interest
    foreign_amount REAL,
    foreign_currency TEXT,
    reference TEXT,
    pdf_page INTEGER,
    pdf_line INTEGER,
    matched_expense_id INTEGER REFERENCES expenses(id),
    match_status TEXT                          -- matched | ambiguous | missing | dismissed
);

-- Add to expenses table:
ALTER TABLE expenses ADD COLUMN statement_ref TEXT;        -- reference to reconciliation_movements.id once linked
ALTER TABLE expenses ADD COLUMN statement_matched_at TEXT; -- ISO timestamp of the match
```
