const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || "";
const params = new URLSearchParams(location.search);
const debugUserId = params.get("telegram_id");
const authSig = params.get("auth_sig");

const state = {
  view: "home",
  previousView: "home",
  newsFilter: "all",
  gamesMode: "subs",
  news: [],
  subscriptions: [],
  popularGames: [],
  searchResults: [],
  countdownTimer: null,
};

const titles = {
  home: "Главная",
  news: "Новости",
  games: "События в играх",
  gameDetails: "События в играх",
};

const api = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  if (debugUserId) url.searchParams.set("telegram_id", debugUserId);
  if (authSig) url.searchParams.set("auth_sig", authSig);
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

document.querySelector("#backBtn").addEventListener("click", () => {
  if (state.view === "home") return;
  setView(state.view === "gameDetails" ? "games" : "home");
});
document.querySelector("#refreshBtn").addEventListener("click", () => loadCurrent(true));
document.querySelector("#newsRefreshBtn").addEventListener("click", () => loadNews(true));
document.querySelector("#gameSearchBtn").addEventListener("click", () => searchGames());
document.querySelector("#gameSearch").addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchGames();
});

document.querySelector("#newsFilters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  state.newsFilter = button.dataset.filter;
  document.querySelectorAll("#newsFilters button").forEach((item) => item.classList.toggle("active", item === button));
  renderNews();
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;

  if (button.dataset.action === "go") setView(button.dataset.view);
  if (button.dataset.action === "games-mode") setGamesMode(button.dataset.mode);
  if (button.dataset.action === "subscribe") {
    subscribe(Number(button.dataset.universeId), Number(button.dataset.placeId), button.dataset.name || "Roblox Game", button);
  }
  if (button.dataset.action === "remove") removeSub(Number(button.dataset.id), button);
  if (button.dataset.action === "details") showDetails(Number(button.dataset.id));
});

function setView(view) {
  stopCountdown();
  state.previousView = state.view;
  state.view = view;
  document.querySelectorAll(".screen").forEach((screen) => screen.classList.toggle("active", screen.id === view));
  document.querySelector("#screenTitle").textContent = titles[view] || "Главная";
  document.querySelector("#backBtn").classList.toggle("visible", view !== "home");
  loadCurrent();
}

async function loadCurrent(force = false) {
  if (state.view === "news") await loadNews(force);
  if (state.view === "games") {
    if (state.gamesMode === "subs") await loadSubscriptions();
    if (state.gamesMode === "search" && (!state.popularGames.length || force)) await loadPopularGames();
  }
}

async function loadNews(force = false) {
  const list = document.querySelector("#newsList");
  if (state.news.length && !force) {
    renderNews();
    return;
  }
  list.innerHTML = skeleton("Загружаю новости...");
  try {
    const data = await api("/api/news");
    state.news = data.items || [];
    renderNews();
  } catch (error) {
    list.innerHTML = empty("Новости пока недоступны");
  }
}

function renderNews() {
  const list = document.querySelector("#newsList");
  const items = state.news.filter((item) => {
    const text = `${item.title || ""} ${item.description || ""}`.toLowerCase();
    if (state.newsFilter === "dev") return /studio|developer|creator|разработ/.test(text);
    if (state.newsFilter === "roblox") return true;
    return true;
  });
  list.innerHTML = items.length ? items.slice(0, 16).map(newsCard).join("") : empty("Новостей пока нет");
}

function setGamesMode(mode) {
  state.gamesMode = mode;
  document.querySelectorAll("[data-action='games-mode']").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  document.querySelector("#searchPanel").classList.toggle("visible", mode === "search");
  if (mode === "subs") loadSubscriptions();
  if (mode === "search") {
    if (state.popularGames.length) renderPopularGames();
    else loadPopularGames();
  }
}

async function loadSubscriptions() {
  const list = document.querySelector("#gamesList");
  list.innerHTML = skeleton("Загружаю подписки...");
  try {
    const data = await api("/api/subscriptions");
    state.subscriptions = data.items || [];
    renderSubscriptions();
  } catch (error) {
    list.innerHTML = empty("Откройте WebApp из Telegram");
  }
}

function renderSubscriptions() {
  const list = document.querySelector("#gamesList");
  list.innerHTML = state.subscriptions.length
    ? state.subscriptions.map(subscriptionCard).join("")
    : empty("Подписок пока нет. Добавьте игру через поиск.");
}

async function loadPopularGames() {
  const list = document.querySelector("#gamesList");
  document.querySelector("#gamesSubtitle").textContent = "Популярные игры";
  list.innerHTML = skeleton("Загружаю популярные игры...");
  try {
    const data = await api("/api/popular");
    state.popularGames = data.items || [];
    renderPopularGames();
  } catch (error) {
    list.innerHTML = empty("Популярные игры пока недоступны");
  }
}

function renderPopularGames() {
  const list = document.querySelector("#gamesList");
  document.querySelector("#gamesSubtitle").textContent = "Популярные игры";
  list.innerHTML = state.popularGames.length ? state.popularGames.map(searchCard).join("") : empty("Популярные игры пока недоступны");
}

