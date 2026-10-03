import os,time,uuid,hmac,hashlib,json,asyncio,secrets
from decimal import Decimal
from urllib.parse import parse_qsl
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine,Column,Integer,String,Numeric,BigInteger,Boolean,Text,func,or_
from sqlalchemy.orm import declarative_base,sessionmaker
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,WebAppInfo
from telegram.ext import Application,CommandHandler

BOT_TOKEN=os.getenv('BOT_TOKEN',''); BOT_USERNAME=os.getenv('BOT_USERNAME',''); WEBAPP_URL=os.getenv('WEBAPP_URL','http://localhost:8000')
ADMIN_IDS={int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit()}
DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./lumaqix.db')
if DATABASE_URL.startswith('postgres://'): DATABASE_URL=DATABASE_URL.replace('postgres://','postgresql+psycopg2://',1)
elif DATABASE_URL.startswith('postgresql://'): DATABASE_URL=DATABASE_URL.replace('postgresql://','postgresql+psycopg2://',1)
NP_KEY=os.getenv('NOWPAYMENTS_API_KEY',''); NP_SECRET=os.getenv('NOWPAYMENTS_IPN_SECRET',''); NP_JWT=os.getenv('NOWPAYMENTS_JWT','')
PAY_CURRENCY=os.getenv('NOWPAYMENTS_PAY_CURRENCY','usdtbsc'); PRICE_CURRENCY=os.getenv('NOWPAYMENTS_PRICE_CURRENCY','usd')
IPN_URL=os.getenv('NOWPAYMENTS_IPN_URL',WEBAPP_URL.rstrip('/')+'/api/nowpayments/ipn'); PAYOUT_IPN_URL=os.getenv('PAYOUT_IPN_URL',WEBAPP_URL.rstrip('/')+'/api/nowpayments/payout-ipn')
REF_PCT=Decimal(os.getenv('REFERRAL_COMMISSION_PERCENT','5')); REF_BONUS=Decimal(os.getenv('REFERRAL_BONUS_USD','0'))
REF_LEVEL_PCTS=[Decimal('7'),Decimal('3'),Decimal('1.5'),Decimal('1'),Decimal('0.5')]
INVITE_POINT_REWARDS=[50,30,20,15]
DAILY_POINTS_MIN=int(os.getenv('DAILY_REWARD_MIN_POINTS','10')); DAILY_POINTS_MAX=int(os.getenv('DAILY_REWARD_MAX_POINTS','50'))
POINTS_PER_POWER=Decimal(os.getenv('POINTS_PER_MINING_POWER','25'))
MINING_USD_PER_POWER_PER_DAY=Decimal(os.getenv('MINING_USD_PER_POWER_PER_DAY','0.01'))
MIN_DEP=Decimal(os.getenv('MIN_DEPOSIT_USD','10')); MIN_WD=Decimal(os.getenv('MIN_WITHDRAW_USD','10')); MAX_WD=Decimal(os.getenv('MAX_WITHDRAW_USD','1000')); WD_FEE=Decimal(os.getenv('WITHDRAW_FEE_USD','0'))
Base=declarative_base(); kw={'connect_args':{'check_same_thread':False}} if DATABASE_URL.startswith('sqlite') else {}
engine=create_engine(DATABASE_URL,pool_pre_ping=True,**kw); Session=sessionmaker(bind=engine,expire_on_commit=False)
class User(Base):
 __tablename__='users'; telegram_id=Column(BigInteger,primary_key=True); username=Column(String(255),default=''); first_name=Column(String(255),default=''); balance=Column(Numeric(18,8),default=0); invited_by=Column(BigInteger); last_daily=Column(BigInteger,default=0); points=Column(Integer,default=0); mining_power=Column(Numeric(18,8),default=0); mining_last_update=Column(BigInteger,default=lambda:int(time.time())); mining_claimable=Column(Numeric(18,8),default=0); mining_total_earned=Column(Numeric(18,8),default=0); blocked=Column(Boolean,default=False); created_at=Column(BigInteger,default=lambda:int(time.time()))
class Tx(Base):
 __tablename__='transactions'; id=Column(String(64),primary_key=True); telegram_id=Column(BigInteger,index=True); type=Column(String(64),index=True); amount=Column(Numeric(18,8)); currency=Column(String(20),default='USD'); status=Column(String(32),index=True); external_id=Column(String(255),unique=True); meta_json=Column(Text); created_at=Column(BigInteger,default=lambda:int(time.time())); updated_at=Column(BigInteger,default=lambda:int(time.time()))
