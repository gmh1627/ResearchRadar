const PAGE_SIZE = 80;
const HIDDEN_TAGS = new Set(["rag"]);

function createPage() {
  return { items: [], total: 0, loading: false };
}

const state = {
  profile: "default",
  view: "digest",
  selectedItem: null,
  search: "",
  arxivDate: "",
  blogDate: "",
  pages: {
    arxiv: createPage(),
    radar: createPage(),
    blogs: createPage(),
  },
  itemCache: new Map(),
  graph: {
    nodes: [],
    edges: [],
    animation: null,
    activeNode: null,
  },
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
  skipped: "已跳过",
  waiting: "等待中",
  error: "错误",
  idle: "空闲",
};

const RELIABILITY_LABELS = {
  high: "高",
  medium: "中",
  low: "低",
};

const EVIDENCE_ROLE_LABELS = {
  primary_research: "正式研究",
  official_update: "官方更新",
  lab_update: "实验室动态",
  cn_research_update: "中文研究动态",
  code_signal: "代码信号",
  engineering_discussion: "工程讨论",
};

const DATE_KIND_LABELS = {
  discovered: "发现",
  published: "发布",
  updated: "更新",
};

const FEEDBACK_ACTION_LABELS = {
  save: "收藏",
  deep_read: "深读",
  like: "有用",
  ignore: "忽略",
  not_relevant: "不相关",
};

let toastTimer = null;

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

function formatDate(value, mode = "short") {
  if (!value) return "无日期";
  try {
    const options =
      mode === "full"
        ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
        : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" };
    return new Intl.DateTimeFormat("zh-CN", options).format(new Date(value));
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
  const match = message.match(/crawling (.+?) \((\d+)\/(\d+)\)/);
  if (match) return `抓取 ${match[1]} · ${match[2]}/${match[3]}`;
  return message || "运行中";
}

function sourceUrl(source) {
  if (source.homepage) return source.homepage;
  if (source.fallback_url) return source.fallback_url;
  if (source.type !== "rss" && source.url) return source.url;
  return source.url || "";
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

function safeUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function balanceMathDelimiters(value) {
  const text = String(value || "");
  let dollars = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "$" && text[i - 1] !== "\\") dollars += 1;
  }
  return dollars % 2 === 1 ? `${text}$` : text;
}

function prepareDisplayText(value, maxLength = null) {
  let text = String(value || "");
  const wasTruncated = maxLength && text.length > maxLength;
  if (maxLength) text = truncate(text, maxLength);
  if (wasTruncated) text = balanceMathDelimiters(text);
  return text;
}

function renderInlineMarkdown(value) {
  let text = String(value || "");
  const tokens = [];
  const addToken = (html) => {
    const token = `@@RRTOKEN${tokens.length}@@`;
    tokens.push({ token, html });
    return token;
  };
  const addLinkToken = (url, label) => {
    const href = safeUrl(url);
    if (!href) return label || url;
    return addToken(`<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${escapeHtml(label || url)}</a>`);
  };

  text = text.replace(/`([^`\n]+)`/g, (_, code) => addToken(`<code>${escapeHtml(code)}</code>`));
  text = text.replace(/(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+?\$)/g, (match) => addToken(escapeHtml(match)));
  text = text.replace(/\\href\s*\{([^{}]+)\}\s*\{([^{}]+)\}/g, (_, url, label) => addLinkToken(url, label));
  text = text.replace(/\\url\s*\{([^{}]+)\}/g, (_, url) => addLinkToken(url, url));
  text = text.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => addLinkToken(url, label));
  text = text.replace(/https?:\/\/[^\s<>"{}\\]+/g, (match) => {
    let url = match;
    let trailing = "";
    while (/[.,;:!?)]$/.test(url)) {
      trailing = `${url.slice(-1)}${trailing}`;
      url = url.slice(0, -1);
    }
    return `${addLinkToken(url, url)}${trailing}`;
  });

  let html = escapeHtml(text);
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
  for (const { token, html: linkHtml } of tokens) {
    html = html.replaceAll(token, linkHtml);
  }
  return html;
}

function renderRichText(value, maxLength = null) {
  return renderInlineMarkdown(prepareDisplayText(value, maxLength)).replaceAll("\n", "<br>");
}

function renderMarkdown(value, maxLength = null) {
  const text = prepareDisplayText(value, maxLength).replace(/\r\n?/g, "\n");
  const lines = text.split("\n");
  const html = [];
  let paragraph = [];
  let quote = [];
  let list = null;
  let inCode = false;
  let codeLang = "";
  let codeLines = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${renderInlineMarkdown(paragraph.join("\n")).replaceAll("\n", "<br>")}</p>`);
    paragraph = [];
  };
  const flushQuote = () => {
    if (!quote.length) return;
    html.push(`<blockquote>${renderInlineMarkdown(quote.join("\n")).replaceAll("\n", "<br>")}</blockquote>`);
    quote = [];
  };
  const flushList = () => {
    if (!list) return;
    const items = list.items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("");
    html.push(`<${list.type}>${items}</${list.type}>`);
    list = null;
  };
  const flushBlocks = () => {
    flushParagraph();
    flushQuote();
    flushList();
  };
  const flushCode = () => {
    const lang = codeLang && /^[a-z0-9_-]+$/i.test(codeLang) ? ` class="language-${escapeHtml(codeLang)}"` : "";
    html.push(`<pre><code${lang}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    inCode = false;
    codeLang = "";
    codeLines = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");
    const trimmed = line.trim();
    const fence = trimmed.match(/^```([a-z0-9_-]*)\s*$/i);
    if (inCode) {
      if (fence) {
        flushCode();
      } else {
        codeLines.push(rawLine);
      }
      continue;
    }
    if (fence) {
      flushBlocks();
      inCode = true;
      codeLang = fence[1] || "";
      continue;
    }
    if (!trimmed) {
      flushBlocks();
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+?)\s*#*$/);
    if (heading) {
      flushBlocks();
      const level = Math.min(6, heading[1].length + 2);
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    if (/^[-*_]{3,}$/.test(trimmed)) {
      flushBlocks();
      html.push("<hr>");
      continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      flushQuote();
      const type = unordered ? "ul" : "ol";
      if (!list || list.type !== type) {
        flushList();
        list = { type, items: [] };
      }
      list.items.push((unordered || ordered)[1]);
      continue;
    }

    const quoted = line.match(/^\s*>\s?(.*)$/);
    if (quoted) {
      flushParagraph();
      flushList();
      quote.push(quoted[1]);
      continue;
    }

    flushQuote();
    flushList();
    paragraph.push(line);
  }

  if (inCode) flushCode();
  flushBlocks();
  return html.join("") || "";
}

