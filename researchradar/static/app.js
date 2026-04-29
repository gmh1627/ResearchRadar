const state = {
  profile: "default",
  view: "digest",
  selectedItem: null,
  search: "",
  arxivDate: "",
  blogDate: "",
};

const $ = (id) => document.getElementById(id);

const TYPE_LABELS = {
  paper: "论文",
  blog: "博客",
  repo: "代码",
  discussion: "讨论",
  cn_community: "中文源",
};

const STATUS_LABELS = {
  success: "成功",
  partial: "部分成功",
  running: "运行中",
  waiting: "等待中",
  error: "错误",
  idle: "空闲",
};

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
  if (!value) return "无日期";
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

function todayIsoDate() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function formatDayLabel(value) {
  if (!value) return "无日期";
  return value === todayIsoDate() ? `今天 ${value}` : value;
}

function typeLabel(type) {
  return TYPE_LABELS[type] || type || "条目";
}

function crawlerLabel(crawler) {
  if (!crawler.running) return "空闲";
  const message = String(crawler.message || "");
  const match = message.match(/crawling (\d+) day\(s\)/);
  if (match) return `抓取中 ${match[1]} 天`;
  return message || "运行中";
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
  const score = item.score !== undefined ? `<span class="muted">分数 ${item.score}</span>` : "";
  const reason = item.relevance_reason ? `<p><strong>推荐理由：</strong>${escapeHtml(item.relevance_reason)}</p>` : "";
  const action = item.recommended_action ? `<p><strong>建议动作：</strong>${escapeHtml(item.recommended_action)}</p>` : "";
  const tags = (item.tags || []).slice(0, 6).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const summary = item.display_summary || item.summary_zh || "中文摘要生成中，请稍后刷新。";
  return `
    <article class="item-card${selected}" data-id="${item.id}">
      <div class="item-meta">
        <span class="pill ${pillClass(item.source_type)}">${escapeHtml(typeLabel(item.source_type))}</span>
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
  $("detailType").textContent = typeLabel(item.source_type);
  $("detailType").className = `pill ${pillClass(item.source_type)}`;
  $("detailSource").textContent = `${item.source_name} · ${formatDate(item.published_at || item.collected_at)}`;
  $("detailTitle").textContent = item.title;
  $("detailSummary").textContent = item.display_summary || item.summary_zh || "中文摘要生成中，请稍后刷新。";
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
  $("crawlerStatus").textContent = crawlerLabel(data.crawler);
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
  $("digestList").innerHTML = data.items.map((item) => renderItem(item, "digest")).join("") || `<div class="empty-state">暂无个性日报数据。</div>`;
  bindItemClicks($("digestList"));
}

async function loadDateChoices({ selectId, countId, dateKey, params }) {
  const q = encodeURIComponent(state.search || "");
  const data = await api(`/api/dates?${params}&q=${q}&days=365&limit=366`);
  const rows = data.dates || [];
  const select = $(selectId);
  if (!rows.length) {
    state[dateKey] = "";
    select.innerHTML = `<option value="">暂无日期</option>`;
    select.disabled = true;
    $(countId).textContent = "暂无数据";
    return "";
  }
  const current = state[dateKey];
  const selected = rows.some((row) => row.date === current) ? current : rows[0].date;
  state[dateKey] = selected;
  select.disabled = false;
  select.innerHTML = rows
    .map((row) => `<option value="${escapeHtml(row.date)}">${escapeHtml(formatDayLabel(row.date))} · ${row.count} 条</option>`)
    .join("");
  select.value = selected;
  return selected;
}

async function loadArxiv(options = {}) {
  const reloadDates = options.reloadDates !== false;
  if (reloadDates || !state.arxivDate) {
    await loadDateChoices({
      selectId: "arxivDate",
      countId: "arxivCount",
      dateKey: "arxivDate",
      params: "source_id=arxiv_core",
    });
  }
  if (!state.arxivDate) {
    $("arxivList").innerHTML = `<div class="empty-state">暂无 arXiv 论文数据。</div>`;
    return;
  }
  const q = encodeURIComponent(state.search || "");
  const data = await api(`/api/items?source_id=arxiv_core&date=${encodeURIComponent(state.arxivDate)}&q=${q}&limit=120&translate_limit=80`);
  $("arxivMeta").textContent = `${formatDayLabel(state.arxivDate)} 更新的 AI / ML / Agent 论文`;
  $("arxivCount").textContent = `显示 ${data.items.length} / 共 ${data.total} 条`;
  $("arxivList").innerHTML = data.items.map((item) => renderItem(item, "arxiv")).join("") || `<div class="empty-state">这个日期没有匹配的 arXiv 论文。</div>`;
  bindItemClicks($("arxivList"));
}

async function loadRadar() {
  const days = $("daysFilter").value;
  const type = $("typeFilter").value;
  const q = encodeURIComponent(state.search || "");
  const data = await api(`/api/items?days=${days}&source_type=${encodeURIComponent(type)}&q=${q}&limit=120`);
  $("radarList").innerHTML = data.items.map((item) => renderItem(item)).join("") || `<div class="empty-state">暂无雷达数据。</div>`;
  bindItemClicks($("radarList"));
}

async function loadBlogs(options = {}) {
  const reloadDates = options.reloadDates !== false;
  if (reloadDates || !state.blogDate) {
    await loadDateChoices({
      selectId: "blogDate",
      countId: "blogCount",
      dateKey: "blogDate",
      params: "source_type=blog%2Ccn_community",
    });
  }
  if (!state.blogDate) {
    $("blogList").innerHTML = `<div class="empty-state">暂无博客与实验室数据。</div>`;
    return;
  }
  const q = encodeURIComponent(state.search || "");
  const data = await api(`/api/items?source_type=blog%2Ccn_community&date=${encodeURIComponent(state.blogDate)}&q=${q}&limit=150&translate_limit=80`);
  $("blogCount").textContent = `显示 ${data.items.length} / 共 ${data.total} 条`;
  $("blogList").innerHTML = data.items.map((item) => renderItem(item, "blogs")).join("") || `<div class="empty-state">这个日期没有匹配的博客与实验室动态。</div>`;
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
          <span class="pill">${escapeHtml(STATUS_LABELS[status] || status)}</span>
          <span class="muted">${escapeHtml(date)} · ${count} 条</span>
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
        <div class="muted">${formatDate(note.created_at)} · 重要性 ${note.importance}</div>
        <h3>${escapeHtml(note.title)}</h3>
        <p>${escapeHtml(note.content)}</p>
        <div class="tags">${(note.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
      </article>
    `)
    .join("") || `<div class="empty-state">暂无研究笔记。</div>`;
}

async function refreshView() {
  await loadHealth();
  if (state.view === "digest") await loadDigest();
  if (state.view === "arxiv") await loadArxiv();
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
  $("crawlerStatus").textContent = "错误";
  alert(error.message || String(error));
}

async function triggerCrawl(days) {
  await api("/api/crawl", {
    method: "POST",
    body: JSON.stringify({ days, mode: "recent" }),
  });
  $("crawlerStatus").textContent = `已排队 ${days} 天`;
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
  $("chatAnswer").textContent = "正在思考...";
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
  $("arxivDate").addEventListener("change", () => {
    state.arxivDate = $("arxivDate").value;
    loadArxiv({ reloadDates: false }).catch(showError);
  });
  $("blogDate").addEventListener("change", () => {
    state.blogDate = $("blogDate").value;
    loadBlogs({ reloadDates: false }).catch(showError);
  });
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
