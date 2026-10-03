import os
import time
import hmac
import hashlib
import json
import asyncio
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "")
NOWPAYMENTS_PAY_CURRENCY = os.getenv("NOWPAYMENTS_PAY_CURRENCY", "usdtbsc")
NOWPAYMENTS_PRICE_CURRENCY = os.getenv("NOWPAYMENTS_PRICE_CURRENCY", "usd")
NOWPAYMENTS_IPN_URL = os.getenv("NOWPAYMENTS_IPN_URL", f"{WEBAPP_URL.rstrip('/')}/api/nowpayments/ipn")
REFERRAL_COMMISSION_PERCENT = Decimal(os.getenv("REFERRAL_COMMISSION_PERCENT", "5"))
DAILY_REWARD_USD = Decimal(os.getenv("DAILY_REWARD_USD", "0"))
NOWPAYMENTS_JWT = os.getenv("NOWPAYMENTS_JWT", "")
REFERRAL_BONUS_USD = Decimal(os.getenv("REFERRAL_BONUS_USD", "0"))
MIN_DEPOSIT_USD = Decimal(os.getenv("MIN_DEPOSIT_USD", "5"))
MIN_WITHDRAW_USD = Decimal(os.getenv("MIN_WITHDRAW_USD", "10"))
MAX_WITHDRAW_USD = Decimal(os.getenv("MAX_WITHDRAW_USD", "1000"))
WITHDRAW_FEE_USD = Decimal(os.getenv("WITHDRAW_FEE_USD", "0"))

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DB_PATH = BASE_DIR / "lumaqix.db"
app = FastAPI(title="Lumaqix")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def qmoney(value):
    return float(Decimal(str(value)).quantize(Decimal("0.00000001")))


def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance REAL DEFAULT 0,
        invited_by INTEGER,
        last_daily INTEGER DEFAULT 0,
        created_at INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        telegram_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        status TEXT NOT NULL,
        external_id TEXT,
        meta_json TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_external ON transactions(external_id) WHERE external_id IS NOT NULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
        id TEXT PRIMARY KEY,
        telegram_id INTEGER NOT NULL,
        address TEXT NOT NULL,
        amount REAL NOT NULL,
        fee REAL NOT NULL,
        status TEXT NOT NULL,
        external_id TEXT,
        txid TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_withdraw_external ON withdrawals(external_id) WHERE external_id IS NOT NULL")
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


def auth_user(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user = validate_init_data(init_data)
    return user


def create_or_get_user(user, start_param=None):
    telegram_id = int(user["id"])
    username = user.get("username", "")
    first_name = user.get("first_name", "")
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if row:
        conn.execute("UPDATE users SET username=?, first_name=? WHERE telegram_id=?", (username, first_name, telegram_id))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        conn.close()
        return row
    invited_by = None
    if start_param:
        try:
            ref_id = int(start_param)
            if ref_id != telegram_id and conn.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (ref_id,)).fetchone():
                invited_by = ref_id
        except Exception:
            pass
    now = int(time.time())
    conn.execute("INSERT INTO users (telegram_id,username,first_name,balance,invited_by,last_daily,created_at) VALUES (?,?,?,?,?,?,?)",
                 (telegram_id, username, first_name, 0, invited_by, 0, now))
    if invited_by and REFERRAL_BONUS_USD > 0:
        conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (qmoney(REFERRAL_BONUS_USD), invited_by))
        txid = str(uuid.uuid4())
        conn.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (txid, invited_by, "referral_bonus", qmoney(REFERRAL_BONUS_USD), "USD", "completed", None,
                      json.dumps({"referred_user": telegram_id}), now, now))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    conn.close()
    return row


def add_balance(conn, uid, amount):
    conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (qmoney(amount), uid))