function typesetMath(root = document.body) {
  if (!window.MathJax || typeof window.MathJax.typesetPromise !== "function") return;
  window.MathJax.typesetPromise([root]).catch((error) => console.warn("MathJax render failed", error));
}

function setRichText(element, value, maxLength = null) {
  if (!element) return;
  element.innerHTML = renderRichText(value, maxLength);
  typesetMath(element);
}

function setMarkdownText(element, value, maxLength = null) {
  if (!element) return;
  element.innerHTML = renderMarkdown(value, maxLength);
  typesetMath(element);
}

window.addEventListener("mathjax-ready", () => typesetMath(document.body));

function clampNumber(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.max(min, Math.min(max, number));
}

function compactNumber(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(number);
}

function pillClass(type) {
  if (type === "paper") return "paper";
  if (type === "repo") return "repo";
  if (type === "discussion") return "discussion";
  if (type === "cn_community") return "cn";
  return "";
}

function itemTimestamp(item) {
  return item.display_timestamp || item.published_at || item.collected_at;
}

function formatItemDate(item, mode = "short") {
  const prefix = DATE_KIND_LABELS[item.date_kind] || (item.published_at ? "发布" : "发现");
  return `${prefix} ${formatDate(itemTimestamp(item), mode)}`;
}

function rememberItems(items) {
  for (const item of items) {
    state.itemCache.set(item.id, item);
  }
}

function visibleTags(tags) {
  return (tags || []).filter((tag) => {
    const value = String(tag || "").trim();
    const head = value.toLowerCase().split(/\s|·/)[0];
    return value && !HIDDEN_TAGS.has(head);
  });
}

function renderItemFacts(item) {
  const meta = item.metadata || {};
  const facts = [];
  if (item.source_id === "github") {
    facts.push(`★ ${compactNumber(meta.stars)}`);
    facts.push(`fork ${compactNumber(meta.forks)}`);
    if (meta.language) facts.push(meta.language);
  } else if (item.source_id === "hackernews") {
    facts.push(`${compactNumber(meta.points)} points`);
    facts.push(`${compactNumber(meta.comments)} comments`);
  } else if (item.source_type === "paper" && item.categories && item.categories.length) {
    facts.push(item.categories.slice(0, 3).join(" / "));
  } else if (item.date_kind === "discovered") {
    facts.push("无可靠发布日期");
  }
  return facts.map((fact) => `<span class="mini-fact">${escapeHtml(fact)}</span>`).join("");
}

function renderItem(item, mode = "radar") {
  const selected = state.selectedItem && state.selectedItem.id === item.id ? " selected" : "";
  const score = item.score !== undefined ? `<span class="mini-fact">匹配 ${escapeHtml(item.score)}</span>` : "";
  const tags = visibleTags(item.tags).slice(0, 6).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const summary = item.display_summary || item.summary_zh || "中文摘要生成中，请稍后刷新。";
  const facts = renderItemFacts(item);
  return `
    <article class="item-card${selected}" data-id="${escapeHtml(item.id)}" tabindex="0">
      <div class="item-meta">
        <span class="pill ${pillClass(item.source_type)}">${escapeHtml(typeLabel(item.source_type))}</span>
        <span class="muted">${escapeHtml(item.source_name)}</span>
        <span class="muted">${escapeHtml(formatItemDate(item))}</span>
        ${score}
        ${facts}
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <p class="rich-text">${renderRichText(summary, mode === "digest" ? 260 : 320)}</p>
      <div class="tags">${tags}</div>
    </article>
  `;
}

