const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const initData = tg?.initData || "";
const user = tg?.initDataUnsafe?.user || {};

function apiUrl(path) {
  const sep = path.includes("?") ? "&" : "?";
  return path + sep + "initData=" + encodeURIComponent(initData);
}

async function loadMe() {
  if (!initData) {
    document.getElementById("message").textContent =
      "⚠️ Open this page inside Telegram Mini App.";
    return;
  }

  const res = await fetch(apiUrl("/api/me"));
  if (!res.ok) {
    document.getElementById("message").textContent = "Could not verify Telegram user.";
    return;
  }

  const data = await res.json();
  document.getElementById("balance").textContent = data.balance ?? 0;
}

async function daily() {
  const btn = document.getElementById("dailyBtn");
  btn.disabled = true;

  const res = await fetch(apiUrl("/api/daily"), { method: "POST" });
  const data = await res.json();

  if (data.ok) {
    document.getElementById("balance").textContent = data.balance;
    document.getElementById("message").textContent = "🎉 +100 points added!";
  } else {
    document.getElementById("message").textContent = "⏳ Daily bonus already claimed.";
  }

  btn.disabled = false;
}

async function leaderboard() {
  const res = await fetch("/api/leaderboard");
  const data = await res.json();
  const box = document.getElementById("leaderboard");

  if (!data.length) {
    box.textContent = "No users yet.";
    return;
  }

  box.innerHTML = data.map((u, i) => `
    <div class="rank">
      <span>#${i + 1} ${escapeHtml(u.first_name || u.username || "User")}</span>
      <b>${u.balance} pts</b>
    </div>
  `).join("");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"
  }[c]));
}

document.getElementById("dailyBtn").addEventListener("click", daily);

document.getElementById("shareBtn").addEventListener("click", async () => {
  const id = user.id;
  if (!id) return;

  const link = `https://t.me/LumaqixBot?startapp=${id}`;
  const text = `🚀 Join me on Lumaqix!\n${link}`;

  if (tg?.openTelegramLink) {
    tg.openTelegramLink(
      `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("Join me on Lumaqix!")}`
    );
  } else if (navigator.share) {
    await navigator.share({ title: "Lumaqix", text });
  } else {
    await navigator.clipboard.writeText(link);
    document.getElementById("message").textContent = "Invite link copied!";
  }
});

loadMe();
leaderboard();
