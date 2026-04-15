#!/usr/bin/env python3
"""
One-shot recovery: reinsert April 2026 expenses from the CSV backup
provided by the user (gastos_2026_04.csv exported before the data loss).

Runs LOCALLY on the laptop — pushes the work into the production container
via `railway ssh` with the CSV embedded as base64 in the command argv.

Safety:
- Refuses to run if the target table already has rows for April 2026
  (idempotent: second run is a no-op unless you pass --force).
- Wraps inserts in a transaction. Either all 30 rows land or none do.
- Preserves the original IDs from the CSV so that any /borrar references,
  screenshots, etc. stay valid.
- Bumps sqlite_sequence so new autoincrement IDs continue after 33.
- Does not touch schema, does not DROP anything.
"""
import base64
import csv
import subprocess
import sys
from pathlib import Path

CSV_PATH = Path.home() / "Downloads" / "Telegram Desktop" / "gastos_2026_04.csv"
DB_PATH_REMOTE = "/data/expenses.db"


def build_rows():
    """Parse the CSV the user provided. Returns list of tuples ready for INSERT."""
    rows = []
    with CSV_PATH.open() as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            if not r.get("ID"):
                continue
            rid = int(r["ID"])
            user_name = r["Usuario"]
            fecha = r["Fecha"]
            monto_cop = float(r["Monto_COP"])
            monto_usd = float(r["Monto_USD"])
            categoria = r["Categoría"]
            nota = r.get("Nota", "") or ""
            # Fabricate a created_at timestamp for the row. The original is
            # lost; we use fecha + noon so ORDER BY created_at stays sensible.
            created_at = f"{fecha}T12:00:00"
            # user_id is not in the CSV. We leave it NULL — the bot's queries
            # all key off user_name for display, so this is non-breaking.
            # metodo_pago defaults to 'Sin especificar' because the CSV was
            # exported before the payment-method feature existed.
            rows.append((rid, None, user_name, fecha, monto_cop, monto_usd,
                         categoria, nota, created_at, "Sin especificar"))
    rows.sort(key=lambda t: t[0])
    return rows


REMOTE_SCRIPT = r"""
import base64, json, sqlite3, sys
payload_b64 = sys.argv[1]
force = "--force" in sys.argv
rows = json.loads(base64.b64decode(payload_b64).decode())
DB = "__DB_PATH__"
conn = sqlite3.connect(DB)
cur = conn.cursor()
# Safety check: refuse if April 2026 already has rows unless --force
existing = cur.execute(
    "SELECT COUNT(*) FROM expenses WHERE fecha LIKE '2026-04-%'"
).fetchone()[0]
if existing and not force:
    print(f"ABORT: table already has {existing} rows for 2026-04. "
          "Pass --force to overwrite (will DELETE first).")
    sys.exit(2)
try:
    cur.execute("BEGIN")
    if force and existing:
        cur.execute("DELETE FROM expenses WHERE fecha LIKE '2026-04-%'")
    for r in rows:
        cur.execute(
            "INSERT INTO expenses "
            "(id, user_id, user_name, fecha, monto_cop, monto_usd, "
            "categoria, nota, created_at, metodo_pago) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            r,
        )
    # Bump sqlite_sequence so autoincrement continues past the highest id.
    max_id = max(r[0] for r in rows)
    seq_row = cur.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='expenses'"
    ).fetchone()
    if seq_row is None:
        cur.execute(
            "INSERT INTO sqlite_sequence(name, seq) VALUES ('expenses', ?)",
            (max_id,),
        )
    elif seq_row[0] < max_id:
        cur.execute(
            "UPDATE sqlite_sequence SET seq=? WHERE name='expenses'",
            (max_id,),
        )
    conn.commit()
except Exception as e:
    conn.rollback()
    print("ROLLBACK:", repr(e))
    sys.exit(3)
# Report
count = cur.execute(
    "SELECT COUNT(*) FROM expenses WHERE fecha LIKE '2026-04-%'"
).fetchone()[0]
total_usd = cur.execute(
    "SELECT COALESCE(SUM(monto_usd),0) FROM expenses WHERE fecha LIKE '2026-04-%'"
).fetchone()[0]
print(f"OK: inserted {len(rows)} rows; table now has {count} April rows; "
      f"sum(monto_usd) = {total_usd:.2f}")
seq_row = cur.execute(
    "SELECT seq FROM sqlite_sequence WHERE name='expenses'"
).fetchone()
print(f"sqlite_sequence(expenses).seq = {seq_row[0] if seq_row else None}")
conn.close()
""".replace("__DB_PATH__", DB_PATH_REMOTE)


def main():
    rows = build_rows()
    import json
    payload = base64.b64encode(json.dumps(rows).encode()).decode()
    print(f"[local] parsed {len(rows)} rows from {CSV_PATH}")
    print(f"[local] payload size: {len(payload)} chars (base64)")

    force_flag = "--force" if "--force" in sys.argv else ""
    import shlex
    script_quoted = shlex.quote(REMOTE_SCRIPT)
    shell_cmd = f"python3 -c {script_quoted} {payload} {force_flag}".strip()
    print(f"[local] invoking railway ssh (cmd length: {len(shell_cmd)})")
    result = subprocess.run(
        ["railway", "ssh", shell_cmd],
        capture_output=True,
        text=True,
        cwd=str(Path.home() / "dev" / "expense-bot"),
    )
    print("--- remote stdout ---")
    print(result.stdout)
    if result.stderr:
        print("--- remote stderr ---")
        print(result.stderr)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