function renderList(containerId, items, mode, emptyText) {
  rememberItems(items);
  const container = $(containerId);
  container.innerHTML = items.map((item) => renderItem(item, mode)).join("") || `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
  bindItemClicks(container);
  typesetMath(container);
}

function bindItemClicks(container) {
  container.querySelectorAll(".item-card").forEach((card) => {
    const open = async () => {
      const cached = state.itemCache.get(card.dataset.id) || {};
      const detail = await api(`/api/items/${card.dataset.id}?user_id=${encodeURIComponent(state.profile)}`);
      selectItem({ ...cached, ...detail });
    };
    card.addEventListener("click", () => open().catch(showError));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open().catch(showError);
      }
    });
  });
}

function selectItem(item) {
  state.selectedItem = item;
  $("detailEmpty").classList.add("hidden");
  $("detailPanel").classList.remove("hidden");
  $("detailType").textContent = typeLabel(item.source_type);
  $("detailType").className = `pill ${pillClass(item.source_type)}`;
  $("detailSource").textContent = `${item.source_name} · ${formatItemDate(item, "full")}`;
  $("detailTitle").textContent = item.title;
  setMarkdownText($("detailSummary"), item.display_summary || item.summary_zh || "中文摘要生成中，请稍后刷新。");
  $("detailLink").href = item.url || "#";
  $("detailTags").innerHTML = visibleTags(item.tags).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  renderDetailReasons(item);
  renderDetailFacts(item);
  renderDetailLinks(item);
  $("chatAnswer").textContent = "";
  $("noteTitle").value = "";
  $("itemNote").value = "";
  $("detailStatus").textContent = item.date_kind === "discovered" ? "未检测到可靠发布日期，当前按首次发现时间排序。" : "";
  document.querySelectorAll(".item-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.id === item.id);
  });
}

function renderDetailReasons(item) {
  const target = $("detailReasons");
  target.classList.add("hidden");
  target.innerHTML = "";
}

function renderDetailFacts(item) {
  const meta = item.metadata || {};
  const facts = [];
  facts.push(["日期", formatItemDate(item, "full")]);
  if (item.source_reliability) facts.push(["可信度", RELIABILITY_LABELS[item.source_reliability] || item.source_reliability]);
  if (item.evidence_role) facts.push(["证据角色", EVIDENCE_ROLE_LABELS[item.evidence_role] || item.evidence_role]);
  if (item.score !== undefined) facts.push(["匹配分", item.score]);
  if (item.authors && item.authors.length) facts.push(["作者", item.authors.slice(0, 6).join("、")]);
  if (item.categories && item.categories.length) facts.push(["分类", item.categories.slice(0, 6).join(" / ")]);
  if (meta.arxiv_id) facts.push(["arXiv ID", meta.arxiv_id]);
  if (meta.language) facts.push(["语言", meta.language]);
  if (meta.stars !== undefined) facts.push(["Stars", compactNumber(meta.stars)]);
  if (meta.forks !== undefined) facts.push(["Forks", compactNumber(meta.forks)]);
  if (meta.points !== undefined) facts.push(["HN points", compactNumber(meta.points)]);
  if (meta.comments !== undefined) facts.push(["HN comments", compactNumber(meta.comments)]);
  if (item.last_seen_at) facts.push(["最近见到", formatDate(item.last_seen_at, "full")]);
  $("detailFacts").innerHTML = facts
    .map(([label, value]) => `
      <div class="evidence-fact">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
    `)
    .join("");
}

function renderDetailLinks(item) {
  const links = item.evidence_links || fallbackLinks(item);
  $("detailLinks").innerHTML = links
    .map((link) => `<a href="${escapeHtml(link.url)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`)
    .join("");
}

function fallbackLinks(item) {
  const meta = item.metadata || {};
  const links = [];
  if (item.url) links.push({ label: item.source_type === "repo" ? "代码仓库" : "来源", url: item.url });
  if (meta.pdf_url) links.push({ label: "PDF", url: meta.pdf_url });
  if (meta.hn_url) links.push({ label: "HN", url: meta.hn_url });
  return links;
}

async function loadHealth() {
  const data = await api("/api/health");
  $("itemCount").textContent = data.stats.item_count;
  $("sourceCount").textContent = data.stats.source_count;
  $("crawlerStatus").textContent = crawlerLabel(data.crawler);
  $("serverLine").textContent = window.location.host;
}

async function loadProfiles() {
  const data = await api("/api/profiles");
  $("profileSelect").innerHTML = data.profiles
    .map((profile) => `<option value="${escapeHtml(profile.user_id)}">${escapeHtml(profile.display_name || profile.user_id)}</option>`)
    .join("");
  $("profileSelect").value = state.profile;
}

async function loadDigest() {
  const days = $("digestDays").value;
  const data = await api(`/api/digest?user_id=${encodeURIComponent(state.profile)}&days=${days}`);
  const count = (data.items || []).length;
  const upper = data.limit ? ` / 上限 ${data.limit}` : "";
  $("digestMeta").textContent = `${data.profile.display_name || data.profile.user_id} · ${formatDate(data.generated_at, "full")} · ${count}${upper} 条`;
  renderDigestSections(data.sections || [], data.items || []);
}

