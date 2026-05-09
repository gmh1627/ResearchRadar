---
theme: default
title: ResearchRadar
info: |
  ResearchRadar introduction deck.
class: rr
drawings:
  persist: false
transition: fade-out
mdc: true
fonts:
  provider: none
  sans: ui-sans-serif, system-ui
  mono: ui-monospace, SFMono-Regular
---

<div class="rr-cover">
  <div>
    <p class="eyebrow">Personal AI Research Source Agent</p>
    <h1>ResearchRadar</h1>
    <p class="subtitle">把论文、实验室动态、代码项目、社区讨论和 AIHOT 精选线索汇入一个可解释、可追踪、可对话的研究雷达。</p>
  </div>
  <div class="rr-cover-card">
    <span>本地浏览器仪表盘</span>
    <strong>5622</strong>
    <small>已入库条目 · 21 个来源</small>
  </div>
</div>

---
layout: two-cols
layoutClass: gap-12
---

## 为什么需要它

AI 研究信息流现在有三个实际问题：

- 来源碎片化：论文、官方博客、GitHub、HN、X/KOL 线索分散。
- 噪声高：很多内容标题带 AI，但对 LLM / Agent / 研究决策没有价值。
- 决策不可追踪：看过什么、为什么推荐、后续是否要深读，容易丢。

::right::

## ResearchRadar 的定位

不是通用新闻聚合器，而是面向个人研究画像的信源 agent：

- 每日抓取并入库
- 用 GPT-5.5 做后处理判断
- 用代码公式做最终排序
- 保留证据链接和推荐理由
- 把阅读、收藏、问答、笔记沉淀为知识库

---

## 现在已经覆盖的信源

<div class="stat-grid">
  <div><strong>3453</strong><span>arXiv AI / ML / Agent 论文</span></div>
  <div><strong>1518</strong><span>Hacker News 工程讨论</span></div>
  <div><strong>280</strong><span>公司与实验室博客</span></div>
  <div><strong>258</strong><span>GitHub 趋势项目</span></div>
  <div><strong>64</strong><span>AIHOT 精选外部线索</span></div>
  <div><strong>49</strong><span>中文社区内容</span></div>
</div>

<div class="source-strip">
  <span>OpenAI</span><span>Anthropic</span><span>DeepMind</span><span>Meta AI</span><span>Google Research</span><span>Hugging Face</span><span>Qwen</span><span>AIHOT</span>
</div>

---
layout: image-right
image: /dashboard.png
---

## 核心界面：个性日报

日报不是简单按时间排序，而是按研究画像和信源质量重排。

- 分区展示：重点论文、官方动态、代码工具、社区讨论、外部精选线索
- 每条都有质量分、标签、摘要、推荐理由
- 右侧证据卡用于查看来源、PDF、HN、AIHOT 页等
- 支持搜索、筛选、分页和反馈

---

## 数据处理流水线

<div class="pipeline">
  <div><b>1</b><span>抓取</span><small>RSS / Page / arXiv / GitHub / HN / AIHOT API</small></div>
  <div><b>2</b><span>规范化</span><small>统一 item schema、去重、时间语义、证据角色</small></div>
  <div><b>3</b><span>GPT-5.5 预筛</span><small>过滤非 AI / 低相关内容</small></div>
  <div><b>4</b><span>GPT-5.5 分析</span><small>中文摘要、标签、五维度、推荐理由</small></div>
  <div><b>5</b><span>代码排序</span><small>权威度、趋势、时效、用户反馈合成最终分</small></div>
</div>

<p class="note">设计原则：大模型负责语义判断，ResearchRadar 负责可控排序和可解释输出。</p>

---

## 为什么加入 AIHOT

ResearchRadar 目前不能稳定直接抓取 X.com。AIHOT 刚好能补上早期信号层：

- X / KOL / 产品发布 / 行业动态的二级精选入口
- 低频抓取即可：每日早上一次，避免重复请求
- 作为 `signal` 类型入库，不替代原始来源
- 保留 AIHOT 分类、原始来源、外部链接、精选状态

```yaml
id: aihot_public
type: aihot
api_url: https://aihot.virxact.com/api/public/items
api_mode: selected
api_take: 80
```

