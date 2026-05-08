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
  radarDate: "",
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
  signal: "线索",
};

const STATUS_LABELS = {
  success: "成功",
  partial: "部分成功",
  running: "运行中",
  interrupted: "已中断",
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
  curated_secondary_signal: "外部精选线索",
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
  text = text.replace(/\\%/g, "%");
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
  if (type === "signal") return "signal";
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

function formatScore(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return number <= 1 ? Math.round(number * 100) : Math.round(number);
}

function scoreClass(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  const normalized = number <= 1 ? number * 100 : number;
  if (normalized >= 78) return "score-high";
  if (normalized >= 62) return "score-mid";
  return "score-muted";
}

function itemReason(item) {
  return item.relevance_reason || (item.metadata && item.metadata.aihot_reason) || "";
}

function renderItem(item, mode = "radar") {
  const selected = state.selectedItem && state.selectedItem.id === item.id ? " selected" : "";
  const scoreValue = formatScore(item.score);
  const score = scoreValue !== "" ? `<span class="mini-fact score-pill ${scoreClass(item.score)}">分 ${escapeHtml(scoreValue)}</span>` : "";
  const tags = visibleTags(item.tags).slice(0, 6).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const summary = item.display_summary || item.summary_zh || "中文摘要生成中，请稍后刷新。";
  const facts = renderItemFacts(item);
  const reason = itemReason(item);
  return `
    <article class="item-card${selected}" data-id="${escapeHtml(item.id)}" data-type="${escapeHtml(item.source_type || "")}" tabindex="0">
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
      ${reason ? `<div class="item-reason"><span>推荐理由</span>${escapeHtml(truncate(reason, 170))}</div>` : ""}
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
  const rows = [];
  if (item.relevance_reason) {
    rows.push(["推荐理由", item.relevance_reason]);
  }
  if (item.recommended_action) {
    rows.push(["建议动作", item.recommended_action]);
  }
  if (item.score_parts && Object.keys(item.score_parts).length) {
    rows.push(["评分拆解", scorePartsText(item.score_parts)]);
  }
  if (!rows.length) {
    target.classList.add("hidden");
    target.innerHTML = "";
    return;
  }
  target.classList.remove("hidden");
  target.innerHTML = rows
    .map(([label, value]) => `
      <div>
        <span>${escapeHtml(label)}</span>
        <p>${escapeHtml(value)}</p>
      </div>
    `)
    .join("");
}

function scorePartsText(parts) {
  const labels = {
    relevance: "相关",
    llm_relevance: "GPT相关",
    llm_novelty: "GPT新颖",
    llm_significance: "GPT重要",
    llm_actionability: "GPT可行动",
    llm_credibility: "GPT可信",
    credibility: "可信",
    novelty: "新颖",
    significance: "重要",
    actionability: "可行动",
    trend: "趋势",
    research_value: "研究价值",
    personalization: "个性化",
    recency: "新鲜",
  };
  return Object.entries(labels)
    .filter(([key]) => parts[key] !== undefined)
    .map(([key, label]) => `${label} ${formatScore(parts[key])}`)
    .join(" · ");
}

function renderDetailFacts(item) {
  const meta = item.metadata || {};
  const facts = [];
  facts.push(["日期", formatItemDate(item, "full")]);
  if (item.source_reliability) facts.push(["可信度", RELIABILITY_LABELS[item.source_reliability] || item.source_reliability]);
  if (item.evidence_role) facts.push(["证据角色", EVIDENCE_ROLE_LABELS[item.evidence_role] || item.evidence_role]);
  if (item.source_tier) facts.push(["信源等级", item.source_tier]);
  if (item.score !== undefined && item.score !== null) facts.push(["质量分", formatScore(item.score)]);
  if (meta.aihot_score !== undefined && meta.aihot_score !== null) facts.push(["AIHOT 分", meta.aihot_score]);
  if (meta.aihot_category_label) facts.push(["AIHOT 分类", meta.aihot_category_label]);
  if (meta.aihot_origin_source) facts.push(["AIHOT 原始来源", meta.aihot_origin_source]);
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
      `/api/items?source_id=arxiv_core&date=${encodeURIComponent(state.arxivDate)}&q=${q}&limit=${PAGE_SIZE}&offset=${offset}`
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
  const reloadDates = options.reloadDates !== false && !append;
  if (reloadDates || !state.radarDate) {
    await loadDateChoices({
      selectId: "radarDate",
      countId: "radarCount",
      dateKey: "radarDate",
      params: `source_type=${encodeURIComponent($("typeFilter").value || "")}`,
    });
  }
  if (!state.radarDate) {
    state.pages.radar = createPage();
    renderList("radarList", [], "radar", "暂无雷达数据。");
    updateMoreButton("radar", "radarMore");
    return;
  }
  const page = state.pages.radar;
  if (page.loading) return;
  if (!append) page.items = [];
  page.loading = true;
  updateMoreButton("radar", "radarMore");
  try {
    const type = $("typeFilter").value;
    const q = encodeURIComponent(state.search || "");
    const offset = append ? page.items.length : 0;
    const data = await api(
      `/api/items?date=${encodeURIComponent(state.radarDate)}&source_type=${encodeURIComponent(type)}&q=${q}&limit=${PAGE_SIZE}&offset=${offset}`
    );
    page.total = data.total;
    page.items = append ? page.items.concat(data.items) : data.items;
    $("radarCount").textContent = `显示 ${page.items.length} / 共 ${page.total} 条`;
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
      `/api/items?source_type=blog%2Ccn_community&date=${encodeURIComponent(state.blogDate)}&q=${q}&limit=${PAGE_SIZE}&offset=${offset}`
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
          <article class="compact-item item-card" data-id="${escapeHtml(item.id)}" data-type="${escapeHtml(item.source_type || "")}" tabindex="0">
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
  // Fit canvas to its container at native device resolution (fixes coordinate mismatch)
  const shell = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const logW = shell ? Math.max(shell.clientWidth || 0, 300) : 700;
  const logH = Math.round(logW * 0.50);
  canvas.style.height = logH + "px";
  canvas.width = Math.round(logW * dpr);
  canvas.height = Math.round(logH * dpr);
  state.graph.logW = logW;
  state.graph.logH = logH;
  state.graph.dpr = dpr;
  const nodes = (graph.nodes || []).map((node) => ({
    ...node,
    tags: visibleTags(node.tags),
    weight: Number(node.weight || 14),
    x: 0, y: 0, vx: 0, vy: 0,
  }));
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const edges = (graph.edges || [])
    .map((edge) => ({ ...edge, sourceNode: nodeMap.get(edge.source), targetNode: nodeMap.get(edge.target) }))
    .filter((edge) => edge.sourceNode && edge.targetNode);
  initGraphPositions(canvas, nodes);
  state.graph.nodes = nodes;
  state.graph.edges = edges;
  state.graph.activeNode = null;
  state.graph.tick = 0;
  state.graph.settled = false;
  $("knowledgeGraphMeta").textContent = `${nodes.length} 个节点 · ${edges.length} 条关系`;
  if (state.graph.animation) cancelAnimationFrame(state.graph.animation);
  state.graph.animation = null;
  bindGraphCanvas(canvas);
  startGraphAnimation(canvas);
}

function bindGraphCanvas(canvas) {
  if (canvas.dataset.bound === "true") return;
  canvas.dataset.bound = "true";
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    // Coordinates are in logical pixels (CSS px) which match node positions
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const next = state.graph.nodes.find((n) => Math.hypot(n.x - x, n.y - y) < graphRadius(n) + 8) || null;
    const changed = (state.graph.activeNode && state.graph.activeNode.id) !== (next && next.id);
    state.graph.activeNode = next;
    canvas.style.cursor = next ? "pointer" : "default";
    if (changed && state.graph.settled) {
      drawGraphEnhanced(canvas.getContext("2d"), canvas, state.graph.nodes, state.graph.edges);
    }
  });
  canvas.addEventListener("mouseleave", () => {
    state.graph.activeNode = null;
    canvas.style.cursor = "default";
    if (state.graph.settled) {
      drawGraphEnhanced(canvas.getContext("2d"), canvas, state.graph.nodes, state.graph.edges);
    }
  });
  canvas.addEventListener("click", () => {
    const node = state.graph.activeNode;
    if (!node) return;
    api(`/api/items/${node.id}?user_id=${encodeURIComponent(state.profile)}`)
      .then((detail) => selectItem({ ...node, ...detail }))
      .catch(showError);
  });
}

// ── Graph: set initial cluster positions (physics handles refinement) ──
function initGraphPositions(canvas, nodes) {
  const W = state.graph.logW || canvas.width;
  const H = state.graph.logH || canvas.height;
  const centers = {
    paper:        { x: W * 0.62, y: H * 0.42 },
    repo:         { x: W * 0.28, y: H * 0.62 },
    discussion:   { x: W * 0.70, y: H * 0.68 },
    blog:         { x: W * 0.36, y: H * 0.28 },
    cn_community: { x: W * 0.54, y: H * 0.76 },
    other:        { x: W * 0.50, y: H * 0.50 },
  };
  const groups = new Map();
  for (const node of nodes) {
    const g = graphGroup(node);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(node);
  }
  for (const [g, gNodes] of groups) {
    const c = centers[g] || centers.other;
    const spread = Math.min(Math.min(W, H) * 0.28, 60 + Math.sqrt(gNodes.length) * 34);
    gNodes.sort((a, b) => (b.weight || 0) - (a.weight || 0));
    gNodes.forEach((node, i) => {
      const j = hashValue(node.id) - 0.5;
      const angle = i * 2.399963 + j * 0.8;
      const r = gNodes.length <= 1 ? 0 : spread * Math.sqrt((i + 0.35) / gNodes.length);
      node.x = clampNumber(c.x + Math.cos(angle) * r + j * 18, 42, W - 42);
      node.y = clampNumber(c.y + Math.sin(angle) * r + j * 12, 42, H - 42);
      node.vx = (Math.random() - 0.5) * 2.0;
      node.vy = (Math.random() - 0.5) * 2.0;
    });
  }
}

// ── Graph: one frame of physics ──
function tickGraphPhysics(canvas, nodes, edges) {
  const W = state.graph.logW || canvas.width;
  const H = state.graph.logH || canvas.height;
  const cx = W * 0.5, cy = H * 0.5;
  // Repulsion
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      const dx = b.x - a.x || 0.01, dy = b.y - a.y || 0.01;
      const d2 = dx * dx + dy * dy, d = Math.sqrt(d2) || 0.1;
      const min = graphRadius(a) + graphRadius(b) + 22;
      const f = d < min ? (min - d) / d * 0.52 : 1100 / (d2 * d);
      a.vx -= dx * f; a.vy -= dy * f;
      b.vx += dx * f; b.vy += dy * f;
    }
  }
  // Spring attraction along edges
  for (const e of edges) {
    const a = e.sourceNode, b = e.targetNode;
    const dx = b.x - a.x || 0.01, dy = b.y - a.y || 0.01;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.1;
    const target = 88 + Math.max(0, 5 - Math.min(e.weight || 1, 5)) * 11;
    const f = (d - target) / d * 0.044;
    a.vx += dx * f; a.vy += dy * f;
    b.vx -= dx * f; b.vy -= dy * f;
  }
  // Center gravity + damping
  for (const n of nodes) {
    n.vx += (cx - n.x) * 0.0055;
    n.vy += (cy - n.y) * 0.0055;
    n.vx *= 0.85; n.vy *= 0.85;
    n.x = clampNumber(n.x + n.vx, 42, W - 42);
    n.y = clampNumber(n.y + n.vy, 42, H - 42);
  }
}

// ── Graph: animation loop ──
function startGraphAnimation(canvas) {
  const ctx = canvas.getContext("2d");
  function loop() {
    const { nodes, edges } = state.graph;
    if (!state.graph.settled) {
      tickGraphPhysics(canvas, nodes, edges);
      drawGraphEnhanced(ctx, canvas, nodes, edges);
      if (++state.graph.tick >= 320) {
        state.graph.settled = true;
        drawGraphEnhanced(ctx, canvas, nodes, edges);
        state.graph.animation = null;
        return;
      }
    }
    state.graph.animation = requestAnimationFrame(loop);
  }
  state.graph.animation = requestAnimationFrame(loop);
}

// ── Graph: enhanced renderer ──
function drawGraphEnhanced(ctx, canvas, nodes, edges) {
  const dpr = state.graph.dpr || 1;
  const W = state.graph.logW || canvas.width;
  const H = state.graph.logH || canvas.height;
  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); // HiDPI scale
  // Fill dark background
  ctx.fillStyle = "#0d1117";
  ctx.fillRect(0, 0, W, H);

  if (!nodes.length) {
    ctx.fillStyle = "rgba(148,163,184,.55)";
    ctx.font = "14px system-ui, -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("暂无图谱数据：先打开、收藏或追问一些条目。", W / 2, H / 2);
    ctx.restore();
    return;
  }

  const activeId = state.graph.activeNode && state.graph.activeNode.id;
  const nbSet = new Set();
  if (activeId) {
    nbSet.add(activeId);
    for (const e of edges) {
      if (e.sourceNode.id === activeId) nbSet.add(e.targetNode.id);
      if (e.targetNode.id === activeId) nbSet.add(e.sourceNode.id);
    }
  }

  // Draw edges — curved bezier
  ctx.save();
  ctx.lineCap = "round";
  for (const e of edges) {
    const a = e.sourceNode, b = e.targetNode;
    const isActive = activeId && (a.id === activeId || b.id === activeId);
    const dim = activeId && !nbSet.has(a.id) && !nbSet.has(b.id);
    const w = Math.min(e.weight || 1, 5);
    const alpha = dim ? 0.025 : isActive ? 0.65 : 0.055 + w * 0.04;
    const mx = (a.x + b.x) / 2 + (b.y - a.y) * 0.09;
    const my = (a.y + b.y) / 2 - (b.x - a.x) * 0.09;
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = isActive ? "#2dd4bf" : "#94a3b8";
    ctx.lineWidth = isActive ? Math.min(2.8, 1 + w * 0.28) : Math.min(1.6, 0.4 + w * 0.15);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.quadraticCurveTo(mx, my, b.x, b.y);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  ctx.restore();

  // Draw nodes — sorted so active is on top
  const sorted = [...nodes].sort((a, b) => {
    if (a.id === activeId) return 1;
    if (b.id === activeId) return -1;
    return (a.weight || 0) - (b.weight || 0);
  });
  for (const node of sorted) {
    const isActive = node.id === activeId;
    const isNb = !isActive && nbSet.has(node.id);
    const dim = activeId && !isActive && !isNb;
    const r = graphRadius(node);

    ctx.save();
    ctx.globalAlpha = dim ? 0.2 : 1;

    // Glow shadow
    if (isActive) {
      ctx.shadowColor = graphNodeGlow(node.source_type);
      ctx.shadowBlur = 22;
    } else if (isNb) {
      ctx.shadowColor = graphNodeGlow(node.source_type);
      ctx.shadowBlur = 10;
    }

    // Radial gradient fill
    const grd = ctx.createRadialGradient(node.x - r * 0.32, node.y - r * 0.36, r * 0.05, node.x, node.y, r * 1.12);
    grd.addColorStop(0,   graphNodeHi(node.source_type));
    grd.addColorStop(0.6, graphNodeMid(node.source_type));
    grd.addColorStop(1,   graphNodeLo(node.source_type));
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
    ctx.fillStyle = grd;
    ctx.fill();

    // White ring
    ctx.lineWidth  = isActive ? 2.5 : isNb ? 2 : 1.5;
    ctx.strokeStyle = isActive ? "rgba(255,255,255,.9)" : "rgba(255,255,255,.55)";
    ctx.shadowBlur = 0;
    ctx.stroke();

    // Outer accent ring for active
    if (isActive) {
      ctx.lineWidth = 4;
      ctx.strokeStyle = graphNodeGlow(node.source_type);
      ctx.globalAlpha = 0.35;
      ctx.beginPath();
      ctx.arc(node.x, node.y, r + 5, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.restore();

    // Label — pill background
    if (!isActive && r < 11) continue;
    const raw = node.label || "";
    const maxLen = isActive ? 22 : 16;
    const lbl = raw.length > maxLen ? raw.slice(0, maxLen - 1) + "…" : raw;
    const labelY = node.y - r - 7;

    ctx.save();
    ctx.globalAlpha = dim ? 0.2 : 1;
    const fs = isActive ? 12 : 11;
    ctx.font = `${isActive ? 600 : 400} ${fs}px system-ui, -apple-system, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    const tw = ctx.measureText(lbl).width;
    const px = 6, py = 3;
    const rx = node.x - tw / 2 - px;
    const ry = labelY - fs - py;
    const rw = tw + px * 2;
    const rh = fs + py * 2;
    // Pill bg
    ctx.fillStyle = isActive ? "rgba(13,20,32,.9)" : "rgba(255,255,255,.82)";
    ctx.shadowColor = "rgba(0,0,0,.18)";
    ctx.shadowBlur = 5;
    graphRoundRect(ctx, rx, ry, rw, rh, 5);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = isActive ? "#5eead4" : "#e2e8f0";
    ctx.fillText(lbl, node.x, labelY);
    ctx.restore();
  }

  ctx.restore();
}

function graphRoundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

function graphRadius(node) {
  return Math.max(8, Math.min(22, Number(node.weight || 14) / 1.7));
}

function graphGroup(node) {
  return ["paper", "repo", "discussion", "blog", "cn_community", "signal"].includes(node.source_type)
    ? node.source_type : "other";
}

function hashValue(value) {
  let hash = 0;
  const text = String(value || "");
  for (let i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  return (hash % 1000) / 1000;
}

// Node colour ramps (hi / mid / lo / glow) on dark canvas
function graphNodeHi(t) {
  if (t === "paper")        return "#7dd3fc";
  if (t === "repo")         return "#86efac";
  if (t === "discussion")   return "#fde68a";
  if (t === "blog")         return "#f9a8d4";
  if (t === "cn_community") return "#c4b5fd";
  if (t === "signal") return "#67e8f9";
  return "#cbd5e1";
}
function graphNodeMid(t) {
  if (t === "paper")        return "#0ea5e9";
  if (t === "repo")         return "#22c55e";
  if (t === "discussion")   return "#f59e0b";
  if (t === "blog")         return "#ec4899";
  if (t === "cn_community") return "#a78bfa";
  if (t === "signal") return "#06b6d4";
  return "#94a3b8";
}
function graphNodeLo(t) {
  if (t === "paper")        return "#0369a1";
  if (t === "repo")         return "#15803d";
  if (t === "discussion")   return "#92400e";
  if (t === "blog")         return "#9d174d";
  if (t === "cn_community") return "#5b21b6";
  if (t === "signal") return "#155e75";
  return "#334155";
}
function graphNodeGlow(t) {
  if (t === "paper")        return "rgba(14,165,233,.75)";
  if (t === "repo")         return "rgba(34,197,94,.75)";
  if (t === "discussion")   return "rgba(245,158,11,.75)";
  if (t === "blog")         return "rgba(236,72,153,.75)";
  if (t === "cn_community") return "rgba(167,139,250,.75)";
  if (t === "signal") return "rgba(6,182,212,.75)";
  return "rgba(148,163,184,.6)";
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
  $("radarDate").addEventListener("change", () => {
    state.radarDate = $("radarDate").value;
    loadRadar({ reloadDates: false }).catch(showError);
  });
  $("blogDate").addEventListener("change", () => {
    state.blogDate = $("blogDate").value;
    loadBlogs({ reloadDates: false }).catch(showError);
  });
  $("daysFilter").addEventListener("change", () => {
    state.radarDate = "";
    loadRadar().catch(showError);
  });
  $("typeFilter").addEventListener("change", () => {
    state.radarDate = "";
    loadRadar().catch(showError);
  });
  $("searchBtn").addEventListener("click", () => {
    state.search = $("searchInput").value.trim();
    state.arxivDate = "";
    state.radarDate = "";
    state.blogDate = "";
    refreshView().catch(showError);
  });
  $("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      state.search = $("searchInput").value.trim();
      state.arxivDate = "";
      state.radarDate = "";
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