function renderDigestSections(sections, fallbackItems) {
  rememberItems(fallbackItems);
  const container = $("digestList");
  if (!sections.length) {
    renderList("digestList", fallbackItems, "digest", "暂无个性日报数据。");
    return;
  }
  container.innerHTML = sections
    .map(
      (section) => `
        <section class="digest-section">
          <div class="digest-section-head">
            <div>
              <h3>${escapeHtml(section.label)}</h3>
              <p>${escapeHtml(section.description || "")}</p>
            </div>
            <span class="mini-fact">${escapeHtml(section.count || section.items.length)} 条</span>
          </div>
          <div class="item-list digest-section-list">
            ${(section.items || []).map((item) => renderItem(item, "digest")).join("")}
          </div>
        </section>
      `
    )
    .join("");
  bindItemClicks(container);
  typesetMath(container);
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
  const append = options.append === true;
  const reloadDates = options.reloadDates !== false && !append;
  if (reloadDates || !state.arxivDate) {
    await loadDateChoices({
      selectId: "arxivDate",
      countId: "arxivCount",
      dateKey: "arxivDate",
      params: "source_id=arxiv_core",
    });
  }
  if (!state.arxivDate) {
    state.pages.arxiv = createPage();
    renderList("arxivList", [], "arxiv", "暂无 arXiv 论文数据。");
    updateMoreButton("arxiv", "arxivMore");
    return;
  }
  const page = state.pages.arxiv;
  if (page.loading) return;
  if (!append) page.items = [];
  page.loading = true;
  updateMoreButton("arxiv", "arxivMore");
  try {
    const q = encodeURIComponent(state.search || "");
    const offset = append ? page.items.length : 0;
    const data = await api(
      `/api/items?source_id=arxiv_core&date=${encodeURIComponent(state.arxivDate)}&q=${q}&limit=${PAGE_SIZE}&offset=${offset}&translate_limit=50`
    );
    page.total = data.total;
    page.items = append ? page.items.concat(data.items) : data.items;
    $("arxivMeta").textContent = `${formatDayLabel(state.arxivDate)} 更新的 AI / ML / Agent 论文`;
    $("arxivCount").textContent = `显示 ${page.items.length} / 共 ${page.total} 条`;
    renderList("arxivList", page.items, "arxiv", "这个日期没有匹配的 arXiv 论文。");
  } finally {
    page.loading = false;
    updateMoreButton("arxiv", "arxivMore");
  }
}

async function loadRadar(options = {}) {
  const append = options.append === true;
  const page = state.pages.radar;
  if (page.loading) return;
  if (!append) page.items = [];
  page.loading = true;
  updateMoreButton("radar", "radarMore");
  try {
    const days = $("daysFilter").value;
    const type = $("typeFilter").value;
    const q = encodeURIComponent(state.search || "");
    const offset = append ? page.items.length : 0;
    const data = await api(`/api/items?days=${days}&source_type=${encodeURIComponent(type)}&q=${q}&limit=${PAGE_SIZE}&offset=${offset}`);
    page.total = data.total;
    page.items = append ? page.items.concat(data.items) : data.items;
    renderList("radarList", page.items, "radar", "暂无雷达数据。");
  } finally {
    page.loading = false;
    updateMoreButton("radar", "radarMore");
  }
}

async function loadBlogs(options = {}) {
  const append = options.append === true;
  const reloadDates = options.reloadDates !== false && !append;
  if (reloadDates || !state.blogDate) {
    await loadDateChoices({
      selectId: "blogDate",
      countId: "blogCount",
      dateKey: "blogDate",
      params: "source_type=blog%2Ccn_community",
    });
  }
  if (!state.blogDate) {
    state.pages.blogs = createPage();
    renderList("blogList", [], "blogs", "暂无博客与实验室数据。");
    updateMoreButton("blogs", "blogMore");
    return;
  }
  const page = state.pages.blogs;
  if (page.loading) return;
  if (!append) page.items = [];
  page.loading = true;
  updateMoreButton("blogs", "blogMore");
  try {
    const q = encodeURIComponent(state.search || "");
    const offset = append ? page.items.length : 0;
    const data = await api(
      `/api/items?source_type=blog%2Ccn_community&date=${encodeURIComponent(state.blogDate)}&q=${q}&limit=${PAGE_SIZE}&offset=${offset}&translate_limit=50`
    );
    page.total = data.total;
    page.items = append ? page.items.concat(data.items) : data.items;
    $("blogCount").textContent = `显示 ${page.items.length} / 共 ${page.total} 条`;
    renderList("blogList", page.items, "blogs", "这个日期没有匹配的博客与实验室动态。");
  } finally {
    page.loading = false;
    updateMoreButton("blogs", "blogMore");
  }
}

function updateMoreButton(pageKey, buttonId) {
  const page = state.pages[pageKey];
  const button = $(buttonId);
  if (!button || !page) return;
  const hasMore = page.items.length < page.total;
  button.classList.toggle("hidden", !hasMore);
  button.disabled = page.loading;
  button.textContent = page.loading ? "加载中..." : `加载更多 (${page.items.length}/${page.total})`;
}

async function loadSources() {
  const data = await api("/api/sources");
  $("sourceTable").innerHTML = data.sources
    .map(({ source, latest }) => {
      const status = latest ? latest.status : "waiting";
      const date = latest ? latest.target_date : "-";
      const count = latest ? latest.items_found : 0;
      const error = latest && latest.error ? latest.error : "";
      const url = sourceUrl(source);
      const tag = url ? "a" : "div";
      const attrs = url ? ` href="${escapeHtml(url)}" target="_blank" rel="noreferrer"` : "";
      return `
        <${tag} class="source-row"${attrs}>
          <div>
            <strong>${escapeHtml(source.name || source.id)}</strong>
            <div class="muted">${escapeHtml(source.id)}</div>
          </div>
          <span class="pill status-${escapeHtml(status)}">${escapeHtml(STATUS_LABELS[status] || status)}</span>
          <span class="muted">${escapeHtml(date)} · ${count} 条</span>
          <span class="muted">${escapeHtml(error ? truncate(error, 180) : url ? "点击访问来源" : "暂无来源链接")}</span>
        </${tag}>
      `;
    })
    .join("");
}

