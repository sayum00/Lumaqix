import os, time, hmac, hashlib, json
from urllib.parse import parse_qsl
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    from telegram.ext import Application, CommandHandler, ContextTypes
except Exception:
    Application = None

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

MIN_DEPOSIT = 2.0
MIN_WITHDRAW = 0.1
DEPOSIT_RATE = 0.027
POINT_RATE = 0.0004
LOCK_SECONDS = 10 * 86400
DAILY_GIFT = 10

app = FastAPI(title="Lumaqix")
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL)

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            points NUMERIC(30,8) DEFAULT 50,
            usdt NUMERIC(30,8) DEFAULT 0,
            coins BIGINT DEFAULT 0,
            invited_by BIGINT,
            gift_boxes INTEGER DEFAULT 0,
            last_daily_gift BIGINT DEFAULT 0,
            withdrawal_used BOOLEAN DEFAULT FALSE,
            created_at BIGINT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS deposits(
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount NUMERIC(30,8) NOT NULL,
            daily_mining NUMERIC(30,8) NOT NULL,
            start_at BIGINT NOT NULL,
            unlock_at BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'locked',
            tx_hash TEXT UNIQUE,
            created_at BIGINT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS withdrawals(
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount NUMERIC(30,8) NOT NULL,
            address TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            tx_hash TEXT UNIQUE,
            created_at BIGINT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS mining_daily(
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            source TEXT NOT NULL,
            source_id BIGINT,
            day TEXT NOT NULL,
            amount NUMERIC(30,8) NOT NULL,
            created_at BIGINT NOT NULL,
            UNIQUE(user_id, source, source_id, day)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS referral_rewards(
            id BIGSERIAL PRIMARY KEY,
            beneficiary_id BIGINT NOT NULL,
            source_user_id BIGINT NOT NULL,
            level INTEGER NOT NULL,
            amount NUMERIC(30,8) NOT NULL,
            source_ref TEXT,
            created_at BIGINT NOT NULL,
            UNIQUE(beneficiary_id, source_user_id, level, source_ref)
        )""")

def validate_init_data(init_data: str):
    if not BOT_TOKEN or not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop("hash", None)
        if not received:
            return None
        check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        user = json.loads(pairs.get("user", "{}"))
        return user, pairs.get("start_param", "")
    except Exception:
        return None

def ensure_user(user, start_param=""):
    uid = int(user["id"])
    now = int(time.time())
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
        if row is None:
            inviter = None
            try:
                if start_param:
                    inviter = int(start_param)
            except ValueError:
                pass
            c.execute("""INSERT INTO users
                (id,username,first_name,invited_by,created_at)
                VALUES(%s,%s,%s,%s,%s)""",
                (uid,user.get("username",""),user.get("first_name",""),inviter,now))
            if inviter and inviter != uid:
                c.execute("""UPDATE users SET coins=coins+10,gift_boxes=gift_boxes+1
                             WHERE id=%s""",(inviter,))
        row = c.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
        return row

@app.get("/")
def home():
    return FileResponse(WEB_DIR/"index.html")

@app.get("/health")
def health():
    return {"status":"ok","service":"Lumaqix","chain_id":os.getenv("BSC_CHAIN_ID","97")}

@app.get("/api/me")
def me(initData: str = Query(default="")):
    result = validate_init_data(initData)
    if not result:
        raise HTTPException(401,"Invalid Telegram initData")
    user,start = result
    row = ensure_user(user,start)
    return dict(row)

@app.post("/api/daily-gift")
def daily_gift(initData: str = Query(default="")):
    result = validate_init_data(initData)
    if not result: raise HTTPException(401,"Invalid Telegram initData")
    user,start = result; ensure_user(user,start); uid=int(user["id"]); now=int(time.time())
    with db() as c:
        row=c.execute("SELECT last_daily_gift FROM users WHERE id=%s",(uid,)).fetchone()
        if now-int(row[0]) < 86400:
            return {"ok":False,"message":"Daily gift already claimed"}
        c.execute("UPDATE users SET coins=coins+%s,last_daily_gift=%s WHERE id=%s",(DAILY_GIFT,now,uid))
        return {"ok":True,"coins":DAILY_GIFT}

@app.post("/api/open-invite-gift")
def open_invite_gift(initData: str = Query(default="")):
    result=validate_init_data(initData)
    if not result: raise HTTPException(401,"Invalid Telegram initData")
    user,start=result; ensure_user(user,start); uid=int(user["id"])
    with db() as c:
        row=c.execute("SELECT gift_boxes FROM users WHERE id=%s",(uid,)).fetchone()
        if int(row[0])<1: return {"ok":False,"message":"No gift box"}
        c.execute("UPDATE users SET gift_boxes=gift_boxes-1,coins=coins+1 WHERE id=%s",(uid,))
        return {"ok":True,"coins":1}

@app.get("/api/referral")
def referral(initData: str = Query(default="")):
    result=validate_init_data(initData)
    if not result: raise HTTPException(401,"Invalid Telegram initData")
    user,start=result; row=ensure_user(user,start); uid=int(user["id"])
    with db() as c:
        refs=c.execute("""SELECT id,username,first_name,created_at
                          FROM users WHERE invited_by=%s ORDER BY created_at DESC""",(uid,)).fetchall()
        return {"rates":{"L1":.10,"L2":.03,"L3":.02,"L4":.01,"L5":.01},
                "direct":[dict(zip(["id","username","first_name","created_at"],r)) for r in refs],
                "gift_boxes":int(row[7])}

@app.post("/api/withdraw")
def withdraw(initData: str = Query(default=""), amount: float = Query(...,gt=0), address: str = Query(...)):
    result=validate_init_data(initData)
    if not result: raise HTTPException(401,"Invalid Telegram initData")
    user,start=result; row=ensure_user(user,start); uid=int(user["id"])
    if amount < MIN_WITHDRAW: raise HTTPException(400,"Minimum withdrawal is 0.1 USDT")
    if not address.startswith("0x") or len(address)!=42: raise HTTPException(400,"Invalid BEP20 address")
    with db() as c:
        r=c.execute("""SELECT usdt,withdrawal_used,
                       EXISTS(SELECT 1 FROM deposits d WHERE d.user_id=u.id AND d.amount>=%s)
                       FROM users u WHERE id=%s""",(MIN_DEPOSIT,uid)).fetchone()
        balance=float(r[0]); used=bool(r[1]); qualified=bool(r[2])
        if amount>balance: raise HTTPException(400,"Insufficient balance")
        if not qualified and used: raise HTTPException(400,"Qualifying deposit required for another withdrawal")
        c.execute("UPDATE users SET usdt=usdt-%s,withdrawal_used=TRUE WHERE id=%s",(amount,uid))
        c.execute("""INSERT INTO withdrawals(user_id,amount,address,created_at)
                     VALUES(%s,%s,%s,%s)""",(uid,amount,address,int(time.time())))
        return {"ok":True,"status":"queued","network":"BEP20"}

init_db()

async def start(update, context):
    if not WEBAPP_URL:
        await update.message.reply_text("WEBAPP_URL is not configured.")
        return
    kb=[[InlineKeyboardButton("🚀 Open Lumaqix",web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text("Welcome to Lumaqix!",reply_markup=InlineKeyboardMarkup(kb))

def run_bot():
    if not BOT_TOKEN or Application is None: return
    tg=Application.builder().token(BOT_TOKEN).build()
    tg.add_handler(CommandHandler("start",start))
    tg.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    import threading, uvicorn
    if BOT_TOKEN: threading.Thread(target=run_bot,daemon=True).start()
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","10000")))