---

## GPT-5.5 的使用方式

ResearchRadar 现在更接近 AIHOT 的思路：

<div class="llm-grid">
  <div>
    <h3>模型做什么</h3>
    <p>相关性预筛、中文摘要、标签、五维度评分、推荐理由、下一步建议。</p>
  </div>
  <div>
    <h3>模型不做什么</h3>
    <p>不直接决定最终质量分；不替代信源权威、时间、趋势和用户反馈。</p>
  </div>
  <div>
    <h3>固定模型</h3>
    <p>后处理强制使用 <code>gpt-5.5</code>，不会被通用聊天模型配置意外覆盖。</p>
  </div>
  <div>
    <h3>运行时机</h3>
    <p>爬取完成后批量执行；列表和日报读取不再即时调用大模型。</p>
  </div>
</div>

---

## 排序逻辑：可解释而不是黑箱

最终质量分由代码计算：

<div class="formula">
score = 相关性 + 重要性 + 新颖性 + 可行动性 + 可信度 + 趋势 + 时效 + 个性化反馈
</div>

每个条目会保留：

- `score_parts`：每个维度的贡献
- `relevance_reason`：为什么推荐或为什么忽略
- `recommended_action`：下一步怎么处理
- `evidence_links`：原始链接、PDF、HN、AIHOT 页等

---
layout: image-right
image: /knowledge.png
---

## 知识库：把阅读变成长期资产

ResearchRadar 不只做“今日列表”，还记录研究行为。

- 收藏、深读、问答、笔记统一沉淀
- 研究画像展示主兴趣、次兴趣、排除项、偏好来源
- 行为标签反向影响个性化排序
- 图谱视图连接条目、反馈、问答和笔记

---
layout: image-right
image: /sources.png
---

## 运维视角：信源状态透明

每个来源都有运行记录：

- `success` / `partial` / `skipped` / `error` / `interrupted`
- 每日爬取会记录每个 source run
- 启动不会自动补爬，保证仪表盘先可用
- 手动抓取和每日定时任务会触发 GPT-5.5 后处理

<p class="note">截图中的错误来自当日网络连接失败；系统仍能清楚标出失败来源，不会静默污染日报。</p>

---

## 本地优先的工程形态

<div class="architecture">
  <div>
    <h3>FastAPI</h3>
    <p>本地 Web 服务和 JSON API。</p>
  </div>
  <div>
    <h3>SQLite</h3>
    <p>单文件数据库，方便备份和迁移。</p>
  </div>
  <div>
    <h3>配置驱动</h3>
    <p>信源、画像、抓取频率、LLM 参数都在 YAML 中管理。</p>
  </div>
  <div>
    <h3>后台任务</h3>
    <p>tmux / autostart 支持，SSH 断开后继续运行。</p>
  </div>
</div>

---

## 当前能力边界

需要明确的限制：

- AIHOT 是二级线索，最终仍要打开原始来源核验。
- X.com 仍未直接抓取，AIHOT 只是补足一部分高价值信号。
- GPT-5.5 后处理按批执行，有成本和延迟，需要控制 `limit`、`days`。
- 网络不可用时爬取会失败，但会记录为 source status，不会伪造新内容。

---

## 下一步可以增强什么

<div class="roadmap">
  <div><b>信源质量</b><span>为 AIHOT / HN / GitHub 加更细的可信度和重复线索合并。</span></div>
  <div><b>研究工作流</b><span>把深读队列、论文笔记、实验计划和引用管理串起来。</span></div>
  <div><b>评估闭环</b><span>定期复盘推荐是否真的被保存、深读、引用或转化为实验。</span></div>
  <div><b>展示导出</b><span>日报导出 Markdown / PDF，周报自动生成。</span></div>
</div>

---

<div class="ending">
  <h1>ResearchRadar = 信源雷达 + 研究画像 + GPT-5.5 后处理 + 可解释排序</h1>
  <p>目标不是“看到更多”，而是更快定位值得深读、值得实验、值得长期跟踪的信息。</p>
  <code>http://127.0.0.1:8765</code>
</div>
