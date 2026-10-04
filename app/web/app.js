const tg=window.Telegram?.WebApp;if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#142348');tg.setBackgroundColor('#0b1630');}catch(e){}}
const initData=tg?.initData||"", user=tg?.initDataUnsafe?.user||{};const $=id=>document.getElementById(id);const fmt=(n,d=8)=>Number(n||0).toFixed(d);
function api(path,opt={}){const sep=path.includes('?')?'&':'?';return fetch(path+sep+'initData='+encodeURIComponent(initData),opt)}
function setBalances(d){
  const p=d.points_balance??50, dep=d.deposit_balance??0, min=d.mining_balance??0, w=d.withdrawable_balance??0;
  $('pointsBalance').textContent=p;$('tradeCoins').textContent=p;$('depositBalance').textContent=fmt(dep,2);$('miningBalance').textContent=fmt(min,8);$('withdrawBalance').textContent=fmt(w,8);$('withdrawBalance2').textContent='$'+fmt(w,4);$('tradeUsdt').textContent='$'+fmt(w,4);$('accCoins').textContent=p;$('accDeposit').textContent=fmt(dep,2);$('accMining').textContent=fmt(min,8);$('accWithdraw').textContent=fmt(w,8);$('withdrawAvailable').textContent=fmt(w,8);$('refIncome').textContent=fmt(d.referral_income,2);
  const day=Number(d.hourly_income||0);/* displayed income is derived from earned USDT, never from coins */
}
async function updateOnlineCount(){
  if(!initData) return;
  try{
    const r=await api('/api/online',{method:'POST'});
    if(r.ok){ const d=await r.json(); if(typeof d.online==='number') $('onlineCount').textContent=d.online.toLocaleString(); }
  }catch(e){ console.error(e); }
}
async function loadMe(){const name=[user.first_name,user.last_name].filter(Boolean).join(' ')||'MD SAYUM ALI';$('userName').textContent=name;$('accountName').textContent=name;$('avatar').textContent=(user.first_name||'M')[0].toUpperCase();$('accountAvatar').textContent=(user.first_name||'M')[0].toUpperCase();if(!initData){setBalances({points_balance:50,deposit_balance:0,mining_balance:0,withdrawable_balance:0,referral_income:0});return}try{const r=await api('/api/me');if(r.ok){const d=await r.json();setBalances(d);$('invitedBy').textContent=d.invited_by||'—'}}catch(e){console.error(e)}}
function showPage(name){document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));const page=$(name+'Page');if(page)page.classList.add('active');document.querySelectorAll('.bottom-nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===name));window.scrollTo({top:0,behavior:'smooth'});}
function openModal(id){$(id).classList.add('open');if(id==='withdrawModal')$('withdrawAvailable').textContent=$('withdrawBalance').textContent}function closeModals(){document.querySelectorAll('.modal').forEach(m=>m.classList.remove('open'))}
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>{const n=b.dataset.page;if(n==='topup')openModal('topupModal');else if(n==='withdraw')openModal('withdrawModal');else showPage(n)}));
document.querySelectorAll('.modal-close').forEach(b=>b.addEventListener('click',closeModals));document.querySelectorAll('.modal').forEach(m=>m.addEventListener('click',e=>{if(e.target===m)closeModals()}));
$('copyInvite').onclick=async()=>{const link=`https://t.me/lumaqixbot?startapp=${user.id||''}`;$('inviteLink').value=link;try{await navigator.clipboard.writeText(link);$('copyInvite').textContent='Copied';setTimeout(()=>$('copyInvite').textContent='Copy',1200)}catch(e){}};
$('dailyGift').onclick=async()=>{if(!initData){$('giftMsg').textContent='Open the Mini App inside Telegram to claim.';return}const r=await api('/api/daily-gift',{method:'POST'}),d=await r.json();$('giftMsg').textContent=d.message||'Done';if(d.user)setBalances(d.user)};
$('openGift').onclick=async()=>{if(!initData){$('giftMsg').textContent='Open the Mini App inside Telegram to use gifts.';return}const r=await api('/api/invite-gift/open',{method:'POST'}),d=await r.json();$('giftMsg').textContent=d.message||'No gift box';if(d.user)setBalances(d.user)};
document.querySelectorAll('.quick button').forEach(b=>b.onclick=()=>{$('topupAmount').value=b.textContent});
$('withdrawBtn').onclick=async()=>{const amount=Number($('withdrawAmount').value),address=$('withdrawAddress').value.trim();if(amount<.1){$('withdrawMsg').textContent='Minimum withdrawal is 0.1 USDT.';return}if(!/^0x[a-fA-F0-9]{40}$/.test(address)){$('withdrawMsg').textContent='Enter a valid BEP20 address.';return}if(!initData){$('withdrawMsg').textContent='Open the Mini App inside Telegram.';return}const r=await api('/api/withdraw',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({amount,address})});const d=await r.json();$('withdrawMsg').textContent=d.message||d.detail||'Request submitted';if(d.user)setBalances(d.user)};
async function btc(){const price=$('btcPrice'),change=$('btcChange');let ws;try{ws=new WebSocket('wss://stream.binance.com:9443/ws/btcusdt@ticker');ws.onmessage=e=>{const d=JSON.parse(e.data);price.textContent=Number(d.c).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});change.textContent=(Number(d.P)>=0?'+':'')+Number(d.P).toFixed(2)+'%';};ws.onerror=()=>{try{ws.close()}catch(e){};fallback()};ws.onclose=()=>{if(price.textContent==='--')fallback()}}catch(e){fallback()}async function fallback(){try{const r=await fetch('https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT');const d=await r.json();price.textContent=Number(d.lastPrice).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});change.textContent=(Number(d.priceChangePercent)>=0?'+':'')+Number(d.priceChangePercent).toFixed(2)+'%'}catch(e){price.textContent='Unavailable';change.textContent=''}}
}
$('howBtn').onclick=()=>showPage('trade');$('claimBtn').onclick=()=>showPage('trade');
loadMe();
updateOnlineCount();
setInterval(updateOnlineCount,15000);btc();showPage('home');