class Withdrawal(Base):
 __tablename__='withdrawals'; id=Column(String(64),primary_key=True); telegram_id=Column(BigInteger,index=True); address=Column(String(255)); amount=Column(Numeric(18,8)); fee=Column(Numeric(18,8),default=0); status=Column(String(32),index=True); external_id=Column(String(255),unique=True); txid=Column(String(255)); created_at=Column(BigInteger,default=lambda:int(time.time())); updated_at=Column(BigInteger,default=lambda:int(time.time()))
def ensure_user_columns():
    # Add mining columns to existing databases without requiring a database reset.
    from sqlalchemy import inspect
    cols={c['name'] for c in inspect(engine).get_columns('users')}
    with engine.begin() as con:
        if 'points' not in cols: con.exec_driver_sql('ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0')
        if 'mining_power' not in cols: con.exec_driver_sql('ALTER TABLE users ADD COLUMN mining_power NUMERIC(18,8) DEFAULT 0')
        if 'mining_last_update' not in cols: con.exec_driver_sql('ALTER TABLE users ADD COLUMN mining_last_update BIGINT DEFAULT 0')
        if 'mining_claimable' not in cols: con.exec_driver_sql('ALTER TABLE users ADD COLUMN mining_claimable NUMERIC(18,8) DEFAULT 0')
        if 'mining_total_earned' not in cols: con.exec_driver_sql('ALTER TABLE users ADD COLUMN mining_total_earned NUMERIC(18,8) DEFAULT 0')

ensure_user_columns()
Base.metadata.create_all(engine); app=FastAPI(title='Lumaqix')
def q(x): return Decimal(str(x)).quantize(Decimal('0.00000001'))
def f(x): return float(q(x))

def apply_mining(db,row,now=None):
    now=int(now or time.time())
    points=int(row.points or 0)
    power=(Decimal(points)/POINTS_PER_POWER) if POINTS_PER_POWER>0 else Decimal('0')
    last=int(row.mining_last_update or 0)
    if last<=0:
        row.mining_last_update=now
        row.mining_power=q(power)
        return Decimal('0')
    elapsed=max(0,now-last)
    earned=q(power*MINING_USD_PER_POWER_PER_DAY*(Decimal(elapsed)/Decimal('86400')))
    if earned>0:
        row.mining_claimable=q(Decimal(row.mining_claimable or 0)+earned)
        row.mining_total_earned=q(Decimal(row.mining_total_earned or 0)+earned)
    row.mining_power=q(power)
    row.mining_last_update=now
    return earned
def auth(r):
 s=r.headers.get('X-Telegram-Init-Data','')
 if not s or not BOT_TOKEN:return None
 try:
  d=dict(parse_qsl(s,keep_blank_values=True)); got=d.pop('hash',None); check='\n'.join(f'{k}={v}' for k,v in sorted(d.items())); sec=hmac.new(b'WebAppData',BOT_TOKEN.encode(),hashlib.sha256).digest(); exp=hmac.new(sec,check.encode(),hashlib.sha256).hexdigest()
  return json.loads(d.get('user','{}')) if got and hmac.compare_digest(exp,got) else None
 except:return None