async function searchGames() {
  const input = document.querySelector("#gameSearch");
  const query = input.value.trim();
  const list = document.querySelector("#gamesList");
  if (!query) return;
  document.querySelector("#gamesSubtitle").textContent = "Результаты поиска";
  list.innerHTML = skeleton("Ищу игры...");
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    state.searchResults = data.items || [];
    list.innerHTML = state.searchResults.length ? state.searchResults.map(searchCard).join("") : empty("Ничего не найдено");
  } catch (error) {
    list.innerHTML = empty("Поиск сейчас недоступен");
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
    showToast("Подписка добавлена");
    setGamesMode("subs");
  } catch (error) {
    tg?.HapticFeedback?.notificationOccurred("error");
    showToast(`Не удалось подписаться: ${error.message}`);
    if (button) {
      button.disabled = false;
      button.textContent = "Подписаться";
    }
  }
}

async function removeSub(id, button) {
  if (button) button.disabled = true;
  try {
    await api(`/api/subscriptions/${id}`, { method: "DELETE" });
    state.subscriptions = state.subscriptions.filter((item) => Number(item.id) !== id);
    showToast("Подписка удалена");
    if (state.view === "gameDetails") {
      setView("games");
    } else {
      renderSubscriptions();
    }
  } catch (error) {
    showToast(`Не удалось удалить: ${error.message}`);
    if (button) button.disabled = false;
  }
}

async function showDetails(id) {
  const sub = state.subscriptions.find((item) => Number(item.id) === id);
  if (!sub) return;
  setView("gameDetails");
  const body = document.querySelector("#detailsBody");
  body.innerHTML = detailsSkeleton(sub);
  try {
    const data = await api(`/api/subscriptions/${id}/events`);
    body.innerHTML = detailsView(sub, data.items || []);
  } catch (error) {
    body.innerHTML = detailsView(sub, [], "Не удалось загрузить события");
  }
}

function newsCard(item) {
  return `
    <a class="news-item" href="${escapeAttr(item.link || "#")}" target="_blank">
      <span class="news-thumb ${item.image ? "" : "placeholder"}">
        ${item.image ? `<img src="${escapeAttr(item.image)}" alt="">` : "R"}
      </span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description || "Короткая новость Roblox")}</small>
        <em>${formatDate(item.published_ts)}</em>
      </span>
    </a>
  `;
}

function subscriptionCard(item) {
  return `
    <article class="game-row" data-action="details" data-id="${Number(item.id)}">
      <img src="${thumbnailUrl(item.universe_id)}" alt="">
      <span>
        <strong>${escapeHtml(item.game_name)}</strong>
        <small>Обновления</small>
        <em>ID: ${Number(item.universe_id)}</em>
      </span>
      <button class="switch on" type="button" data-action="remove" data-id="${Number(item.id)}" aria-label="Отписаться"></button>
    </article>
  `;
}

function searchCard(item) {
  const name = escapeAttr(item.name || "Roblox Game");
  return `
    <article class="game-row">
      <img src="${thumbnailUrl(item.universe_id)}" alt="">
      <span>
        <strong>${escapeHtml(item.name || "Roblox Game")}</strong>
        <small>ID: ${Number(item.universe_id)}</small>
        <em>онлайн ${Number(item.playing || 0)}</em>
      </span>
      <button class="mini-primary" type="button" data-action="subscribe" data-universe-id="${Number(item.universe_id)}" data-place-id="${Number(item.place_id)}" data-name="${name}">Подписаться</button>
    </article>
  `;
}

function detailsSkeleton(sub) {
  return `
    <section class="details-hero">
      <img src="${thumbnailUrl(sub.universe_id)}" alt="">
      <span>
        <strong>${escapeHtml(sub.game_name)}</strong>
        <small>ID: ${Number(sub.universe_id)}</small>
        <button class="subscribed-pill" type="button">Вы подписаны</button>
      </span>
    </section>
    ${skeleton("Загружаю события...")}
  `;
}

function detailsView(sub, events, message = "") {
  const countdownEvent = getCountdownEvent(events);
  const visibleEvents = sortEvents(events).slice(0, 4);
  return `
    <section class="details-hero">
      <img src="${thumbnailUrl(sub.universe_id)}" alt="">
      <span>
        <strong>${escapeHtml(sub.game_name)}</strong>
        <small>ID: ${Number(sub.universe_id)}</small>
        <button class="subscribed-pill" type="button">Вы подписаны</button>
      </span>
    </section>

    <section class="info-panel">
      <h2>Что ты будешь получать:</h2>
      <p>✓ Обновления игры</p>
      <p>✓ Новые ивенты</p>
      <p>✓ Коды и награды</p>
      <p>✓ Анонсы и новости</p>
    </section>

    ${countdownEvent ? countdownPanel(countdownEvent) : ""}

    <section class="info-panel">
      <h2>Последнее событие</h2>
      ${visibleEvents.length ? visibleEvents.map(eventCard).join("") : `<p class="muted">${escapeHtml(message || "Активных событий пока нет")}</p>`}
    </section>

    <button class="danger-wide" type="button" data-action="remove" data-id="${Number(sub.id)}">Отписаться</button>
  `;
}

