import os
import time
import hmac
import hashlib
import json
import asyncio
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DB_PATH = BASE_DIR / "lumaqix.db"
app = FastAPI(title="Lumaqix")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance INTEGER DEFAULT 0,
        invited_by INTEGER,
        last_daily INTEGER DEFAULT 0,
        created_at INTEGER
    )""")
    conn.commit()
    conn.close()

def validate_init_data(init_data):
    if not init_data or not BOT_TOKEN:
        return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", None)
        if not received_hash:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return None
        return json.loads(data.get("user", "{}"))
    except Exception:
        return None

def create_or_get_user(user, start_param=None):
    telegram_id = int(user["id"])
    username = user.get("username", "")
    first_name = user.get("first_name", "")
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if row:
        conn.execute("UPDATE users SET username=?, first_name=? WHERE telegram_id=?",
                     (username, first_name, telegram_id))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        conn.close()
        return row
    invited_by = None
    try:
        ref_id = int(start_param) if start_param else None
        if ref_id and ref_id != telegram_id:
            if conn.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (ref_id,)).fetchone():
                invited_by = ref_id
                conn.execute("UPDATE users SET balance=balance+500 WHERE telegram_id=?", (ref_id,))
    except Exception:
        pass
    conn.execute("""INSERT INTO users
        (telegram_id,username,first_name,balance,invited_by,last_daily,created_at)
        VALUES (?,?,?,?,?,?,?)""",
        (telegram_id, username, first_name, 0, invited_by, 0, int(time.time())))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    conn.close()
    return row

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🚀 Open Lumaqix",
                 web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "🌟 Welcome to Lumaqix!\n\nTap the button below to open the Mini App.",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def run_bot():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing.")
        return
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start_command))
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
    try:
        asyncio.run(run_bot())
    except Exception as e:
        print(f"Telegram bot error: {e}")

@app.get("/")
async def root():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/api/me")
async def api_me(request: Request):
    user = validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid Telegram init data"}, status_code=401)
    parsed = dict(parse_qsl(request.headers.get("X-Telegram-Init-Data", ""), keep_blank_values=True))
    row = create_or_get_user(user, parsed.get("start_param"))
    return {"ok": True, "user": {"id": row["telegram_id"], "username": row["username"],
            "first_name": row["first_name"], "balance": row["balance"]}}

@app.post("/api/daily")
async def api_daily(request: Request):
    user = validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid Telegram init data"}, status_code=401)
    uid = int(user["id"])
    now = int(time.time())
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    if now - row["last_daily"] < 86400:
        remaining = 86400 - (now - row["last_daily"])
        balance = row["balance"]
        conn.close()
        return {"ok": False, "claimed": False, "remaining": remaining, "balance": balance}
    conn.execute("UPDATE users SET balance=balance+100,last_daily=? WHERE telegram_id=?", (now, uid))
    conn.commit()
    balance = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (uid,)).fetchone()["balance"]
    conn.close()
    return {"ok": True, "claimed": True, "reward": 100, "balance": balance}

@app.get("/api/leaderboard")
async def api_leaderboard():
    conn = get_db()
    rows = conn.execute("SELECT username,first_name,balance FROM users ORDER BY balance DESC LIMIT 20").fetchall()
    conn.close()
    return {"ok": True, "leaderboard": [
        {"rank": i, "name": r["username"] or r["first_name"] or "User", "balance": r["balance"]}
        for i, r in enumerate(rows, 1)
    ]}

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

if __name__ == "__main__":
    import threading
    import uvicorn
    init_db()
    threading.Thread(target=start_bot, name="run_bot", daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