def canonicalize(obj):
    if isinstance(obj, dict):
        return {k: canonicalize(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [canonicalize(x) for x in obj]
    return obj


def verify_nowpayments_ipn(payload, signature):
    if not NOWPAYMENTS_IPN_SECRET or not signature:
        return False
    normalized = json.dumps(canonicalize(payload), separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(NOWPAYMENTS_IPN_SECRET.encode(), normalized.encode(), hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


async def nowpayments_create_payment(amount_usd, order_id, description):
    if not NOWPAYMENTS_API_KEY:
        raise RuntimeError("NOWPAYMENTS_API_KEY is not configured")
    payload = {
        "price_amount": qmoney(amount_usd),
        "price_currency": NOWPAYMENTS_PRICE_CURRENCY,
        "pay_currency": NOWPAYMENTS_PAY_CURRENCY,
        "ipn_callback_url": NOWPAYMENTS_IPN_URL,
        "order_id": order_id,
        "order_description": description,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post("https://api.nowpayments.io/v1/payment", headers={"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}, json=payload)
        r.raise_for_status()
        return r.json()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_param = context.args[0] if context.args else None
    if update.effective_user:
        create_or_get_user(update.effective_user.to_dict(), start_param)
    keyboard = [[InlineKeyboardButton("🚀 Open Lumaqix", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text("🌟 Welcome to Lumaqix!\n\nTap the button below to open the Mini App.", reply_markup=InlineKeyboardMarkup(keyboard))


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
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"ok": True, "service": "Lumaqix API", "message": "web/ is not included in the uploaded ZIP"})


@app.get("/api/me")
async def api_me(request: Request):
    user = auth_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid Telegram init data"}, status_code=401)
    parsed = dict(parse_qsl(request.headers.get("X-Telegram-Init-Data", ""), keep_blank_values=True))
    row = create_or_get_user(user, parsed.get("start_param"))
    return {"ok": True, "user": {"id": row["telegram_id"], "username": row["username"], "first_name": row["first_name"], "balance": row["balance"]}}


@app.post("/api/daily")
async def api_daily(request: Request):
    user = auth_user(request)
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
    reward = DAILY_REWARD_USD
    if reward <= 0:
        conn.close()
        return {"ok": False, "claimed": False, "remaining": 0, "balance": row["balance"], "message": "Daily reward is disabled"}
    conn.execute("UPDATE users SET balance=balance+?,last_daily=? WHERE telegram_id=?", (qmoney(reward), now, uid))
    txid = str(uuid.uuid4())
    conn.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", (txid, uid, "daily", qmoney(reward), "USD", "completed", None, None, now, now))
    conn.commit()
    balance = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (uid,)).fetchone()["balance"]
    conn.close()
    return {"ok": True, "claimed": True, "reward": qmoney(reward), "balance": balance}


@app.get("/api/referral")
async def api_referral(request: Request):
    user = auth_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid Telegram init data"}, status_code=401)
    uid = int(user["id"])
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) AS c FROM users WHERE invited_by=?", (uid,)).fetchone()["c"]
    earnings = conn.execute("SELECT COALESCE(SUM(amount),0) AS s FROM transactions WHERE telegram_id=? AND type='referral_commission' AND status='completed'", (uid,)).fetchone()["s"]
    conn.close()
    return {"ok": True, "referral_link": f"https://t.me/{BOT_USERNAME}?start={uid}", "count": count, "earnings": earnings, "commission_percent": qmoney(REFERRAL_COMMISSION_PERCENT)}


@app.get("/api/transactions")
async def api_transactions(request: Request):
    user = auth_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid Telegram init data"}, status_code=401)
    conn = get_db()
    rows = conn.execute("SELECT id,type,amount,currency,status,external_id,created_at FROM transactions WHERE telegram_id=? ORDER BY created_at DESC LIMIT 100", (int(user["id"]),)).fetchall()
    conn.close()
    return {"ok": True, "transactions": [dict(r) for r in rows]}


@app.post("/api/deposit")
async def api_deposit(request: Request):
    user = auth_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid Telegram init data"}, status_code=401)
    try:
        body = await request.json()
        amount = Decimal(str(body.get("amount", "0")))
    except (InvalidOperation, ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "Invalid amount"}, status_code=400)
    if amount < MIN_DEPOSIT_USD:
        return JSONResponse({"ok": False, "error": f"Minimum deposit is {MIN_DEPOSIT_USD}"}, status_code=400)
    uid = int(user["id"])
    order_id = f"DEP-{uid}-{uuid.uuid4().hex[:16]}"
    conn = get_db()
    now = int(time.time())
    conn.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), uid, "deposit", qmoney(amount), "USD", "pending", order_id, None, now, now))
    conn.commit()
    conn.close()
    try:
        payment = await nowpayments_create_payment(amount, order_id, f"Lumaqix deposit for Telegram user {uid}")
    except Exception as e:
        conn = get_db()
        conn.execute("UPDATE transactions SET status='failed',meta_json=?,updated_at=? WHERE external_id=?", (json.dumps({"error": str(e)}), int(time.time()), order_id))
        conn.commit(); conn.close()
        return JSONResponse({"ok": False, "error": "Payment provider error"}, status_code=502)
    conn = get_db()
    conn.execute("UPDATE transactions SET external_id=?,meta_json=?,updated_at=? WHERE external_id=?", (str(payment.get("payment_id")), json.dumps(payment), int(time.time()), order_id))
    conn.commit(); conn.close()
    return {"ok": True, "payment": {"payment_id": payment.get("payment_id"), "pay_address": payment.get("pay_address"), "pay_amount": payment.get("pay_amount"), "pay_currency": payment.get("pay_currency"), "payment_status": payment.get("payment_status"), "order_id": order_id}}


@app.post("/api/nowpayments/ipn")
async def nowpayments_ipn(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    signature = request.headers.get("x-nowpayments-sig", "")
    if not verify_nowpayments_ipn(payload, signature):
        return JSONResponse({"ok": False, "error": "Invalid signature"}, status_code=401)
    payment_id = str(payload.get("payment_id") or "")
    order_id = str(payload.get("order_id") or "")
    status = str(payload.get("payment_status") or "").lower()
    conn = get_db()
    tx = conn.execute("SELECT * FROM transactions WHERE external_id=? OR external_id=?", (payment_id, order_id)).fetchone()
    if not tx:
        conn.close()
        return {"ok": True, "ignored": True}
    now = int(time.time())
    if status == "finished" and tx["status"] != "completed":
        add_balance(conn, tx["telegram_id"], Decimal(str(tx["amount"])))
        conn.execute("UPDATE transactions SET status='completed',meta_json=?,updated_at=? WHERE id=?", (json.dumps(payload), now, tx["id"]))
        ref = conn.execute("SELECT invited_by FROM users WHERE telegram_id=?", (tx["telegram_id"],)).fetchone()
        if ref and ref["invited_by"] and REFERRAL_COMMISSION_PERCENT > 0:
            commission = Decimal(str(tx["amount"])) * REFERRAL_COMMISSION_PERCENT / Decimal("100")
            if commission > 0:
                add_balance(conn, ref["invited_by"], commission)
                rid = str(uuid.uuid4())
                conn.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", (rid, ref["invited_by"], "referral_commission", qmoney(commission), "USD", "completed", payment_id, json.dumps({"deposit_user": tx["telegram_id"]}), now, now))
    elif tx["status"] not in ("completed", "failed", "expired", "refunded"):
        conn.execute("UPDATE transactions SET status=?,meta_json=?,updated_at=? WHERE id=?", (status or "unknown", json.dumps(payload), now, tx["id"]))
    conn.commit(); conn.close()
    return {"ok": True}


@app.post("/api/withdraw")
async def api_withdraw(request: Request):
    user = auth_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid Telegram init data"}, status_code=401)
    try:
        body = await request.json()
        amount = Decimal(str(body.get("amount", "0")))
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid amount"}, status_code=400)
    address = str(body.get("address", "")).strip()
    if not address:
        return JSONResponse({"ok": False, "error": "BEP-20 address is required"}, status_code=400)
    if amount < MIN_WITHDRAW_USD or amount > MAX_WITHDRAW_USD:
        return JSONResponse({"ok": False, "error": f"Withdrawal must be between {MIN_WITHDRAW_USD} and {MAX_WITHDRAW_USD}"}, status_code=400)
    uid = int(user["id"])
    total = amount + WITHDRAW_FEE_USD
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (uid,)).fetchone()
    if not row or Decimal(str(row["balance"])) < total:
        conn.rollback(); conn.close()
        return JSONResponse({"ok": False, "error": "Insufficient balance"}, status_code=400)
    wid = str(uuid.uuid4())
    now = int(time.time())
    conn.execute("UPDATE users SET balance=balance-? WHERE telegram_id=?", (qmoney(total), uid))
    conn.execute("INSERT INTO withdrawals VALUES (?,?,?,?,?,?,?,?,?,?)", (wid, uid, address, qmoney(amount), qmoney(WITHDRAW_FEE_USD), "pending", None, None, now, now))
    conn.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), uid, "withdrawal", qmoney(-total), "USD", "pending", wid, json.dumps({"address": address}), now, now))
    conn.commit(); conn.close()
    if NOWPAYMENTS_API_KEY and NOWPAYMENTS_JWT:
        try:
            payload = {"ipn_callback_url": f"{WEBAPP_URL.rstrip('/')}/api/nowpayments/payout-ipn", "withdrawals": [{"address": address, "currency": NOWPAYMENTS_PAY_CURRENCY, "amount": qmoney(amount)}]}
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post("https://api.nowpayments.io/v1/payout", headers={"x-api-key": NOWPAYMENTS_API_KEY, "Authorization": f"Bearer {NOWPAYMENTS_JWT}", "Content-Type": "application/json"}, json=payload)
                r.raise_for_status()
                payout = r.json()
            external_id = str(payout.get("id") or payout.get("payout_id") or wid)
            txid = payout.get("txid")
            conn = get_db()
            conn.execute("UPDATE withdrawals SET status='processing',external_id=?,txid=?,updated_at=? WHERE id=?", (external_id, txid, int(time.time()), wid))
            conn.execute("UPDATE transactions SET status='processing',external_id=?,updated_at=? WHERE external_id=?", (external_id, int(time.time()), wid))
            conn.commit(); conn.close()
            return {"ok": True, "withdrawal_id": wid, "status": "processing", "payout": payout}
        except Exception as e:
            conn = get_db()
            conn.execute("UPDATE withdrawals SET status='failed',updated_at=? WHERE id=?", (int(time.time()), wid))
            conn.execute("UPDATE transactions SET status='failed',meta_json=?,updated_at=? WHERE external_id=?", (json.dumps({"error": str(e)}), int(time.time()), wid))
            add_balance(conn, uid, total)
            conn.commit(); conn.close()
            return JSONResponse({"ok": False, "error": "Payout provider error; reserved balance was returned"}, status_code=502)
    return {"ok": True, "withdrawal_id": wid, "status": "pending", "message": "Withdrawal request created. Automatic payout is enabled only when NOWPAYMENTS_JWT is configured."}


