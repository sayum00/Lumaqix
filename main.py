import os, hmac, hashlib, sqlite3, time, urllib.parse, json, threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler

BASE=Path(__file__).resolve().parent.parent
load_dotenv(BASE/".env")
TOKEN=os.getenv("BOT_TOKEN",""); WEB=os.getenv("WEBAPP_URL","").rstrip("/")
DB=BASE/"lumaqix.db"; app=FastAPI(title="Lumaqix")

def con():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
c=con()
c.execute("""CREATE TABLE IF NOT EXISTS users(
telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
balance INTEGER DEFAULT 0, referred_by INTEGER, created_at INTEGER, last_daily INTEGER DEFAULT 0)""")
c.execute("""CREATE TABLE IF NOT EXISTS referrals(
invited_id INTEGER PRIMARY KEY, inviter_id INTEGER, created_at INTEGER)""")
c.commit(); c.close()

def auth(init_data):
    if not TOKEN or not init_data: raise HTTPException(401,"Telegram authentication unavailable")
    d=dict(urllib.parse.parse_qsl(init_data,keep_blank_values=True)); h=d.pop("hash",None)
    if not h: raise HTTPException(401,"Missing Telegram hash")
    if time.time()-int(d.get("auth_date","0"))>86400: raise HTTPException(401,"Telegram data expired")
    check="\n".join(f"{k}={d[k]}" for k in sorted(d))
    secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,h): raise HTTPException(401,"Invalid Telegram signature")
    u=json.loads(d.get("user","{}"))
    if not u.get("id"): raise HTTPException(401,"User missing")
    return d,u

class Body(BaseModel): init_data:str

@app.post("/api/me")
def me(b:Body):
    d,u=auth(b.init_data); uid=int(u["id"]); now=int(time.time()); c=con()
    row=c.execute("SELECT * FROM users WHERE telegram_id=?",(uid,)).fetchone()
    if not row:
        inv=int(d["start_param"]) if d.get("start_param","").isdigit() and int(d["start_param"])!=uid else None
        if inv and not c.execute("SELECT telegram_id FROM users WHERE telegram_id=?",(inv,)).fetchone(): inv=None
        c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)",(uid,u.get("username",""),u.get("first_name",""),0,inv,now,0))
        if inv:
            c.execute("INSERT OR IGNORE INTO referrals VALUES(?,?,?)",(uid,inv,now))
            c.execute("UPDATE users SET balance=balance+500 WHERE telegram_id=?",(inv,))
        c.commit()
    row=c.execute("SELECT * FROM users WHERE telegram_id=?",(uid,)).fetchone()
    refs=c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=?",(uid,)).fetchone()["n"]; c.close()
    return {"id":uid,"first_name":row["first_name"],"username":row["username"],"balance":row["balance"],"referrals":refs}

@app.post("/api/daily")
def daily(b:Body):
    _,u=auth(b.init_data); uid=int(u["id"]); now=int(time.time()); c=con()
    row=c.execute("SELECT last_daily FROM users WHERE telegram_id=?",(uid,)).fetchone()
    if not row: c.close(); raise HTTPException(404,"User not registered")
    if now-row["last_daily"]<86400: c.close(); raise HTTPException(400,"Daily bonus already claimed")
    c.execute("UPDATE users SET balance=balance+100,last_daily=? WHERE telegram_id=?",(now,uid)); c.commit()
    bal=c.execute("SELECT balance FROM users WHERE telegram_id=?",(uid,)).fetchone()["balance"]; c.close()
    return {"balance":bal,"amount":100}

@app.get("/api/leaderboard")
def board():
    c=con(); rows=c.execute("SELECT first_name,username,balance FROM users ORDER BY balance DESC LIMIT 20").fetchall(); c.close()
    return [dict(x) for x in rows]

async def start(update,context):
    await update.message.reply_text("🤖 Welcome to Lumaqix!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Open Lumaqix",web_app=WebAppInfo(url=WEB))]]))

def bot():
    if not TOKEN: return
    import asyncio
    async def run():
        a=Application.builder().token(TOKEN).build(); a.add_handler(CommandHandler("start",start))
        await a.initialize(); await a.start(); await a.updater.start_polling(); await asyncio.Event().wait()
    asyncio.run(run())

app.mount("/",StaticFiles(directory=BASE/"app/web",html=True),name="web")
if __name__=="__main__":
    threading.Thread(target=bot,daemon=True).start()
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8000")))
