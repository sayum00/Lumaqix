```python
import os
import time
import hmac
import hashlib
import json
import sqlite3
import asyncio
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DB_PATH = BASE_DIR / "lumaqix.db"

app = FastAPI(title="Lumaqix")


# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 0,
            invited_by INTEGER,
            last_daily INTEGER DEFAULT 0,
            created_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


# =========================
# TELEGRAM MINI APP AUTH
# =========================

def validate_init_data(init_data: str):
    if not init_data or not BOT_TOKEN:
        return None

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        user_data = json.loads(data.get("user", "{}"))

        return user_data

    except Exception:
        return None


# =========================
# USER FUNCTIONS
# =========================

def create_or_get_user(user, start_param=None):
    telegram_id = int(user["id"])
    username = user.get("username", "")
    first_name = user.get("first_name", "")

    conn = get_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?
            WHERE telegram_id = ?
            """,
            (username, first_name, telegram_id)
        )

        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        conn.close()
        return row

    invited_by = None

    if start_param:
        try:
            ref_id = int(start_param)

            if ref_id != telegram_id:
                inviter = conn.execute(
                    "SELECT telegram_id FROM users WHERE telegram_id = ?",
                    (ref_id,)
                ).fetchone()

                if inviter:
                    invited_by = ref_id

                    conn.execute(
                        """
                        UPDATE users
                        SET balance = balance + 500
                        WHERE telegram_id = ?
                        """,
                        (ref_id,)
                    )

        except Exception:
            invited_by = None

    now = int(time.time())

    conn.execute(
        """
        INSERT INTO users
        (
            telegram_id,
            username,
            first_name,
            balance,
            invited_by,
            last_daily,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            telegram_id,
            username,
            first_name,
            0,
            invited_by,
            0,
            now
        )
    )

    conn.commit()

    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return row


# =========================
# TELEGRAM BOT
# =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 Open Lumaqix",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🌟 Welcome to Lumaqix!\n\n"
        "Tap the button below to open the Mini App.",
        reply_markup=reply_markup
    )


async def run_bot():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing.")
        return

    telegram_app = Application.builder().token(BOT_TOKEN).build()

    telegram_app.add_handler(
        CommandHandler("start", start_command)
    )

    print("Telegram bot starting...")

    await telegram_app.initialize()
    await telegram_app.start()

    if telegram_app.updater:
        await telegram_app.updater.start_polling()

    print("Telegram bot is running.")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        if telegram_app.updater:
            await telegram_app.updater.stop()

        await telegram_app.stop()
        await telegram_app.shutdown()


def start_bot():
    """
    Run Telegram bot in its own thread with its own event loop.
    This fixes:
    'There is no current event loop in thread'
    """

    try:
        asyncio.run(run_bot())
    except Exception as e:
        print(f"Telegram bot error: {e}")


# =========================
# API
# =========================

@app.get("/")
async def root():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/me")
async def api_me(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data", "")

    user = validate_init_data(init_data)

    if not user:
        return JSONResponse(
            {
                "ok": False,
                "error": "Invalid Telegram init data"
            },
            status_code=401
        )

    start_param = None

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        start_param = parsed.get("start_param")
    except Exception:
        pass

    row = create_or_get_user(user, start_param)

    return {
        "ok": True,
        "user": {
            "id": row["telegram_id"],
            "username": row["username"],
            "first_name": row["first_name"],
            "balance": row["balance"]
        }
    }


@app.post("/api/daily")
async def api_daily(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data", "")

    user = validate_init_data(init_data)

    if not user:
        return JSONResponse(
            {
                "ok": False,
                "error": "Invalid Telegram init data"
            },
            status_code=401
        )

    telegram_id = int(user["id"])
    now = int(time.time())

    conn = get_db()

    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not row:
        conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error": "User not found"
            },
            status_code=404
        )

    if now - row["last_daily"] < 86400:
        remaining = 86400 - (now - row["last_daily"])

        conn.close()

        return {
            "ok": False,
            "claimed": False,
            "remaining": remaining,
            "balance": row["balance"]
        }

    conn.execute(
        """
        UPDATE users
        SET balance = balance + 100,
            last_daily = ?
        WHERE telegram_id = ?
        """,
        (now, telegram_id)
    )

    conn.commit()

    new_row = conn.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return {
        "ok": True,
        "claimed": True,
        "reward": 100,
        "balance": new_row["balance"]
    }


@app.get("/api/leaderboard")
async def api_leaderboard():
    conn = get_db()

    rows = conn.execute(
        """
        SELECT
            username,
            first_name,
            balance
        FROM users
        ORDER BY balance DESC
        LIMIT 20
        """
    ).fetchall()

    conn.close()

    leaderboard = []

    for index, row in enumerate(rows, start=1):
        name = row["username"] or row["first_name"] or "User"

        leaderboard.append(
            {
                "rank": index,
                "name": name,
                "balance": row["balance"]
            }
        )

    return {
        "ok": True,
        "leaderboard": leaderboard
    }


# =========================
# STATIC FILES
# =========================

app.mount(
    "/static",
    StaticFiles(directory=WEB_DIR),
    name="static"
)


# =========================
# START SERVER
# =========================

if __name__ == "__main__":
    import threading
    import uvicorn

    init_db()

    bot_thread = threading.Thread(
        target=start_bot,
        name="run_bot",
        daemon=True
    )

    bot_thread.start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000"))
    )
```
    
