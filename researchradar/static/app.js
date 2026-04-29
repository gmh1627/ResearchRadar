const state = {
  profile: "default",
  view: "digest",
  selectedItem: null,
  search: "",
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function formatDate(value) {
  if (!value) return "undated";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function truncate(value, max = 360) {
  value = String(value || "");
  return value.length > max ? value.slice(0, max - 1).trim() + "..." : value;
}

function pillClass(type) {
  if (type === "paper") return "paper";
  if (type === "repo") return "repo";
  if (type === "discussion") return "discussion";
  return "";
}

function renderItem(item, mode = "radar") {
  const selected = state.selectedItem && state.selectedItem.id === item.id ? " selected" : "";
  const score = item.score !== undefined ? `<span class="muted">score ${item.score}</span>` : "";
  const reason = item.relevance_reason ? `<p><strong>Why:</strong> ${escapeHtml(item.relevance_reason)}</p>` : "";
  const action = item.recommended_action ? `<p><strong>Action:</strong> ${escapeHtml(item.recommended_action)}</p>` : "";
  const tags = (item.tags || []).slice(0, 6).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const summary = item.display_summary || item.summary || "暂无中文摘要。";
  return `
    <article class="item-card${selected}" data-id="${item.id}">
      <div class="item-meta">
        <span class="pill ${pillClass(item.source_type)}">${escapeHtml(item.source_type)}</span>
        <span class="muted">${escapeHtml(item.source_name)}</span>
        <span class="muted">${formatDate(item.published_at || item.collected_at)}</span>
        ${score}
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(truncate(summary, mode === "digest" ? 260 : 320))}</p>
      ${reason}
      ${action}
      <div class="tags">${tags}</div>
    </article>
  `;
}

function bindItemClicks(container) {
  container.querySelectorAll(".item-card").forEach((card) => {
    card.addEventListener("click", async () => {
      const item = await api(`/api/items/${card.dataset.id}`);
      selectItem(item);
    });
  });
}

function selectItem(item) {
  state.selectedItem = item;
  $("detailEmpty").classList.add("hidden");
  $("detailPanel").classList.remove("hidden");
  $("detailType").textContent = item.source_type;
  $("detailType").className = `pill ${pillClass(item.source_type)}`;
  $("detailSource").textContent = `${item.source_name} · ${formatDate(item.published_at || item.collected_at)}`;
  $("detailTitle").textContent = item.title;
  $("detailSummary").textContent = item.display_summary || item.summary_zh || item.summary || "该条目没有摘要，建议打开原始来源。";
  $("detailLink").href = item.url;
  $("detailTags").innerHTML = (item.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  $("chatAnswer").textContent = "";
  $("detailStatus").textContent = "";
  document.querySelectorAll(".item-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.id === item.id);
  });
}

async function loadHealth() {
  const data = await api("/api/health");
  $("itemCount").textContent = data.stats.item_count;
  $("sourceCount").textContent = data.stats.source_count;
  $("crawlerStatus").textContent = data.crawler.running ? data.crawler.message : "idle";
  const host = window.location.host;
  $("serverLine").textContent = host;
}

async function loadProfiles() {
  const data = await api("/api/profiles");
  $("profileSelect").innerHTML = data.profiles
    .map((profile) => `<option value="${profile.user_id}">${escapeHtml(profile.display_name || profile.user_id)}</option>`)
    .join("");
  $("profileSelect").value = state.profile;
}

async function loadDigest() {
  const days = $("digestDays").value;
  const data = await api(`/api/digest?user_id=${encodeURIComponent(state.profile)}&days=${days}`);
  $("digestMeta").textContent = `${data.profile.display_name || data.profile.user_id} · ${formatDate(data.generated_at)}`;
  $("digestList").innerHTML = data.items.map((item) => renderItem(item, "digest")).join("") || `<div class="empty-state">暂无 digest 数据。</div>`;
  bindItemClicks($("digestList"));
}

async function loadRadar() {
  const days = $("daysFilter").value;
  const type = $("typeFilter").value;
  const q = encodeURIComponent(state.search || "");
  const data = await api(`/api/items?days=${days}&source_type=${encodeURIComponent(type)}&q=${q}&limit=120`);
  $("radarList").innerHTML = data.items.map((item) => renderItem(item)).join("") || `<div class="empty-state">暂无 radar 数据。</div>`;
  bindItemClicks($("radarList"));
}

async function loadBlogs() {
  const q = encodeURIComponent(state.search || "");
  const data = await api(`/api/items?days=30&q=${q}&limit=150`);
  const blogTypes = new Set(["blog", "cn_community"]);
  const rows = data.items.filter((item) => blogTypes.has(item.source_type));
  $("blogList").innerHTML = rows.map((item) => renderItem(item)).join("") || `<div class="empty-state">暂无 blog/lab 数据。</div>`;
  bindItemClicks($("blogList"));
}

async function loadSources() {
  const data = await api("/api/sources");
  $("sourceTable").innerHTML = data.sources
    .map(({ source, latest }) => {
      const status = latest ? latest.status : "waiting";
      const date = latest ? latest.target_date : "-";
      const count = latest ? latest.items_found : 0;
      const error = latest && latest.error ? latest.error : "";
      return `
        <div class="source-row">
          <div><strong>${escapeHtml(source.name || source.id)}</strong><div class="muted">${escapeHtml(source.id)}</div></div>
          <span class="pill">${escapeHtml(status)}</span>
          <span class="muted">${escapeHtml(date)} · ${count} items</span>
          <span class="muted">${escapeHtml(truncate(error, 180))}</span>
        </div>
      `;
    })
    .join("");
}

async function loadNotes() {
  const data = await api(`/api/notes?user_id=${encodeURIComponent(state.profile)}`);
  $("notesList").innerHTML = data.notes
    .map((note) => `
      <article class="note">
        <div class="muted">${formatDate(note.created_at)} · importance ${note.importance}</div>
        <h3>${escapeHtml(note.title)}</h3>
        <p>${escapeHtml(note.content)}</p>
        <div class="tags">${(note.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
      </article>
    `)
    .join("") || `<div class="empty-state">暂无 research notes。</div>`;
}

async function refreshView() {
  await loadHealth();
  if (state.view === "digest") await loadDigest();
  if (state.view === "radar") await loadRadar();
  if (state.view === "blogs") await loadBlogs();
  if (state.view === "sources") await loadSources();
  if (state.view === "notes") await loadNotes();
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".view").forEach((panel) => panel.classList.toggle("active", panel.id === `view-${view}`));
  refreshView().catch(showError);
}

function showError(error) {
  console.error(error);
  $("crawlerStatus").textContent = "error";
  alert(error.message || String(error));
}

async function triggerCrawl(days) {
  await api("/api/crawl", {
    method: "POST",
    body: JSON.stringify({ days, mode: "recent" }),
  });
  $("crawlerStatus").textContent = `queued ${days} day(s)`;
  setTimeout(refreshView, 1500);
}

async function sendFeedback(action) {
  if (!state.selectedItem) return;
  $("detailStatus").textContent = "正在保存反馈...";
  await api(`/api/items/${state.selectedItem.id}/feedback`, {
    method: "POST",
    body: JSON.stringify({ user_id: state.profile, action }),
  });
  const labels = {
    like: "已标记为有用",
    save: "已收藏",
    deep_read: "已加入深读",
    ignore: "已忽略",
    not_relevant: "已标记为不相关",
  };
  $("detailStatus").textContent = labels[action] || "反馈已保存";
  await refreshView();
}

async function askQuestion() {
  if (!state.selectedItem) return;
  const question = $("chatQuestion").value.trim();
  if (!question) return;
  $("chatAnswer").textContent = "Thinking...";
  const data = await api("/api/chat", {
    method: "POST",
    body: JSON.stringify({
      user_id: state.profile,
      scope: "item",
      item_id: state.selectedItem.id,
      question,
      save_note: $("saveNote").checked,
    }),
  });
  $("chatAnswer").textContent = data.answer;
  if ($("saveNote").checked) await loadNotes();
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("profileSelect").addEventListener("change", () => {
    state.profile = $("profileSelect").value;
    refreshView().catch(showError);
  });
  $("digestDays").addEventListener("change", () => loadDigest().catch(showError));
  $("daysFilter").addEventListener("change", () => loadRadar().catch(showError));
  $("typeFilter").addEventListener("change", () => loadRadar().catch(showError));
  $("searchBtn").addEventListener("click", () => {
    state.search = $("searchInput").value.trim();
    refreshView().catch(showError);
  });
  $("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      state.search = $("searchInput").value.trim();
      refreshView().catch(showError);
    }
  });
  $("crawl1").addEventListener("click", () => triggerCrawl(1).catch(showError));
  $("crawl14").addEventListener("click", () => triggerCrawl(14).catch(showError));
  $("crawl30").addEventListener("click", () => triggerCrawl(30).catch(showError));
  document.querySelectorAll(".feedback").forEach((button) => {
    button.addEventListener("click", () => sendFeedback(button.dataset.action).catch(showError));
  });
  $("askBtn").addEventListener("click", () => askQuestion().catch(showError));
}

async function init() {
  bindEvents();
  await loadProfiles();
  await refreshView();
  setInterval(loadHealth, 10000);
}

init().catch(showError);
