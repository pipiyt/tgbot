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
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
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
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;

  if (button.dataset.action === "subscribe") {
    subscribe(Number(button.dataset.universeId), Number(button.dataset.placeId), button.dataset.name || "Roblox Game", button);
  }
  if (button.dataset.action === "remove") removeSub(Number(button.dataset.id));
  if (button.dataset.action === "events") showEvents(Number(button.dataset.id));
  if (button.dataset.action === "back-to-subs") loadSubscriptions();
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

async function subscribe(universeId, placeId, name, button) {
  if (button) {
    button.disabled = true;
    button.textContent = "Добавляю...";
  }
  try {
    await api("/api/subscriptions", {
      method: "POST",
      body: JSON.stringify({ universe_id: universeId, place_id: placeId, name, init_data: initData }),
    });
    tg?.HapticFeedback?.notificationOccurred("success");
    setView("alerts");
  } catch (error) {
    tg?.HapticFeedback?.notificationOccurred("error");
    showToast(`Не удалось подписаться: ${error.message}`);
    if (button) {
      button.disabled = false;
      button.textContent = "Подписаться";
    }
  }
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
      <img class="thumb" src="${thumbnailUrl(item.universe_id)}" alt="">
      <div>
        <h3>${escapeHtml(item.game_name)}</h3>
        <p>Подписка на обновления и события</p>
        <div class="row-actions">
          <button class="primary" data-action="events" data-id="${Number(item.id)}">События</button>
          <button class="danger" data-action="remove" data-id="${Number(item.id)}">Удалить</button>
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
      <img class="thumb" src="${thumbnailUrl(item.universe_id)}" alt="">
      <div>
        <h3>${escapeHtml(item.name)}</h3>
        <p>ID: ${Number(item.universe_id)} · онлайн ${Number(item.playing || 0)}</p>
        <button class="primary" data-action="subscribe" data-universe-id="${Number(item.universe_id)}" data-place-id="${Number(item.place_id)}" data-name="${name}">Подписаться</button>
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
  return `<button class="primary" data-action="back-to-subs">← Назад</button>`;
}

function thumbnailUrl(universeId) {
  return `/api/thumbnail/${Number(universeId) || 0}`;
}

function showToast(text) {
  const toast = document.querySelector("#toast");
  if (!toast) return;
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 3500);
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