async function loadNotes() {
  const data = await api(`/api/knowledge?user_id=${encodeURIComponent(state.profile)}`);
  renderKnowledgeStats(data.stats || {});
  renderKnowledgeProfile(data.profile || {}, data.stats || {});
  renderKnowledgeQueue(data.items || []);
  renderConversations(data.conversations || []);
  renderNotes(data.notes || []);
  await loadKnowledgeGraph();
}

function renderKnowledgeStats(stats) {
  const statItems = [
    ["收藏", stats.saved || 0],
    ["深读", stats.deep_read || 0],
    ["笔记", stats.notes || 0],
    ["问答", stats.conversations || 0],
  ];
  const tags = (stats.top_tags || [])
    .filter((row) => !HIDDEN_TAGS.has(String(row.tag || "").trim().toLowerCase()))
    .map((row) => `<span class="tag">${escapeHtml(row.tag)} · ${escapeHtml(row.count)}</span>`)
    .join("");
  $("knowledgeStats").innerHTML = `
    ${statItems
      .map(([label, value]) => `
        <div class="knowledge-stat">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `)
      .join("")}
    <div class="knowledge-tags">${tags || '<span class="muted">暂无偏好标签</span>'}</div>
  `;
}

function renderProfileTags(items, emptyText = "暂无") {
  const values = Array.isArray(items) ? visibleTags(items) : [];
  if (!values.length) return `<span class="muted">${escapeHtml(emptyText)}</span>`;
  return values.map((value) => `<span class="tag">${escapeHtml(value)}</span>`).join("");
}

function renderProfileField(label, value) {
  return `
    <div class="profile-field">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "未设置")}</strong>
    </div>
  `;
}

function renderKnowledgeProfile(profile, stats) {
  const topTags = (stats.top_tags || []).map((row) => `${row.tag} · ${row.count}`);
  $("knowledgeProfile").innerHTML = `
    <div class="subhead profile-head">
      <h3>研究画像</h3>
      <span class="muted profile-title">${escapeHtml(profile.display_name || "未命名画像")} · ${escapeHtml(profile.user_id || state.profile)}</span>
    </div>
    <div class="profile-grid">
      <section class="profile-block primary">
        <h4>主兴趣</h4>
        <div class="tags">${renderProfileTags(profile.primary_topics)}</div>
      </section>
      <section class="profile-block">
        <h4>次兴趣</h4>
        <div class="tags">${renderProfileTags(profile.secondary_topics)}</div>
      </section>
      <section class="profile-block negative">
        <h4>排除 / 降权</h4>
        <div class="tags">${renderProfileTags(profile.negative_topics)}</div>
      </section>
      <section class="profile-block">
        <h4>偏好来源</h4>
        <div class="tags">${renderProfileTags(profile.preferred_sources)}</div>
      </section>
      <section class="profile-block">
        <h4>行为沉淀</h4>
        <div class="tags">${renderProfileTags(topTags, "暂无反馈标签")}</div>
      </section>
      <section class="profile-block profile-settings">
        <h4>研究设定</h4>
        <div class="profile-fields">
          ${renderProfileField("角色", profile.role)}
          ${renderProfileField("专业", profile.major)}
          ${renderProfileField("语言", profile.digest_language)}
          ${renderProfileField("技术深度", profile.technical_depth)}
          ${renderProfileField("代码链接", profile.include_code_links ? "需要" : "不强制")}
        </div>
      </section>
    </div>
  `;
}

function renderKnowledgeQueue(items) {
  rememberItems(items);
  $("knowledgeQueue").innerHTML =
    items
      .slice(0, 16)
      .map((item) => {
        const action = FEEDBACK_ACTION_LABELS[item.feedback_action] || item.feedback_action || "已标记";
        const summary = item.display_summary || item.summary_zh || item.summary || "";
        return `
          <article class="compact-item item-card" data-id="${escapeHtml(item.id)}" tabindex="0">
            <button class="icon-action delete-feedback" data-id="${escapeHtml(item.id)}" data-action="${escapeHtml(item.feedback_action || "")}" title="从收藏/深读移除" aria-label="从收藏/深读移除">×</button>
            <div class="item-meta">
              <span class="pill ${pillClass(item.source_type)}">${escapeHtml(typeLabel(item.source_type))}</span>
              <span class="mini-fact">${escapeHtml(action)}</span>
              <span class="muted">${escapeHtml(formatItemDate(item))}</span>
            </div>
            <h3>${escapeHtml(item.title)}</h3>
            <p class="rich-text">${renderRichText(summary, 140)}</p>
          </article>
        `;
      })
      .join("") || `<div class="empty-state compact-empty">暂无收藏或深读条目。</div>`;
  bindItemClicks($("knowledgeQueue"));
  bindDeleteActions($("knowledgeQueue"));
  typesetMath($("knowledgeQueue"));
}