def user_get(u,start=None):
 db=Session(); uid=int(u['id']); row=db.get(User,uid)
 if row:
  row.username=u.get('username',''); row.first_name=u.get('first_name',''); db.commit(); db.close(); return row
 ref=None
 try:
  x=int(start or 0)
  if x and x!=uid and db.get(User,x): ref=x
 except: pass
 row=User(telegram_id=uid,username=u.get('username',''),first_name=u.get('first_name',''),balance=0,invited_by=ref,last_daily=0,points=50,mining_power=q(Decimal('50')/POINTS_PER_POWER),mining_last_update=int(time.time()),blocked=False)
 db.add(row); db.flush()
 # Direct invite reward: 1st +50, 2nd +30, 3rd +20, 4th+ +15 Points.
 if ref:
  ru=db.get(User,ref)
  if ru:
   prior=db.query(User).filter(User.invited_by==ref).count()-1
   idx=max(0,prior)
   bonus=INVITE_POINT_REWARDS[idx] if idx < len(INVITE_POINT_REWARDS) else INVITE_POINT_REWARDS[-1]
   apply_mining(db,ru)
   ru.points=int(ru.points or 0)+bonus
   ru.mining_power=q(Decimal(ru.points)/POINTS_PER_POWER)
   db.add(Tx(
    id=str(uuid.uuid4()), telegram_id=ru.telegram_id, type='referral_invite_points',
    amount=q(bonus), currency='POINTS', status='completed',
    meta_json=json.dumps({'referred_user':uid,'invite_number':idx+1,'bonus_points':bonus})
   ))
   db.add(Tx(
    id=str(uuid.uuid4()), telegram_id=ru.telegram_id, type='referral_box',
    amount=q(0), currency='POINTS', status='available',
    meta_json=json.dumps({'referred_user':uid,'invite_number':idx+1})
   ))
  if REF_BONUS>0:
   ru=db.get(User,ref)
   if ru:
    ru.balance=q(Decimal(ru.balance)+REF_BONUS)
    db.add(Tx(id=str(uuid.uuid4()),telegram_id=ref,type='referral_bonus',amount=q(REF_BONUS),currency='USD',status='completed',meta_json=json.dumps({'referred_user':uid})))
 db.commit(); db.close(); return row
def verify(payload,sig):
 if not NP_SECRET or not sig:return False
 def canon(x): return {k:canon(x[k]) for k in sorted(x)} if isinstance(x,dict) else [canon(v) for v in x] if isinstance(x,list) else x
 raw=json.dumps(canon(payload),separators=(',',':'),ensure_ascii=False); exp=hmac.new(NP_SECRET.encode(),raw.encode(),hashlib.sha512).hexdigest(); return hmac.compare_digest(exp,sig)
async def create_payment(amount,order):
 async with httpx.AsyncClient(timeout=30) as c:
  r=await c.post('https://api.nowpayments.io/v1/payment',headers={'x-api-key':NP_KEY,'Content-Type':'application/json'},json={'price_amount':f(amount),'price_currency':PRICE_CURRENCY,'pay_currency':PAY_CURRENCY,'ipn_callback_url':IPN_URL,'order_id':order,'order_description':'Lumaqix deposit'}); r.raise_for_status(); return r.json()
async def create_payout(address,amount,wid):
 async with httpx.AsyncClient(timeout=30) as c:
  r=await c.post('https://api.nowpayments.io/v1/payout',headers={'x-api-key':NP_KEY,'Authorization':f'Bearer {NP_JWT}','Content-Type':'application/json'},json={'ipn_callback_url':PAYOUT_IPN_URL,'withdrawals':[{'address':address,'currency':PAY_CURRENCY,'amount':f(amount)}]}); r.raise_for_status(); return r.json()
async def start_cmd(update,context):
 if not update.effective_user:return
 row=user_get(update.effective_user.to_dict(),context.args[0] if context.args else None)
 if row.blocked:return await update.message.reply_text('Your account is blocked.')
 kb=[[InlineKeyboardButton('🚀 Open Lumaqix',web_app=WebAppInfo(url=WEBAPP_URL))]]; await update.message.reply_text('🌟 Welcome to Lumaqix!\n\nOpen the Mini App to use deposit, withdrawal, referral, rewards and statistics.',reply_markup=InlineKeyboardMarkup(kb))
async def admin_cmd(update,context):
 if not update.effective_user or update.effective_user.id not in ADMIN_IDS:return
 db=Session(); n=db.query(User).count(); bal=db.query(func.coalesce(func.sum(User.balance),0)).scalar() or 0; p=db.query(Withdrawal).filter(Withdrawal.status=='pending').count(); db.close(); await update.message.reply_text(f'🛠 Admin\nUsers: {n}\nBalance: ${f(bal):.2f}\nPending withdrawals: {p}')
def run_bot():
 if not BOT_TOKEN:return
 async def go():
  a=Application.builder().token(BOT_TOKEN).build(); a.add_handler(CommandHandler('start',start_cmd)); a.add_handler(CommandHandler('admin',admin_cmd)); await a.initialize(); await a.start(); await a.updater.start_polling(); print('Telegram bot running')
  while True: await asyncio.sleep(3600)
 asyncio.run(go())