function eventCard(event) {
  const status = eventStatus(event);
  return `
    <article class="event-row">
      ${event.image_url ? `<img src="${escapeAttr(event.image_url)}" alt="">` : `<span class="event-placeholder">EV</span>`}
      <span>
        <strong>${escapeHtml(event.title || "Событие")}</strong>
        <small>${escapeHtml(eventDescription(event))}</small>
        <em>${escapeHtml(status)}</em>
      </span>
    </article>
  `;
}

function eventDescription(event) {
  const raw = event.description_ru || event.description || "";
  const cleaned = String(raw)
    .replace(/[\u{1F300}-\u{1FAFF}]/gu, " ")
    .replace(/[•·]+/g, " ")
    .replace(/\s*-\s*/g, ". ")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return "Подробности события скоро появятся.";
  return compactText(cleaned, 120);
}

function sortEvents(events) {
  return [...events].sort((a, b) => {
    const aStart = parseEventTime(a.start_time) || Number.MAX_SAFE_INTEGER;
    const bStart = parseEventTime(b.start_time) || Number.MAX_SAFE_INTEGER;
    return aStart - bStart;
  });
}

function eventStatus(event) {
  const startMs = parseEventTime(event.start_time);
  const endMs = parseEventTime(event.end_time);
  const now = Date.now();
  if (startMs && startMs <= now && (!endMs || endMs > now)) return "Уже идёт";
  if (startMs && startMs > now) return `Начнётся через ${formatCountdown(startMs - now)}`;
  if (endMs && endMs <= now) return "Завершено";
  return event.time_label || "";
}

function countdownPanel(event) {
  setTimeout(() => startCountdown(event), 0);
  return `
    <section class="countdown-panel" data-countdown>
      <div class="countdown-ring"><span data-countdown-days>0</span></div>
      <div>
        <h2 data-countdown-title>До ближайшего события</h2>
        <strong>${escapeHtml(event.title || "Событие")}</strong>
        <p data-countdown-value>Считаю время...</p>
      </div>
    </section>
  `;
}

function getCountdownEvent(events) {
  const now = Date.now();
  const withDates = events
    .map((event) => ({
      ...event,
      startMs: parseEventTime(event.start_time),
      endMs: parseEventTime(event.end_time),
    }))
    .filter((event) => event.startMs || event.endMs);

  const active = withDates
    .filter((event) => event.startMs && event.startMs <= now && (!event.endMs || event.endMs > now))
    .sort((a, b) => (a.endMs || Number.MAX_SAFE_INTEGER) - (b.endMs || Number.MAX_SAFE_INTEGER))[0];
  if (active) return active;

  return withDates
    .filter((event) => event.startMs && event.startMs > now)
    .sort((a, b) => a.startMs - b.startMs)[0];
}

function startCountdown(event) {
  stopCountdown();
  updateCountdown(event);
  state.countdownTimer = setInterval(() => updateCountdown(event), 1000);
}

function stopCountdown() {
  if (state.countdownTimer) {
    clearInterval(state.countdownTimer);
    state.countdownTimer = null;
  }
}

function updateCountdown(event) {
  const panel = document.querySelector("[data-countdown]");
  if (!panel) {
    stopCountdown();
    return;
  }
  const now = Date.now();
  const title = panel.querySelector("[data-countdown-title]");
  const value = panel.querySelector("[data-countdown-value]");
  const days = panel.querySelector("[data-countdown-days]");
  const isActive = event.startMs && event.startMs <= now && (!event.endMs || event.endMs > now);

  if (isActive) {
    title.textContent = "Событие уже идёт";
    value.textContent = event.endMs ? `Закончится через ${formatCountdown(event.endMs - now)}` : "Новых обновлений пока нет";
    days.textContent = "LIVE";
    panel.classList.add("live");
    return;
  }

  const diff = Math.max(0, (event.startMs || now) - now);
  title.textContent = "До ближайшего события";
  value.textContent = diff > 0 ? formatCountdown(diff) : "Событие уже идёт";
  days.textContent = Math.floor(diff / 86400000);
  panel.classList.remove("live");
}

function parseEventTime(value) {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function formatCountdown(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (days) return `${days} д ${hours} ч ${minutes} мин`;
  if (hours) return `${hours} ч ${minutes} мин ${seconds} сек`;
  return `${minutes} мин ${seconds} сек`;
}

function compactText(value, limit) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1).trim()}…` : text;
}

function thumbnailUrl(universeId) {
  return `/api/thumbnail/${Number(universeId) || 0}`;
}

function skeleton(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function empty(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function showToast(text) {
  const toast = document.querySelector("#toast");
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 3500);
}

function formatDate(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
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