@app.post("/api/nowpayments/payout-ipn")
async def nowpayments_payout_ipn(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    signature = request.headers.get("x-nowpayments-sig", "")
    if not verify_nowpayments_ipn(payload, signature):
        return JSONResponse({"ok": False, "error": "Invalid signature"}, status_code=401)
    external_id = str(payload.get("id") or payload.get("payout_id") or "")
    status = str(payload.get("status") or payload.get("payout_status") or "").lower()
    conn = get_db()
    row = conn.execute("SELECT * FROM withdrawals WHERE external_id=?", (external_id,)).fetchone()
    if row:
        now = int(time.time())
        final = "completed" if status in ("finished", "completed", "success") else ("failed" if status in ("failed", "rejected", "refunded") else "processing")
        conn.execute("UPDATE withdrawals SET status=?,txid=?,updated_at=? WHERE id=?", (final, payload.get("txid"), now, row["id"]))
        conn.execute("UPDATE transactions SET status=?,meta_json=?,updated_at=? WHERE external_id=?", (final, json.dumps(payload), now, external_id))
        if final == "failed":
            add_balance(conn, row["telegram_id"], Decimal(str(row["amount"])) + Decimal(str(row["fee"])))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/admin/overview")
async def admin_overview(request: Request):
    user = auth_user(request)
    if not user or int(user["id"]) not in ADMIN_IDS:
        return JSONResponse({"ok": False, "error": "Admin only"}, status_code=403)
    conn = get_db()
    data = {
        "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "balance_total": conn.execute("SELECT COALESCE(SUM(balance),0) s FROM users").fetchone()["s"],
        "deposits": conn.execute("SELECT COUNT(*) c FROM transactions WHERE type='deposit' AND status='completed'").fetchone()["c"],
        "withdrawals_pending": conn.execute("SELECT COUNT(*) c FROM withdrawals WHERE status='pending'").fetchone()["c"],
        "referral_commissions": conn.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE type='referral_commission' AND status='completed'").fetchone()["s"],
    }
    conn.close()
    return {"ok": True, "overview": data}


@app.get("/api/leaderboard")
async def api_leaderboard():
    conn = get_db()
    rows = conn.execute("SELECT username,first_name,balance FROM users ORDER BY balance DESC LIMIT 20").fetchall()
    conn.close()
    return {"ok": True, "leaderboard": [{"rank": i, "name": r["username"] or r["first_name"] or "User", "balance": r["balance"]} for i, r in enumerate(rows, 1)]}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    import threading
    import uvicorn
    init_db()
    threading.Thread(target=start_bot, name="run_bot", daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