function renderConversations(conversations) {
  $("conversationList").innerHTML =
    conversations
      .slice(0, 12)
      .map(
        (conv) => `
          <article class="conversation">
            <button class="icon-action delete-conversation" data-id="${escapeHtml(conv.id)}" title="删除问答记录" aria-label="删除问答记录">×</button>
            <div class="muted">${formatDate(conv.created_at, "full")}${conv.item_title ? ` · ${escapeHtml(conv.item_title)}` : ""}</div>
            <h3 class="rich-text">${renderRichText(conv.question, 160)}</h3>
            <div class="rich-text markdown-body compact-markdown">${renderMarkdown(conv.answer, 240)}</div>
          </article>
        `
      )
      .join("") || `<div class="empty-state compact-empty">暂无问答记录。</div>`;
  bindDeleteActions($("conversationList"));
  typesetMath($("conversationList"));
}

function renderNotes(notes) {
  $("notesList").innerHTML =
    notes
      .map(
        (note) => `
      <article class="note">
        <button class="icon-action delete-note" data-id="${escapeHtml(note.id)}" title="删除笔记" aria-label="删除笔记">×</button>
        <div class="muted">${formatDate(note.created_at, "full")} · 重要性 ${note.importance}</div>
        <h3>${escapeHtml(note.title)}</h3>
        ${note.item_title ? `<div class="note-source">${escapeHtml(note.item_source_name || "来源")} · ${escapeHtml(note.item_title)}</div>` : ""}
        <div class="rich-text markdown-body note-content">${renderMarkdown(note.content)}</div>
        <div class="tags">${visibleTags(note.tags).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
      </article>
    `
      )
      .join("") || `<div class="empty-state">暂无知识笔记。</div>`;
  bindDeleteActions($("notesList"));
  typesetMath($("notesList"));
}

async function loadKnowledgeGraph() {
  const data = await api(`/api/knowledge/graph?user_id=${encodeURIComponent(state.profile)}&limit=90`);
  renderKnowledgeGraph(data);
}

function renderKnowledgeGraph(graph) {
  const canvas = $("knowledgeGraph");
  if (!canvas) return;
  const nodes = (graph.nodes || []).map((node) => ({
    ...node,
    tags: visibleTags(node.tags),
    weight: Number(node.weight || 14),
    x: 0,
    y: 0,
  }));
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const edges = (graph.edges || [])
    .map((edge) => ({ ...edge, sourceNode: nodeMap.get(edge.source), targetNode: nodeMap.get(edge.target) }))
    .filter((edge) => edge.sourceNode && edge.targetNode);
  layoutGraph(canvas, nodes, edges);
  state.graph.nodes = nodes;
  state.graph.edges = edges;
  state.graph.activeNode = null;
  $("knowledgeGraphMeta").textContent = `${nodes.length} 个节点 · ${edges.length} 条关系`;
  if (state.graph.animation) cancelAnimationFrame(state.graph.animation);
  state.graph.animation = null;
  bindGraphCanvas(canvas);
  drawGraph(canvas.getContext("2d"), nodes, edges);
}

function bindGraphCanvas(canvas) {
  if (canvas.dataset.bound === "true") return;
  canvas.dataset.bound = "true";
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = (event.clientX - rect.left) * scaleX;
    const y = (event.clientY - rect.top) * scaleY;
    const nextNode = state.graph.nodes.find((node) => Math.hypot(node.x - x, node.y - y) < graphRadius(node) + 8) || null;
    const changed = (state.graph.activeNode && state.graph.activeNode.id) !== (nextNode && nextNode.id);
    state.graph.activeNode = nextNode;
    canvas.style.cursor = state.graph.activeNode ? "pointer" : "default";
    if (changed) drawGraph(canvas.getContext("2d"), state.graph.nodes, state.graph.edges);
  });
  canvas.addEventListener("mouseleave", () => {
    state.graph.activeNode = null;
    canvas.style.cursor = "default";
    drawGraph(canvas.getContext("2d"), state.graph.nodes, state.graph.edges);
  });
  canvas.addEventListener("click", () => {
    const node = state.graph.activeNode;
    if (!node) return;
    api(`/api/items/${node.id}?user_id=${encodeURIComponent(state.profile)}`)
      .then((detail) => selectItem({ ...node, ...detail }))
      .catch(showError);
  });
}

