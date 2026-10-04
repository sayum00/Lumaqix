const tg=window.Telegram?.WebApp; if(tg){tg.ready();tg.expand();}
const initData=tg?.initData||"";
const user=tg?.initDataUnsafe?.user||{};
const $=id=>document.getElementById(id);
const fmt=(n,d=8)=>Number(n||0).toFixed(d);
function api(path,opt={}){const sep=path.includes("?")?"&":"?";return fetch(path+sep+"initData="+encodeURIComponent(initData),opt);}
function setBalances(d){
  $("pointsBalance").textContent=d.points_balance??50;
  $("tradeCoins").textContent=d.points_balance??50;
  $("depositBalance").textContent=fmt(d.deposit_balance,2);
  $("miningBalance").textContent=fmt(d.mining_balance,8);
  $("withdrawBalance").textContent=fmt(d.withdrawable_balance,8);
  $("withdrawBalance2").textContent=fmt(d.withdrawable_balance,8);
  $("tradeUsdt").textContent="$"+fmt(d.withdrawable_balance,4);
  $("accCoins").textContent=d.points_balance??50;
  $("accDeposit").textContent=fmt(d.deposit_balance,2);
  $("accMining").textContent=fmt(d.mining_balance,8);
  $("accWithdraw").textContent=fmt(d.withdrawable_balance,8);
  $("refIncome").textContent=fmt(d.referral_income,2);
  const daily=Number(d.hourly_income||0);
  $("hourIncome").textContent="$"+fmt(daily,4);
  $("dayIncome").textContent="$"+fmt(daily*24,4);
  $("monthIncome").textContent="$"+fmt(daily*24*30,4);
}
async function loadMe(){
  $("userName").textContent=[user.first_name,user.last_name].filter(Boolean).join(" ")||"Lumaqix User";
  $("avatar").textContent=(user.first_name||"L").charAt(0).toUpperCase();
  if(!initData){setBalances({points_balance:50,deposit_balance:0,mining_balance:0,withdrawable_balance:0});return;}
  try{const r=await api("/api/me");if(r.ok){const d=await r.json();setBalances(d);$("invitedBy").textContent=d.invited_by||"—";}}catch(e){console.error(e);}
}
function openPage(name){
  document.querySelectorAll(".screen").forEach(x=>x.classList.remove("active"));
  if(name==="home") return;
  const el=$(name+"Screen"); if(el) el.classList.add("active");
  if(name==="topup")$("topupModal").classList.add("open");
  if(name==="withdraw")$("withdrawModal").classList.add("open");
}
document.querySelectorAll("[data-page]").forEach(b=>b.addEventListener("click",()=>openPage(b.dataset.page)));
document.querySelectorAll(".modal-close").forEach(b=>b.addEventListener("click",()=>b.closest(".modal").classList.remove("open")));
$("closeBtn").onclick=()=>{if(tg?.close)tg.close();};
$("copyInvite").onclick=async()=>{const id=user.id||"";const link=`https://t.me/lumaqixbot?startapp=${id}`;await navigator.clipboard?.writeText(link);$("inviteLink").textContent=link;};
if(user.id)$("inviteLink").textContent=`https://t.me/lumaqixbot?startapp=${user.id}`;
document.querySelectorAll(".quick button").forEach(b=>b.onclick=()=>{$("topupAmount").value=b.textContent});
$("dailyGift").onclick=async()=>{if(!initData)return;const r=await api("/api/daily-gift",{method:"POST"});const d=await r.json();$("giftMsg").textContent=d.message||"Gift claimed";if(d.user)setBalances(d.user);};
$("openGift").onclick=async()=>{if(!initData)return;const r=await api("/api/invite-gift/open",{method:"POST"});const d=await r.json();$("giftMsg").textContent=d.message||"No gift box";if(d.user)setBalances(d.user);};
$("withdrawBtn").onclick=async()=>{const amount=Number($("withdrawAmount").value),address=$("withdrawAddress").value.trim();if(amount<.1){$("withdrawMsg").textContent="Minimum withdrawal is 0.1 USDT.";return;}if(!/^0x[a-fA-F0-9]{40}$/.test(address)){$("withdrawMsg").textContent="Enter a valid BEP20 address.";return;}const r=await api("/api/withdraw",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({amount,address})});const d=await r.json();$("withdrawMsg").textContent=d.message||d.detail||"Request submitted";if(d.user)setBalances(d.user);};
async function btc(){
  const priceEl=$("btcPrice"),chgEl=$("btcChange");
  try{
    const ws=new WebSocket("wss://stream.binance.com:9443/ws/btcusdt@ticker");
    ws.onmessage=e=>{const d=JSON.parse(e.data);priceEl.textContent=Number(d.c).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});chgEl.textContent=Number(d.P)>=0?`+${Number(d.P).toFixed(2)}%`:`${Number(d.P).toFixed(2)}%`;};
    ws.onerror=()=>fallback();
  }catch(e){fallback();}
  async function fallback(){try{const r=await fetch("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT");const d=await r.json();priceEl.textContent=Number(d.lastPrice).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});chgEl.textContent=(Number(d.priceChangePercent)>=0?"+":"")+Number(d.priceChangePercent).toFixed(2)+"%";}catch(e){priceEl.textContent="Unavailable";}}
}
loadMe();btc();
