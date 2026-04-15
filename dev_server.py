"""Local dev server for the dashboard UI + API.

Runs api.py's FastAPI app on localhost without starting the Telegram
polling loop, so it is safe to run alongside the production bot without
Conflict errors against Telegram's getUpdates lock.

    python3 dev_server.py

Writes to a local SQLite DB at ~/dev/expense-bot-dev.db by default so
it never touches the production volume on Railway. Override with DB_PATH
env var if you want to point at a copy of the real data.
"""

import os
import sys
from pathlib import Path

# Stub a fake token so bot.py's `TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]`
# module-load line doesn't crash. We won't use PTB here.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "local-dev-not-a-real-token")

# Local dev DB lives OUTSIDE the repo so it doesn't get committed.
_default_db = str(Path.home() / "dev" / "expense-bot-dev.db")
os.environ.setdefault("DB_PATH", _default_db)

# Make sure we can import bot.py / api.py from this script's dir.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

# Tell api.py where the static folder is (relative to this script, not cwd).
os.environ.setdefault("STATIC_DIR", str(HERE / "static"))

import bot  # noqa: E402
import api  # noqa: E402

import uvicorn  # noqa: E402

if __name__ == "__main__":
    bot.init_db()
    bot.load_config()
    app = api.make_api_app()
    print(f"🌐 Dev dashboard on http://127.0.0.1:8080")
    print(f"   DB:     {bot.DB_PATH}")
    print(f"   BUDGET: {len(bot.BUDGET)} cats")
    print(f"   PAY:    {len(bot.PAYMENT_METHODS)} groups")
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info", access_log=False)