function layoutGraph(canvas, nodes, edges) {
  const width = canvas.width;
  const height = canvas.height;
  const centers = {
    paper: { x: width * 0.61, y: height * 0.38 },
    repo: { x: width * 0.35, y: height * 0.62 },
    discussion: { x: width * 0.68, y: height * 0.69 },
    blog: { x: width * 0.34, y: height * 0.32 },
    cn_community: { x: width * 0.48, y: height * 0.71 },
    other: { x: width * 0.52, y: height * 0.52 },
  };
  const groups = new Map();
  for (const node of nodes) {
    const group = graphGroup(node);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(node);
  }
  for (const [group, groupNodes] of groups) {
    const center = centers[group] || centers.other;
    const spread = Math.min(Math.min(width, height) * 0.29, 62 + Math.sqrt(groupNodes.length) * 34);
    groupNodes.sort((a, b) => (b.weight || 0) - (a.weight || 0));
    groupNodes.forEach((node, index) => {
      const jitter = hashValue(node.id) - 0.5;
      const angle = index * 2.399963 + jitter * 0.9;
      const radius = groupNodes.length <= 1 ? 0 : spread * Math.sqrt((index + 0.35) / groupNodes.length);
      node.clusterX = center.x;
      node.clusterY = center.y;
      node.x = clampNumber(center.x + Math.cos(angle) * radius + jitter * 18, 34, width - 34);
      node.y = clampNumber(center.y + Math.sin(angle) * radius - jitter * 14, 34, height - 34);
    });
  }

  for (let iteration = 0; iteration < 120; iteration += 1) {
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = b.x - a.x || 0.01;
        const dy = b.y - a.y || 0.01;
        const dist = Math.max(Math.hypot(dx, dy), 1);
        const minDist = graphRadius(a) + graphRadius(b) + 18;
        const force = dist < minDist ? ((minDist - dist) / dist) * 0.42 : 18 / (dist * dist);
        a.x -= dx * force;
        a.y -= dy * force;
        b.x += dx * force;
        b.y += dy * force;
      }
    }
    for (const edge of edges) {
      const a = edge.sourceNode;
      const b = edge.targetNode;
      const dx = b.x - a.x || 0.01;
      const dy = b.y - a.y || 0.01;
      const dist = Math.max(Math.hypot(dx, dy), 1);
      const target = 82 + Math.max(0, 4 - Math.min(edge.weight || 1, 4)) * 12;
      const force = (dist - target) * 0.0038 * Math.min(edge.weight || 1, 4);
      a.x += dx * force;
      a.y += dy * force;
      b.x -= dx * force;
      b.y -= dy * force;
    }
    for (const node of nodes) {
      node.x += (node.clusterX - node.x) * 0.022;
      node.y += (node.clusterY - node.y) * 0.022;
      node.x = clampNumber(node.x, 34, width - 34);
      node.y = clampNumber(node.y, 34, height - 34);
    }
  }
}

function drawGraph(context, nodes, edges) {
  const width = context.canvas.width;
  const height = context.canvas.height;
  context.clearRect(0, 0, width, height);
  if (!nodes.length) {
    context.fillStyle = "#697386";
    context.font = "16px system-ui, -apple-system, Segoe UI, sans-serif";
    context.fillText("暂无可绘制的知识图谱：先打开、收藏或追问一些条目。", 28, 48);
    return;
  }

  const activeId = state.graph.activeNode && state.graph.activeNode.id;
  const activeNeighbors = new Set();
  if (activeId) {
    activeNeighbors.add(activeId);
    for (const edge of edges) {
      if (edge.sourceNode.id === activeId) activeNeighbors.add(edge.targetNode.id);
      if (edge.targetNode.id === activeId) activeNeighbors.add(edge.sourceNode.id);
    }
  }

  context.save();
  context.lineCap = "round";
  for (const edge of [...edges].sort((a, b) => (a.weight || 0) - (b.weight || 0))) {
    const active = activeId && (edge.sourceNode.id === activeId || edge.targetNode.id === activeId);
    const dim = activeId && !active;
    context.beginPath();
    context.moveTo(edge.sourceNode.x, edge.sourceNode.y);
    context.lineTo(edge.targetNode.x, edge.targetNode.y);
    context.strokeStyle = active
      ? "rgba(92, 86, 130, 0.42)"
      : `rgba(88, 104, 103, ${dim ? 0.035 : Math.min(0.22, 0.04 + (edge.weight || 1) * 0.035)})`;
    context.lineWidth = active ? Math.min(3.4, 1.1 + (edge.weight || 1) * 0.28) : Math.min(2.1, 0.45 + (edge.weight || 1) * 0.14);
    context.stroke();
  }

  const orderedNodes = [...nodes].sort((a, b) => {
    if (a.id === activeId) return 1;
    if (b.id === activeId) return -1;
    return (a.weight || 0) - (b.weight || 0);
  });
  for (const node of orderedNodes) {
    const active = activeId === node.id;
    const neighbor = activeNeighbors.has(node.id);
    const dim = activeId && !active && !neighbor;
    const radius = graphRadius(node);
    context.beginPath();
    context.arc(node.x, node.y, radius, 0, Math.PI * 2);
    context.fillStyle = active ? "#7c3f76" : graphColor(node.source_type);
    context.globalAlpha = dim ? 0.28 : active ? 0.94 : 0.78;
    context.fill();
    context.globalAlpha = 1;
    context.lineWidth = 1;
    context.strokeStyle = "rgba(255, 255, 255, 0.78)";
    context.stroke();
    if (active) {
      context.lineWidth = 4;
      context.strokeStyle = "rgba(124, 63, 118, 0.35)";
      context.stroke();
    }
    const labelY = node.y - radius < 22 ? node.y + radius + 14 : node.y - radius - 10;
    context.font = active ? "600 13px system-ui, -apple-system, Segoe UI, sans-serif" : "12px system-ui, -apple-system, Segoe UI, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.lineWidth = 4;
    context.strokeStyle = "rgba(255, 255, 255, 0.86)";
    context.strokeText(node.label, node.x, labelY);
    context.fillStyle = dim ? "rgba(24, 32, 42, 0.34)" : "#18202a";
    context.fillText(node.label, node.x, labelY);
  }
  context.restore();
}

function graphRadius(node) {
  return Math.max(9, Math.min(24, Number(node.weight || 14) / 1.65));
}

function graphGroup(node) {
  return ["paper", "repo", "discussion", "blog", "cn_community"].includes(node.source_type) ? node.source_type : "other";
}

function hashValue(value) {
  let hash = 0;
  const text = String(value || "");
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
  }
  return (hash % 1000) / 1000;
}

function graphColor(type) {
  if (type === "paper") return "#6e9690";
  if (type === "repo") return "#6e855d";
  if (type === "discussion") return "#8d919d";
  if (type === "blog") return "#b5a06d";
  if (type === "cn_community") return "#9a7c9c";
  return "#8ba6a2";
}

