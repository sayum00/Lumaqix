import os
import time
import hmac
import hashlib
import json
import sqlite3
import threading
from urllib.parse import parse_qsl
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    from telegram.ext import Application, CommandHandler, ContextTypes
except Exception:
    Update = InlineKeyboardButton = InlineKeyboardMarkup = WebAppInfo = Application = CommandHandler = ContextTypes = None

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "app" / "web"
DB_PATH = BASE_DIR / "lumaqix.db"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
PORT = int(os.getenv("PORT", "10000"))

app = FastAPI(title="Lumaqix")
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            last_daily INTEGER DEFAULT 0,
            invited_by INTEGER DEFAULT NULL
        )
    """)
    conn.commit()
    conn.close()


def validate_init_data(init_data: str):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{k}={pairs[k]}" for k in sorted(pairs)
        )
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()
        calculated = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            return None

        user = json.loads(pairs.get("user", "{}"))
        return user, pairs.get("start_param", "")
    except Exception:
        return None


def ensure_user(user, start_param=""):
    user_id = int(user["id"])
    conn = db()
    existing = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    if not existing:
        inviter_id = None
        if start_param:
            try:
                inviter_id = int(start_param)
            except ValueError:
                inviter_id = None

        conn.execute(
            "INSERT INTO users (id, username, first_name, balance, invited_by) VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                user.get("username", ""),
                user.get("first_name", ""),
                0,
                inviter_id,
            ),
        )

        if inviter_id and inviter_id != user_id:
            conn.execute(
                "UPDATE users SET balance = balance + 500 WHERE id=?",
                (inviter_id,),
            )

        conn.commit()

    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row)


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "service": "Lumaqix"}


@app.get("/api/me")
def me(initData: str = ""):
    result = validate_init_data(initData)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData")
    user, start_param = result
    return ensure_user(user, start_param)


@app.post("/api/daily")
def daily(initData: str = ""):
    result = validate_init_data(initData)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData")

    user, start_param = result
    row = ensure_user(user, start_param)
    now = int(time.time())

    if now - int(row["last_daily"]) < 86400:
        remaining = 86400 - (now - int(row["last_daily"]))
        return {"ok": False, "message": "Daily bonus already claimed", "remaining": remaining, "balance": row["balance"]}

    conn = db()
    conn.execute(
        "UPDATE users SET balance = balance + 100, last_daily=? WHERE id=?",
        (now, int(user["id"])),
    )
    conn.commit()
    updated = conn.execute("SELECT balance FROM users WHERE id=?", (int(user["id"]),)).fetchone()
    conn.close()

    return {"ok": True, "bonus": 100, "balance": updated["balance"]}


@app.get("/api/leaderboard")
def leaderboard():
    conn = db()
    rows = conn.execute(
        "SELECT username, first_name, balance FROM users ORDER BY balance DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL:
        await update.message.reply_text(
            "Lumaqix is online, but WEBAPP_URL is not configured yet."
        )
        return

    keyboard = [[
        InlineKeyboardButton(
            "🚀 Open Lumaqix",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]]
    await update.message.reply_text(
        "Welcome to Lumaqix! 🎯\n\nOpen the Mini App below:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def run_bot():
    if not BOT_TOKEN or Application is None:
        print("Telegram bot polling disabled: BOT_TOKEN is missing.")
        return

    try:
        tg_app = Application.builder().token(BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", start))
        print("Lumaqix Telegram bot starting...")
        tg_app.run_polling(drop_pending_updates=True)
    except Exception as e:
        print("Telegram bot error:", e)


init_db()

if __name__ == "__main__":
    if BOT_TOKEN:
        threading.Thread(target=run_bot, daemon=True).start()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
