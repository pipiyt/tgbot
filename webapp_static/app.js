const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || "";
const debugUserId = new URLSearchParams(location.search).get("telegram_id");

const state = {
  view: "news",
  subscriptions: [],
};

const api = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  if (debugUserId) url.searchParams.set("telegram_id", debugUserId);
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
      ...(options.headers || {}),
    },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

document.querySelector("#refreshBtn").addEventListener("click", () => loadCurrent());
document.querySelector("#gameSearchBtn").addEventListener("click", () => searchGames());
document.querySelector("#gameSearch").addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchGames();
});

function setView(view) {
  state.view = view;
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === view));
  loadCurrent();
}

async function loadCurrent() {
  if (state.view === "news") await loadNews();
  if (state.view === "alerts") await loadSubscriptions();
}

async function loadNews() {
  const list = document.querySelector("#newsList");
  list.innerHTML = cardSkeleton("Загружаю новости...");
  try {
    const data = await api("/api/news");
    document.querySelector("#newsCount").textContent = `${data.items.length}`;
    list.innerHTML = data.items.length ? data.items.map(newsCard).join("") : empty();
  } catch (error) {
    list.innerHTML = empty("Не удалось загрузить новости");
  }
}

async function loadSubscriptions() {
  const list = document.querySelector("#subsList");
  list.innerHTML = cardSkeleton("Загружаю подписки...");
  try {
    const data = await api("/api/subscriptions");
    state.subscriptions = data.items;
    list.innerHTML = data.items.length ? data.items.map(subscriptionCard).join("") : empty("Подписок пока нет");
  } catch (error) {
    list.innerHTML = empty("Откройте WebApp из Telegram");
  }
}

async function searchGames() {
  const query = document.querySelector("#gameSearch").value.trim();
  const list = document.querySelector("#searchResults");
  if (!query) return;
  list.innerHTML = cardSkeleton("Ищу игру...");
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    list.innerHTML = data.items.length ? data.items.map(searchCard).join("") : empty("Ничего не найдено");
  } catch (error) {
    list.innerHTML = empty("Поиск недоступен");
  }
}

async function subscribe(universeId, placeId, name) {
  await api("/api/subscriptions", {
    method: "POST",
    body: JSON.stringify({ universe_id: universeId, place_id: placeId, name }),
  });
  tg?.HapticFeedback?.notificationOccurred("success");
  setView("alerts");
}

async function removeSub(id) {
  await api(`/api/subscriptions/${id}`, { method: "DELETE" });
  await loadSubscriptions();
}

async function showEvents(id) {
  const list = document.querySelector("#subsList");
  list.innerHTML = cardSkeleton("Загружаю события...");
  try {
    const data = await api(`/api/subscriptions/${id}/events`);
    if (!data.items.length) {
      list.innerHTML = empty("Активных или будущих событий нет") + backButton();
      return;
    }
    list.innerHTML = data.items.map((item) => eventCard(item, data.subscription)).join("") + backButton();
  } catch (error) {
    list.innerHTML = empty("Не удалось загрузить события") + backButton();
  }
}

function newsCard(item) {
  return `
    <a class="card ${item.image ? "" : "no-image"}" href="${escapeAttr(item.link || "#")}" target="_blank">
      ${item.image ? `<img class="thumb" src="${escapeAttr(item.image)}" alt="">` : ""}
      <div>
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(item.description || "")}</p>
        <span class="meta">${formatDate(item.published_ts)}</span>
      </div>
    </a>
  `;
}

function subscriptionCard(item) {
  return `
    <div class="card">
      <img class="thumb" src="https://www.roblox.com/asset-thumbnail/image?assetId=${Number(item.place_id)}&width=150&height=150&format=png" alt="">
      <div>
        <h3>${escapeHtml(item.game_name)}</h3>
        <p>Подписка на обновления и события</p>
        <div class="row-actions">
          <button class="primary" onclick="showEvents(${Number(item.id)})">События</button>
          <button class="danger" onclick="removeSub(${Number(item.id)})">Удалить</button>
          <span class="switch"></span>
        </div>
      </div>
    </div>
  `;
}

function searchCard(item) {
  const name = escapeAttr(item.name || "Roblox Game");
  return `
    <div class="card">
      <img class="thumb" src="https://www.roblox.com/asset-thumbnail/image?assetId=${Number(item.place_id)}&width=150&height=150&format=png" alt="">
      <div>
        <h3>${escapeHtml(item.name)}</h3>
        <p>ID: ${Number(item.universe_id)} · онлайн ${Number(item.playing || 0)}</p>
        <button class="primary" onclick="subscribe(${Number(item.universe_id)}, ${Number(item.place_id)}, '${name}')">Подписаться</button>
      </div>
    </div>
  `;
}

function eventCard(item, sub) {
  return `
    <div class="card ${item.image_url ? "" : "no-image"}">
      ${item.image_url ? `<img class="thumb" src="${escapeAttr(item.image_url)}" alt="">` : ""}
      <div>
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(item.description_ru || item.description || "")}</p>
        <span class="meta">${escapeHtml(item.time_label || "")}</span>
      </div>
    </div>
  `;
}

function cardSkeleton(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function empty(text = "Пока ничего нет") {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function backButton() {
  return `<button class="primary" onclick="loadSubscriptions()">← Назад</button>`;
}

function formatDate(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

loadCurrent();