@app.get('/')
async def root():return FileResponse('web/index.html')
@app.get('/health')
async def health():return {'ok':True,'service':'Lumaqix'}
@app.get('/api/market/btc')
async def market_btc():
    """Live BTC/USDT market snapshot from Binance public market data."""
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            t = await c.get('https://api.binance.com/api/v3/ticker/24hr', params={'symbol':'BTCUSDT'})
            t.raise_for_status(); d=t.json()
            k = await c.get('https://api.binance.com/api/v3/klines', params={'symbol':'BTCUSDT','interval':'1m','limit':60})
            k.raise_for_status(); kl=k.json()
        candles=[{'time':int(x[0]//1000),'open':float(x[1]),'high':float(x[2]),'low':float(x[3]),'close':float(x[4]),'volume':float(x[5])} for x in kl]
        return {'ok':True,'symbol':'BTCUSDT','price':float(d['lastPrice']),'change':float(d['priceChange']),'change_percent':float(d['priceChangePercent']),'high':float(d['highPrice']),'low':float(d['lowPrice']),'volume':float(d['volume']),'quote_volume':float(d['quoteVolume']),'bid':float(d['bidPrice']),'ask':float(d['askPrice']),'updated_at':int(time.time()*1000),'candles':candles}
    except Exception:
        return JSONResponse({'ok':False,'error':'Live BTC market temporarily unavailable'},502)

@app.get('/api/me')
async def me(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Invalid Telegram init data'},401)
 p=dict(parse_qsl(r.headers.get('X-Telegram-Init-Data',''),keep_blank_values=True)); row=user_get(u,p.get('start_param'))
 if row.blocked:return JSONResponse({'ok':False,'error':'Account blocked'},403)
 db=Session(); row=db.get(User,int(row.telegram_id)); earned=apply_mining(db,row); db.commit()
 out={'id':row.telegram_id,'username':row.username,'first_name':row.first_name,'balance':f(row.balance),'points':int(row.points or 0),'mining_power':f(row.mining_power or 0),'mining_usd_per_day':f(Decimal(row.mining_power or 0)*MINING_USD_PER_POWER_PER_DAY),'mining_earned_now':f(earned),'mining_claimable':f(row.mining_claimable or 0),'mining_total_earned':f(row.mining_total_earned or 0),'mining_hourly':f(Decimal(row.mining_power or 0)*MINING_USD_PER_POWER_PER_DAY/Decimal('24')),'mining_monthly':f(Decimal(row.mining_power or 0)*MINING_USD_PER_POWER_PER_DAY*Decimal('30'))}
 db.close(); return {'ok':True,'user':out}
@app.post('/api/daily')
async def daily(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 db=Session(); row=db.get(User,int(u['id'])); now=int(time.time())
 if not row or row.blocked:db.close();return JSONResponse({'ok':False,'error':'Account unavailable'},403)
 if now-int(row.last_daily or 0)<86400: rem=86400-(now-int(row.last_daily or 0)); bal=f(row.balance); db.close(); return {'ok':False,'claimed':False,'remaining':rem,'balance':bal}
 if DAILY_POINTS_MAX<DAILY_POINTS_MIN:db.close();return JSONResponse({'ok':False,'error':'Invalid daily reward range'},500)
 reward_points=secrets.randbelow(DAILY_POINTS_MAX-DAILY_POINTS_MIN+1)+DAILY_POINTS_MIN
 apply_mining(db,row,now)
 row.points=int(row.points or 0)+reward_points
 row.mining_power=q(Decimal(row.points)/POINTS_PER_POWER)
 row.last_daily=now
 db.add(Tx(id=str(uuid.uuid4()),telegram_id=row.telegram_id,type='daily_points',amount=q(reward_points),currency='POINTS',status='completed'))
 db.commit(); bal=f(row.balance); pts=int(row.points or 0); power=f(row.mining_power or 0); claimable=f(row.mining_claimable or 0); db.close(); return {'ok':True,'claimed':True,'reward_points':reward_points,'points':pts,'mining_power':power,'balance':bal,'mining_claimable':claimable}
@app.post('/api/claim-profit')
async def claim_profit(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 db=Session(); row=db.get(User,int(u['id']))
 if not row or row.blocked: db.close(); return JSONResponse({'ok':False,'error':'Account unavailable'},403)
 apply_mining(db,row)
 amount=q(row.mining_claimable or 0)
 if amount<=0:
  db.commit(); db.close(); return {'ok':False,'error':'No mining profit is ready to claim','claimable':0}
 row.balance=q(Decimal(row.balance or 0)+amount)
 row.mining_claimable=q(0)
 db.add(Tx(id=str(uuid.uuid4()),telegram_id=row.telegram_id,type='mining_claim',amount=amount,currency='USD',status='completed'))
 db.commit(); out={'ok':True,'claimed':f(amount),'balance':f(row.balance),'claimable':f(row.mining_claimable)}; db.close(); return out
@app.get('/api/referral')
async def referral(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 db=Session(); uid=int(u['id'])
 count=db.query(User).filter(User.invited_by==uid).count()

 # Backfill one box for older direct referrals.
 box_rows=db.query(Tx).filter(Tx.telegram_id==uid,Tx.type=='referral_box').order_by(Tx.created_at.asc()).all()
 if len(box_rows)<count:
  referred=db.query(User).filter(User.invited_by==uid).order_by(User.created_at.asc()).all()
  existing_refs=set()
  for bx in box_rows:
   try: existing_refs.add(str(json.loads(bx.meta_json or '{}').get('referred_user')))
   except: pass
  for ru in referred:
   if len(box_rows)>=count: break
   if str(ru.telegram_id) in existing_refs: continue
   db.add(Tx(id=str(uuid.uuid4()),telegram_id=uid,type='referral_box',amount=q(0),currency='POINTS',status='available',
             meta_json=json.dumps({'referred_user':ru.telegram_id,'invite_number':len(box_rows)+1})))
   box_rows.append(True)

 earn=db.query(func.coalesce(func.sum(Tx.amount),0)).filter(Tx.telegram_id==uid,Tx.type=='referral_commission',Tx.status=='completed').scalar() or 0
 direct_points=db.query(func.coalesce(func.sum(Tx.amount),0)).filter(Tx.telegram_id==uid,Tx.type=='referral_invite_points',Tx.status=='completed').scalar() or 0
 boxes=db.query(Tx).filter(Tx.telegram_id==uid,Tx.type=='referral_box',Tx.status=='available').count()
 claimed_boxes=db.query(Tx).filter(Tx.telegram_id==uid,Tx.type=='referral_box',Tx.status=='completed').count()
 levels=[{'level':i+1,'percent':f(p)} for i,p in enumerate(REF_LEVEL_PCTS)]
 db.commit(); db.close()
 return {'ok':True,'referral_link':f'https://t.me/{BOT_USERNAME}?start={uid}','count':count,
         'earnings':f(earn),'invite_points':f(direct_points),'commission_percent':f(REF_LEVEL_PCTS[0]),
         'level_commissions':levels,'invite_rewards':[{'invite':i+1,'points':v} for i,v in enumerate(INVITE_POINT_REWARDS)],
         'boxes_available':boxes,'boxes_claimed':claimed_boxes}

@app.post('/api/referral-box/claim')
async def claim_referral_box(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 db=Session(); uid=int(u['id'])
 box=db.query(Tx).filter(Tx.telegram_id==uid,Tx.type=='referral_box',Tx.status=='available').order_by(Tx.created_at.asc()).first()
 if not box:
  db.close(); return {'ok':False,'error':'No Power Box is available'}
 row=db.get(User,uid)
 if not row or row.blocked:
  db.close(); return JSONResponse({'ok':False,'error':'Account unavailable'},403)
 reward=secrets.randbelow(3)+1
 apply_mining(db,row)
 row.points=int(row.points or 0)+reward
 row.mining_power=q(Decimal(row.points)/POINTS_PER_POWER)
 box.status='completed'; box.amount=q(reward); box.updated_at=int(time.time())
 box.meta_json=json.dumps({'reward_points':reward,'opened_at':int(time.time()),'source':json.loads(box.meta_json or '{}')})
 db.add(Tx(id=str(uuid.uuid4()),telegram_id=uid,type='referral_box_reward',amount=q(reward),currency='POINTS',
           status='completed',meta_json=json.dumps({'box_id':box.id,'reward_points':reward})))
 db.commit()
 remaining=db.query(Tx).filter(Tx.telegram_id==uid,Tx.type=='referral_box',Tx.status=='available').count()
 out={'ok':True,'reward_points':reward,'points':int(row.points or 0),'mining_power':f(row.mining_power),'boxes_available':remaining}
 db.close(); return out

@app.get('/api/stats')
async def stats(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 db=Session(); uid=int(u['id']); dep=db.query(func.coalesce(func.sum(Tx.amount),0)).filter(Tx.telegram_id==uid,Tx.type=='deposit',Tx.status=='completed').scalar() or 0; refs=db.query(User).filter(User.invited_by==uid).count(); rew=db.query(func.coalesce(func.sum(Tx.amount),0)).filter(Tx.telegram_id==uid,Tx.type.in_(['daily','daily_points','referral_bonus','referral_commission']),Tx.status=='completed').scalar() or 0; wd=db.query(func.coalesce(func.sum(Withdrawal.amount),0)).filter(Withdrawal.telegram_id==uid,Withdrawal.status=='completed').scalar() or 0; db.close(); return {'ok':True,'stats':{'deposits':f(dep),'referrals':refs,'rewards':f(rew),'withdrawn':f(wd)}}
@app.get('/api/transactions')
async def txs(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 db=Session(); rows=db.query(Tx).filter(Tx.telegram_id==int(u['id'])).order_by(Tx.created_at.desc()).limit(100).all(); out=[{'id':x.id,'type':x.type,'amount':f(x.amount),'currency':x.currency,'status':x.status,'created_at':x.created_at} for x in rows]; db.close(); return {'ok':True,'transactions':out}
@app.post('/api/deposit')
async def deposit(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 try:a=Decimal(str((await r.json()).get('amount','0')))
 except:return JSONResponse({'ok':False,'error':'Invalid amount'},400)
 if a<MIN_DEP:return JSONResponse({'ok':False,'error':f'Minimum deposit is ${MIN_DEP}'},400)
 uid=int(u['id']); order=f'DEP-{uid}-{uuid.uuid4().hex[:16]}'; db=Session(); tid=str(uuid.uuid4()); db.add(Tx(id=tid,telegram_id=uid,type='deposit',amount=q(a),currency='USD',status='pending',external_id=order)); db.commit()
 try:p=await create_payment(a,order)
 except Exception as e: t=db.get(Tx,tid); t.status='failed'; t.meta_json=json.dumps({'error':str(e)}); db.commit(); db.close(); return JSONResponse({'ok':False,'error':'Payment provider error'},502)
 t=db.get(Tx,tid); t.external_id=str(p.get('payment_id') or order); t.meta_json=json.dumps(p); t.updated_at=int(time.time()); db.commit(); db.close(); return {'ok':True,'payment':{'payment_id':p.get('payment_id'),'pay_address':p.get('pay_address'),'pay_amount':p.get('pay_amount'),'pay_currency':p.get('pay_currency'),'payment_status':p.get('payment_status'),'order_id':order,'invoice_url':p.get('invoice_url')}}
@app.post('/api/nowpayments/ipn')
async def ipn(r:Request):
 try:p=await r.json()
 except:return JSONResponse({'ok':False,'error':'Invalid JSON'},400)
 if not verify(p,r.headers.get('x-nowpayments-sig','')):return JSONResponse({'ok':False,'error':'Invalid signature'},401)
 pid=str(p.get('payment_id') or ''); order=str(p.get('order_id') or ''); status=str(p.get('payment_status') or '').lower(); db=Session(); t=db.query(Tx).filter(or_(Tx.external_id==pid,Tx.external_id==order)).first()
 if not t:db.close();return {'ok':True,'ignored':True}
 if status=='finished' and t.status!='completed':
  t.status='completed';t.meta_json=json.dumps(p);t.updated_at=int(time.time());u=db.get(User,t.telegram_id);u.balance=q(Decimal(u.balance)+Decimal(t.amount));
  # Five-level deposit commission: L1 7%, L2 3%, L3 1.5%, L4 1%, L5 0.5%.
  ancestor=u.invited_by
  for level,pct in enumerate(REF_LEVEL_PCTS, start=1):
   if not ancestor: break
   ru=db.get(User,ancestor)
   if not ru: break
   c=q(Decimal(t.amount)*pct/100)
   if c>0:
    ru.balance=q(Decimal(ru.balance)+c)
    db.add(Tx(
     id=str(uuid.uuid4()), telegram_id=ru.telegram_id, type='referral_commission',
     amount=c, currency='USD', status='completed',
     external_id=f'REF-{pid}-L{level}-{ru.telegram_id}',
     meta_json=json.dumps({'source_deposit':pid,'level':level,'percent':f(pct),'depositor':u.telegram_id})
    ))
   ancestor=ru.invited_by
 elif t.status not in ('completed','failed','expired','refunded'):t.status=status or 'unknown';t.meta_json=json.dumps(p);t.updated_at=int(time.time())
 db.commit();db.close();return {'ok':True}
def bep20(a):return isinstance(a,str) and len(a)==42 and a.startswith('0x') and all(c in '0123456789abcdefABCDEF' for c in a[2:])
@app.post('/api/withdraw')
async def withdraw(r:Request):
 u=auth(r)
 if not u:return JSONResponse({'ok':False,'error':'Unauthorized'},401)
 try:b=await r.json();a=Decimal(str(b.get('amount','0')));addr=str(b.get('address','')).strip()
 except:return JSONResponse({'ok':False,'error':'Invalid request'},400)
 if not bep20(addr):return JSONResponse({'ok':False,'error':'Invalid BEP-20 address'},400)
 if a<MIN_WD or a>MAX_WD:return JSONResponse({'ok':False,'error':f'Withdrawal must be between ${MIN_WD} and ${MAX_WD}'},400)
 uid=int(u['id']);total=q(a+WD_FEE);db=Session();row=db.get(User,uid)
 if not row or row.blocked or Decimal(row.balance)<total:db.close();return JSONResponse({'ok':False,'error':'Insufficient balance or account unavailable'},400)
 wid=str(uuid.uuid4());row.balance=q(Decimal(row.balance)-total);db.add(Withdrawal(id=wid,telegram_id=uid,address=addr,amount=q(a),fee=q(WD_FEE),status='pending'));db.add(Tx(id=str(uuid.uuid4()),telegram_id=uid,type='withdrawal',amount=q(-total),currency='USD',status='pending',external_id=wid,meta_json=json.dumps({'address':addr})));db.commit()
 if NP_KEY and NP_JWT:
  try:
   p=await create_payout(addr,a,wid);ext=str(p.get('id') or p.get('payout_id') or wid);w=db.get(Withdrawal,wid);w.status='processing';w.external_id=ext;w.txid=p.get('txid');tx=db.query(Tx).filter(Tx.external_id==wid).first();tx.status='processing';tx.external_id=ext;db.commit();db.close();return {'ok':True,'withdrawal_id':wid,'status':'processing','payout':p}
  except Exception as e:
   db.rollback();row=db.get(User,uid);row.balance=q(Decimal(row.balance)+total);w=db.get(Withdrawal,wid);w.status='failed';tx=db.query(Tx).filter(Tx.external_id==wid).first();tx.status='failed';tx.meta_json=json.dumps({'error':str(e)});db.commit();db.close();return JSONResponse({'ok':False,'error':'Automatic payout failed; balance returned'},502)
 db.commit();db.close();return {'ok':True,'withdrawal_id':wid,'status':'pending'}
@app.post('/api/nowpayments/payout-ipn')
async def payout_ipn(r:Request):
 try:p=await r.json()
 except:return JSONResponse({'ok':False,'error':'Invalid JSON'},400)
 if not verify(p,r.headers.get('x-nowpayments-sig','')):return JSONResponse({'ok':False,'error':'Invalid signature'},401)
 ext=str(p.get('id') or p.get('payout_id') or '');s=str(p.get('status') or p.get('payout_status') or '').lower();db=Session();w=db.query(Withdrawal).filter(Withdrawal.external_id==ext).first()
 if not w:db.close();return {'ok':True,'ignored':True}
 final='completed' if s in ('finished','completed','success') else 'failed' if s in ('failed','rejected','refunded') else 'processing';w.status=final;w.txid=p.get('txid');tx=db.query(Tx).filter(Tx.external_id==ext).first();
 if tx:tx.status=final;tx.meta_json=json.dumps(p)
 if final=='failed':u=db.get(User,w.telegram_id);u.balance=q(Decimal(u.balance)+Decimal(w.amount)+Decimal(w.fee))
 db.commit();db.close();return {'ok':True}
def admin(r):
 u=auth(r);return u and int(u['id']) in ADMIN_IDS
@app.get('/api/admin/overview')
async def overview(r:Request):
 if not admin(r):return JSONResponse({'ok':False,'error':'Admin only'},403)
 db=Session();d={'users':db.query(User).count(),'blocked':db.query(User).filter(User.blocked==True).count(),'balance_total':f(db.query(func.coalesce(func.sum(User.balance),0)).scalar() or 0),'deposits':db.query(Tx).filter(Tx.type=='deposit',Tx.status=='completed').count(),'withdrawals_pending':db.query(Withdrawal).filter(Withdrawal.status=='pending').count()};db.close();return {'ok':True,'overview':d}
@app.get('/api/admin/users')
async def admin_users(r:Request):
 if not admin(r):return JSONResponse({'ok':False,'error':'Admin only'},403)
 db=Session();rows=db.query(User).order_by(User.created_at.desc()).limit(200).all();out=[{'id':x.telegram_id,'username':x.username,'name':x.first_name,'balance':f(x.balance),'blocked':x.blocked} for x in rows];db.close();return {'ok':True,'users':out}
@app.post('/api/admin/user/{uid}/block')
async def block(uid:int,r:Request):
 if not admin(r):return JSONResponse({'ok':False,'error':'Admin only'},403)
 b=await r.json();db=Session();u=db.get(User,uid)
 if not u:db.close();return JSONResponse({'ok':False,'error':'User not found'},404)
 u.blocked=bool(b.get('blocked',True));db.commit();x=u.blocked;db.close();return {'ok':True,'blocked':x}
@app.post('/api/admin/user/{uid}/balance')
async def adjust(uid:int,r:Request):
 if not admin(r):return JSONResponse({'ok':False,'error':'Admin only'},403)
 try:a=Decimal(str((await r.json()).get('amount','0')))
 except:return JSONResponse({'ok':False,'error':'Invalid amount'},400)
 db=Session();u=db.get(User,uid)
 if not u:db.close();return JSONResponse({'ok':False,'error':'User not found'},404)
 u.balance=q(Decimal(u.balance)+a);db.add(Tx(id=str(uuid.uuid4()),telegram_id=uid,type='admin_adjustment',amount=q(a),currency='USD',status='completed'));db.commit();x=f(u.balance);db.close();return {'ok':True,'balance':x}
@app.get('/api/admin/withdrawals')
async def admin_wd(r:Request):
 if not admin(r):return JSONResponse({'ok':False,'error':'Admin only'},403)
 db=Session();rows=db.query(Withdrawal).order_by(Withdrawal.created_at.desc()).limit(200).all();out=[{'id':x.id,'user_id':x.telegram_id,'address':x.address,'amount':f(x.amount),'status':x.status,'txid':x.txid} for x in rows];db.close();return {'ok':True,'withdrawals':out}
@app.post('/api/admin/withdrawal/{wid}/reject')
async def reject(wid:str,r:Request):
 if not admin(r):return JSONResponse({'ok':False,'error':'Admin only'},403)
 db=Session();w=db.get(Withdrawal,wid)
 if not w or w.status!='pending':db.close();return JSONResponse({'ok':False,'error':'Not pending'},400)
 u=db.get(User,w.telegram_id);u.balance=q(Decimal(u.balance)+Decimal(w.amount)+Decimal(w.fee));w.status='rejected';tx=db.query(Tx).filter(Tx.external_id==wid).first();
 if tx:tx.status='rejected'
 db.commit();db.close();return {'ok':True}
@app.post('/api/admin/broadcast')
async def broadcast(r:Request):
 if not admin(r):return JSONResponse({'ok':False,'error':'Admin only'},403)
 msg=str((await r.json()).get('message','')).strip();db=Session();ids=[x.telegram_id for x in db.query(User).filter(User.blocked==False).all()];db.close();sent=failed=0
 if BOT_TOKEN:
  async with httpx.AsyncClient(timeout=20) as c:
   for uid in ids:
    try:
     z=await c.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',json={'chat_id':uid,'text':msg});sent+=z.is_success;failed+=not z.is_success
    except:failed+=1
 return {'ok':True,'sent':sent,'failed':failed}
if os.path.isdir('web'):app.mount('/static',StaticFiles(directory='web'),name='static')
if __name__=='__main__':
 import threading,uvicorn;threading.Thread(target=run_bot,daemon=True).start();uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8000')))
