import os, time, hmac, hashlib, json
from pathlib import Path
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import psycopg

BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
WEBAPP_URL=os.getenv("WEBAPP_URL","").strip()
DATABASE_URL=os.getenv("DATABASE_URL","").strip()
PORT=int(os.getenv("PORT","10000"))
BASE_DIR=Path(__file__).resolve().parent
WEB_DIR=BASE_DIR/"web"
if not DATABASE_URL: raise RuntimeError("DATABASE_URL is not configured")
app=FastAPI(title="Lumaqix")
app.mount("/web",StaticFiles(directory=str(WEB_DIR)),name="web")

def conn():
    return psycopg.connect(DATABASE_URL)

def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
          id BIGINT PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '',
          points_balance INTEGER NOT NULL DEFAULT 50,
          deposit_balance NUMERIC(30,12) NOT NULL DEFAULT 0,
          withdrawable_balance NUMERIC(30,12) NOT NULL DEFAULT 0,
          mining_balance NUMERIC(30,12) NOT NULL DEFAULT 0,
          referral_income NUMERIC(30,12) NOT NULL DEFAULT 0,
          invited_by BIGINT, first_login_bonus BOOLEAN NOT NULL DEFAULT TRUE,
          daily_gift_at BIGINT NOT NULL DEFAULT 0, invite_gift_boxes INTEGER NOT NULL DEFAULT 0,
          created_at BIGINT NOT NULL DEFAULT 0
        )""")
        # Safe additions for an existing users table created by an earlier version.
        cols=[
          ("points_balance","INTEGER NOT NULL DEFAULT 0"),
          ("deposit_balance","NUMERIC(30,12) NOT NULL DEFAULT 0"),
          ("withdrawable_balance","NUMERIC(30,12) NOT NULL DEFAULT 0"),
          ("mining_balance","NUMERIC(30,12) NOT NULL DEFAULT 0"),
          ("referral_income","NUMERIC(30,12) NOT NULL DEFAULT 0"),
          ("first_login_bonus","BOOLEAN NOT NULL DEFAULT FALSE"),
          ("daily_gift_at","BIGINT NOT NULL DEFAULT 0"),
          ("invite_gift_boxes","INTEGER NOT NULL DEFAULT 0"),
          ("created_at","BIGINT NOT NULL DEFAULT 0")]
        for name,typ in cols:
            try:c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {name} {typ}")
            except Exception: c.rollback()
        c.commit()

def validate(data):
    if not BOT_TOKEN or not data:return None
    try:
        p=dict(parse_qsl(data,keep_blank_values=True)); rh=p.pop("hash",None)
        if not rh:return None
        check="\n".join(f"{k}={p[k]}" for k in sorted(p))
        secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
        calc=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc,rh):return None
        return json.loads(p.get("user","{}")),p.get("start_param","")
    except Exception:return None

def ensure_user(user,start=""):
    uid=int(user["id"])
    with conn() as c:
        row=c.execute("SELECT * FROM users WHERE id=%s",(uid,)).fetchone()
        if not row:
            inviter=None
            try: inviter=int(start) if start else None
            except: inviter=None
            c.execute("""INSERT INTO users
              (id,username,first_name,points_balance,first_login_bonus,invited_by,created_at)
              VALUES(%s,%s,%s,50,TRUE,%s,%s)""",
              (uid,user.get("username",""),user.get("first_name",""),inviter,int(time.time())))
            if inviter and inviter!=uid:
                c.execute("""UPDATE users SET points_balance=points_balance+10,
                  invite_gift_boxes=invite_gift_boxes+1 WHERE id=%s""",(inviter,))
            c.commit()
        return user_row(c,uid)

def user_row(c,uid):
    r=c.execute("""SELECT id,username,first_name,points_balance,deposit_balance,
      withdrawable_balance,mining_balance,referral_income,invited_by,invite_gift_boxes
      FROM users WHERE id=%s""",(uid,)).fetchone()
    if not r: return None
    keys=["id","username","first_name","points_balance","deposit_balance","withdrawable_balance","mining_balance","referral_income","invited_by","invite_gift_boxes"]
    d=dict(zip(keys,r))
    for k in ["deposit_balance","withdrawable_balance","mining_balance","referral_income"]: d[k]=float(d[k] or 0)
    d["hourly_income"]=d["mining_balance"]/24/30 if d["mining_balance"] else 0
    return d

def auth(initData):
    result=validate(initData)
    if not result: raise HTTPException(401,"Invalid Telegram initData")
    user,start=result
    return ensure_user(user,start)

@app.get("/")
def home(): return FileResponse(WEB_DIR/"index.html")
@app.get("/health")
def health(): return {"status":"ok","service":"Lumaqix"}

@app.get("/api/me")
def me(initData:str=""): return auth(initData)

@app.post("/api/daily-gift")
def daily_gift(initData:str=""):
    u=auth(initData); uid=u["id"]; now=int(time.time())
    with conn() as c:
        row=c.execute("SELECT daily_gift_at FROM users WHERE id=%s",(uid,)).fetchone()
        if now-int(row[0] or 0)<86400:return {"ok":False,"message":"Daily gift is available once every 24 hours.","user":u}
        # Daily gift is coins only; it never becomes withdrawable USDT and is not mined.
        c.execute("UPDATE users SET points_balance=points_balance+10,daily_gift_at=%s WHERE id=%s",(now,uid))
        c.commit()
        return {"ok":True,"message":"+10 coins added. Coins are separate from withdrawable USDT.","user":user_row(c,uid)}

@app.post("/api/invite-gift/open")
def invite_gift(initData:str=""):
    u=auth(initData); uid=u["id"]
    with conn() as c:
        row=c.execute("SELECT invite_gift_boxes FROM users WHERE id=%s",(uid,)).fetchone()
        if not row or int(row[0])<1:return {"ok":False,"message":"No Invite Gift Box available.","user":u}
        c.execute("UPDATE users SET invite_gift_boxes=invite_gift_boxes-1,points_balance=points_balance+1 WHERE id=%s",(uid,))
        c.commit()
        return {"ok":True,"message":"+1 coin from Invite Gift Box. It is not USDT balance.","user":user_row(c,uid)}

class WithdrawRequest(BaseModel):
    amount:float
    address:str

@app.post("/api/withdraw")
def withdraw(req:WithdrawRequest,initData:str=""):
    u=auth(initData)
    if req.amount<0.1: raise HTTPException(400,"Minimum withdrawal is 0.1 USDT")
    if not req.address.startswith("0x") or len(req.address)!=42: raise HTTPException(400,"Invalid BEP20 address")
    with conn() as c:
        row=c.execute("SELECT withdrawable_balance FROM users WHERE id=%s FOR UPDATE",(u["id"],)).fetchone()
        bal=float(row[0] or 0)
        if req.amount>bal: raise HTTPException(400,"Insufficient withdrawable USDT")
        c.execute("UPDATE users SET withdrawable_balance=withdrawable_balance-%s WHERE id=%s",(req.amount,u["id"]))
        c.commit()
        return {"ok":True,"message":"Withdrawal queued. Blockchain broadcast worker is required for real on-chain payout.","user":user_row(c,u["id"])}

init_db()

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=PORT)