function bindDeleteActions(container) {
  container.querySelectorAll(".delete-feedback").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteFeedback(button.dataset.id, button.dataset.action).catch(showError);
    });
  });
  container.querySelectorAll(".delete-conversation").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteConversation(button.dataset.id).catch(showError);
    });
  });
  container.querySelectorAll(".delete-note").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteNote(button.dataset.id).catch(showError);
    });
  });
}

async function deleteFeedback(itemId, action) {
  await api(`/api/items/${encodeURIComponent(itemId)}/feedback?user_id=${encodeURIComponent(state.profile)}&action=${encodeURIComponent(action || "")}`, {
    method: "DELETE",
  });
  showToast("已从收藏/深读移除");
  await loadNotes();
}

async function deleteConversation(conversationId) {
  await api(`/api/conversations/${encodeURIComponent(conversationId)}?user_id=${encodeURIComponent(state.profile)}`, { method: "DELETE" });
  showToast("问答记录已删除");
  await loadNotes();
}

async function deleteNote(noteId) {
  await api(`/api/notes/${encodeURIComponent(noteId)}?user_id=${encodeURIComponent(state.profile)}`, { method: "DELETE" });
  showToast("笔记已删除");
  await loadNotes();
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

function showToast(message) {
  const toast = $("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 3800);
}

function showError(error) {
  console.error(error);
  $("crawlerStatus").textContent = "错误";
  showToast(error.message || String(error));
}

async function triggerCrawl(days) {
  document.querySelectorAll(".ops button").forEach((button) => (button.disabled = true));
  try {
    await api("/api/crawl", {
      method: "POST",
      body: JSON.stringify({ days, mode: "recent" }),
    });
    $("crawlerStatus").textContent = `已排队 ${days} 天`;
    showToast(`已提交 ${days} 天抓取任务`);
    setTimeout(() => refreshView().catch(showError), 1500);
  } finally {
    document.querySelectorAll(".ops button").forEach((button) => (button.disabled = false));
  }
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
  showToast(labels[action] || "反馈已保存");
  await refreshView();
}

async function askQuestion() {
  if (!state.selectedItem) {
    showToast("请先选择一条内容再提问");
    return;
  }
  const question = $("chatQuestion").value.trim();
  if (!question) return;
  $("askBtn").disabled = true;
  $("chatAnswer").textContent = "正在读取原文并生成回答；如果外部模型较慢，系统会自动降级为本地回答。";
  try {
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
    setMarkdownText($("chatAnswer"), data.answer || "后端返回了空回答，请稍后重试。");
    if ($("saveNote").checked) await loadNotes();
  } catch (error) {
    console.error(error);
    setMarkdownText(
      $("chatAnswer"),
      `这次没有收到后端回答。\n\n错误信息：${error.message || String(error)}\n\n可以稍后重试；如果连续出现，优先检查 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL 配置。`
    );
    showToast("提问失败，已把错误写入回答框");
  } finally {
    $("askBtn").disabled = false;
  }
}

async function saveItemNote() {
  if (!state.selectedItem) {
    showToast("请先选择一条内容");
    return;
  }
  const content = $("itemNote").value.trim();
  if (!content) {
    showToast("先写一点笔记内容");
    return;
  }
  const title = $("noteTitle").value.trim() || `条目笔记：${state.selectedItem.title.slice(0, 60)}`;
  $("saveItemNote").disabled = true;
  try {
    await api("/api/notes", {
      method: "POST",
      body: JSON.stringify({
        user_id: state.profile,
        item_id: state.selectedItem.id,
        title,
        content,
        tags: visibleTags(state.selectedItem.tags),
        importance: Number($("noteImportance").value || 3),
      }),
    });
    $("noteTitle").value = "";
    $("itemNote").value = "";
    showToast("笔记已保存到知识库");
    if (state.view === "notes") await loadNotes();
  } finally {
    $("saveItemNote").disabled = false;
  }
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
    state.arxivDate = "";
    state.blogDate = "";
    refreshView().catch(showError);
  });
  $("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      state.search = $("searchInput").value.trim();
      state.arxivDate = "";
      state.blogDate = "";
      refreshView().catch(showError);
    }
  });
  $("crawl1").addEventListener("click", () => triggerCrawl(1).catch(showError));
  $("crawl14").addEventListener("click", () => triggerCrawl(14).catch(showError));
  $("crawl30").addEventListener("click", () => triggerCrawl(30).catch(showError));
  $("arxivMore").addEventListener("click", () => loadArxiv({ append: true, reloadDates: false }).catch(showError));
  $("radarMore").addEventListener("click", () => loadRadar({ append: true }).catch(showError));
  $("blogMore").addEventListener("click", () => loadBlogs({ append: true, reloadDates: false }).catch(showError));
  document.querySelectorAll(".feedback").forEach((button) => {
    button.addEventListener("click", () => sendFeedback(button.dataset.action).catch(showError));
  });
  $("askBtn").addEventListener("click", () => askQuestion().catch(showError));
  $("saveItemNote").addEventListener("click", () => saveItemNote().catch(showError));
}

async function init() {
  bindEvents();
  await loadProfiles();
  await refreshView();
  setInterval(() => loadHealth().catch(showError), 10000);
}

init().catch(showError);
