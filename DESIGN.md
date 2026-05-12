# ResearchRadar 设计文档

版本：v0.1  
日期：2026-04-29

## 1. 一句话定位

ResearchRadar 是一个面向 AI 研究者的个人研究信息源 Agent。它每天自动读取核心 AI / ML / Agent 方向的新论文、代码项目、技术博客、社区讨论和中文技术动态，根据用户研究画像生成个性化研究日报；用户可以对任意条目继续追问大模型，系统再把有价值的交互蒸馏为长期 research note，持续更新个人知识库和兴趣画像。

它不是普通 RSS 阅读器，也不是论文摘要脚本，而是一个持续维护个人研究视野的 Agent：

- 保证每天看到核心 AI / ML / Agent 新论文的全量标题摘要。
- 从多源信息池里筛出真正值得用户今天读的内容。
- 对每条推荐给出为什么相关、证据来源和建议动作。
- 支持围绕论文、repo、讨论继续追问。
- 把收藏、反馈和高价值问答沉淀进个人知识库。
- 支持多位同学配置不同研究方向，生成不同日报。

## 2. 背景与设计约束

来自 `agent_design.txt` 的关键结论如下：

- 课程项目不应该只做报告、评测或离线脚本，而应该做一个 3 周内可运行、有人愿意用、有技术深度的 Agent 产品。
- 系统应先做稳定闭环，再逐步引入复杂 Agent 能力。也就是 workflow-first, agent-enhanced，而不是一开始堆多 Agent。
- 你的个人需求是：AI 博士研究生，主线关注大模型、智能体、Agent systems，同时希望每天跟踪国内外 AI 研究进展。
- 后续需求已经收窄：不扫全部泛 AI 外围领域，第一版聚焦核心 AI / ML / Agent。
- 系统必须支持其他同学使用，因此研究方向、负面主题、信息源偏好和推送风格都要可配置。
- 大模型交互层是核心创新：用户不只是接收日报，还能围绕信息追问、理解、判断，并将交互结果沉淀为知识。

## 3. 借鉴对象与 ResearchRadar 的取舍

| 借鉴对象 | 可借鉴思想 | ResearchRadar 的做法 |
| --- | --- | --- |
| Feedly AI / AI Feeds | 从大量来源中筛出符合主题的信息，而不是展示全部 feed | 做个性化过滤与排序，但研究画像比关键词更丰富 |
| Folo + RSSHub | 用开放 RSS 生态接入多种来源 | 把 RSS/RSSHub 作为稳定信息源接入层之一 |
| Readwise Reader / Ghostreader | 阅读、标注、AI 助手和知识沉淀结合 | 做 item-level chat、收藏、research note 和知识库检索 |
| ResearchRabbit / Semantic Scholar | 基于 seed paper、引用关系和相似论文扩展研究地图 | v1.5 引入论文推荐和 related work 扩展 |
| Hugging Face Daily / Trending Papers | 社区热度可以作为发现信号 | 作为趋势信号，不直接等价于质量 |
| Hacker News | 工程社区讨论和真实使用反馈 | v1 接入，作为工程可用性与讨论信号 |
| X.com | 新模型、论文、repo 的早期信号 | v1.5 可选，仅用官方 API/白名单 watchlist，不做无边界爬取 |
| Claude Code Dream / memory consolidation | 长期记忆需要定期去重、纠错、压缩 | 每周做 Research Memory Consolidation，更新 profile 和 research notes |

ResearchRadar 的创新点不是发明一个全新的检索系统，而是把多源采集、个性化画像、证据化摘要、交互式理解、知识沉淀和反馈学习做成一个闭环。

## 4. 产品目标与非目标

### 4.1 目标

1. 每日自动生成 Core AI / ML / Agent arXiv Radar。
2. 每日为每个用户生成 8-12 条个性化精选研究情报。
3. 每条推荐必须包含相关理由、关键信息、证据来源、建议动作。
4. 用户可以对单条 item、当天 digest、个人知识库进行追问。
5. 系统把有价值的问答蒸馏为 research note，而不是无脑保存整段聊天。
6. 用户反馈会影响后续推荐：收藏、深读、忽略、不相关、点赞。
7. 支持多用户 profile，同一信息池输出不同个性化日报。
8. 保留运行日志和推荐依据，方便作业展示和失败分析。

### 4.2 非目标

1. v1 不做全网爬虫。
2. v1 不做大规模 X/微信/知乎抓取。
3. v1 不覆盖全部 AI 外围领域，例如 CV、Robotics、Audio、HCI、安全等，除非明显命中 LLM/Agent/RAG/Reasoning/Tool Use 等关键词。
4. 系统不替代深度论文阅读，只做发现、优先级排序、初读辅助和知识沉淀。
5. 系统不训练或微调模型，只调用 LLM API 完成结构化判断、摘要、问答和蒸馏。

## 5. 用户画像

### 5.1 默认用户：AI 博士研究生

- 角色：人工智能专业博士研究生。
- 主方向：大模型、智能体、Agent systems、future prediction、tool-use agent、agent memory。
- 次方向：LLM reasoning、RAG、evaluation、alignment、open-source LLM、AI infra。
- 信息需求：每天快速知道核心 AI / ML / Agent 方向有什么新论文、新 repo、新讨论、新趋势。
- 输出偏好：中文日报，技术深度高，包含论文链接、代码链接、建议动作。

### 5.2 其他同学

系统不能写死你的兴趣。每位同学拥有独立 User Research Profile：

- primary topics：主研究方向。
- secondary topics：次关注方向。
- negative topics：不想看的主题。
- seed papers/authors/repos：种子论文、作者、项目。
- preferred sources：偏好信息源。
- digest style：语言、长度、技术深度、是否包含代码。
- push settings：推送渠道、时间、时区。

例子：

| 用户 | 主方向 | 日报差异 |
| --- | --- | --- |
| 你 | LLM + Agent + future prediction | Agent memory、tool use、reasoning、evaluation 权重更高 |
| CV 同学 | diffusion + video generation + 3D generation | 只在另开 profile 时纳入 CV 类目和视频生成源 |
| Robotics 同学 | embodied AI + robot learning | 只在该 profile 中打开 cs.RO 与机器人信息源 |

## 6. 信息源范围

### 6.1 arXiv 核心范围

v1 默认全量追踪：

- `cs.AI` Artificial Intelligence
- `cs.LG` Machine Learning
- `stat.ML` Machine Learning
- `cs.CL` Computation and Language / NLP
- `cs.MA` Multiagent Systems

条件追踪：

- `cs.IR` Information Retrieval：只在命中 LLM、agentic search、web agent 等关键词时纳入。
- `cs.NE` Neural and Evolutionary Computing：只在命中 self-improvement、open-ended learning、neuro-symbolic、evolution 等关键词时纳入。

暂不默认追踪：

- `cs.CV`, `cs.RO`, `cs.MM`, `cs.SD`, `eess.AS`, `eess.IV`, `cs.SE`, `cs.HC`, `cs.CY`, `cs.CR`

设计原则：每天全量扫核心 AI / ML / Agent，不扫泛 AI 外围领域；外围领域只有明显关联 LLM、Agent、RAG、Reasoning、Tool Use、Self-Improvement 时才纳入。

### 6.2 其他信息源优先级

v1 必做：

- arXiv core categories
- GitHub keyword/trending repos
- Hugging Face Papers
- Hacker News
- 官方博客 RSS / 技术博客 RSS

v1.5 选做：

- OpenReview
- Semantic Scholar
- Papers with Code
- alphaXiv
- X Watchlist，基于官方 API、白名单账号/列表/关键词

v2 再做：

- 机器之心、量子位、新智元、InfoQ 中文、智源社区、掘金、少数派、V2EX
- 微信公众号 watchlist，通过 WeWe RSS / we-mp-rss / RSSHub / 手动导入兜底
- 知乎、B站等高噪声源，仅作为弱信号

### 6.3 证据角色分层

不同来源不应拥有同等可信度：

| 来源类型 | 系统角色 | 可信度处理 |
| --- | --- | --- |
| arXiv / OpenReview | 正式技术贡献 | 可作为主要证据，但仍需注意未同行评审 |
| Semantic Scholar | 学术图谱与相关论文 | 用于扩展，不直接替代原文 |
| GitHub | 可复现性与工程活跃度 | 看 README、commit、issue、release，不只看 star |
| HN / V2EX | 工程社区反馈 | 作为讨论和质疑信号 |
| X / 知乎 | 早期信号和观点 | 标记为 weak / needs verification |
| 公司博客 | 官方解释和产品动态 | 适合跟踪 release，但注意营销语言 |
| 中文媒体 | 国内传播和产业动态 | 作为中文摘要与趋势补充 |

## 7. 核心产品形态

### 7.1 每日推送

每天早上系统推送一份简短日报：

- 今日总览：论文数、主题分布、趋势摘要。
- 今日必读：5-8 条。
- Agent / LLM 专区：agent architecture、tool use、memory、reasoning、planning、evaluation。
- ML / RL / 方法专区：training、optimization、RL、generalization、uncertainty。
- NLP / LLM 应用专区：instruction following、alignment、long context、data synthesis。
- GitHub / HN / Blog 早期信号：2-5 条。
- 全量页面链接：进入 Web 查看所有 Core AI / ML / Agent arXiv 标题摘要。

推送正文不塞入全部摘要，只放总览、精选和链接。全量内容进入 Web radar 页面。

### 7.2 Web Dashboard

Web 端包含 5 个核心页面：

1. Today Radar：当天全量 arXiv 标题摘要、主题统计、筛选与搜索。
2. Personal Digest：个性化精选卡片和推荐理由。
3. Item Detail：原文摘要、证据、相关链接、相关论文/repo/讨论。
4. Research Chat：围绕 item/digest/知识库追问大模型。
5. Knowledge Base：收藏、research notes、历史问答摘要、用户画像记忆。

### 7.3 Evidence Card

每条推荐生成结构化 Evidence Card：

- 标题
- 类型：paper / repo / blog / HN discussion / X signal / 中文文章
- 一句话结论
- 为什么和该用户相关
- 关键贡献或关键信息
- 新颖性判断
- 可信度与证据来源
- 建议动作：略读、深读、收藏、复现、等后续版本、忽略
- 链接：paper、PDF、code、blog、discussion
- 标签：Agent、Tool Use、Memory、RAG、Evaluation 等

## 8. 总体架构

```text
User Profiles
    |
    v
Scheduler / Daily Orchestrator
    |
    v
Source Connectors
    |-- arXiv
    |-- GitHub
    |-- Hugging Face Papers
    |-- Hacker News
    |-- RSS / Blogs
    |-- optional X / OpenReview / Semantic Scholar
    |
    v
Normalizer + Raw Store
    |
    v
Deduplicator + Clusterer
    |
    v
Tagger + Area Classifier
    |
    v
Shared Candidate Pool
    |
    +--------------------------+
    |                          |
    v                          v
Full arXiv Radar          Personal Ranker
                               |
                               v
                         LLM Precision Filter
                               |
                               v
                         Daily Digest Agent
                               |
                               v
                         Push / Web Dashboard
                               |
                               v
                  Feedback + Interactive Research Copilot
                               |
                               v
                  Conversation Distiller + Knowledge Base
                               |
                               v
                     Weekly Memory Consolidator
```

架构上采取共享采集、个性化处理：

- 全局采集一次，形成 shared candidate pool。
- 对每个用户单独计算 relevance、novelty、ranking、digest。
- 公共知识库保存全量 item，个人知识库保存收藏、反馈、notes、profile memory。
- v1 实现中，服务端启动调度器前会获取本地 `data/scheduler.lock`，避免多个 Web 实例重复执行 catch-up 和每日抓取。
- 日报缓存必须带上 effective profile version。用户接受 profile memory 后，新的 digest 会绕开旧缓存重新排序。

## 9. Agent 与模块设计

### 9.1 设计原则

v1 不做复杂多 Agent 编排。主流程是稳定 workflow，只在需要判断、解释、生成、蒸馏的节点引入 LLM/Agent。

Agent 感体现在：

- 自主决定今天各 profile 应该扩展哪些 query。
- 自主判断 item 对某个用户是否相关、新颖、可行动。
- 自主解释推荐理由。
- 自主从用户反馈和对话中提出 profile update candidate。
- 每周自主整理长期记忆，去重、纠错、降噪。

### 9.2 Daily Orchestrator

职责：

- 读取所有用户 profile。
- 确定本次运行窗口，例如前一个 UTC 日或 36 小时窗口。
- 调用各 connector 拉取候选信息。
- 触发标准化、去重、打标签、排序、摘要、推送。
- 写入 run trace，保证失败后可重跑。

输入：

- date window
- source config
- user profiles

输出：

- full radar
- personalized digests
- run trace

### 9.3 Arxiv Core Radar Agent

职责：

- 抓取 `cs.AI/cs.LG/stat.ML/cs.CL/cs.MA` 当日新论文。
- 对 `cs.IR/cs.NE` 执行条件抓取。
- 按 arXiv id 去重，处理 cross-list。
- 生成全量标题摘要页面。
- 打系统标签：LLM、Agent、Tool Use、Reasoning、Memory、RAG 等。

输出：

- `full_arxiv_core_YYYYMMDD`
- arXiv category counts
- topic counts
- 每篇论文的 metadata、abstract、tags、relevance baseline

### 9.4 Personal Ranker

职责：

- 根据用户画像对 shared candidate pool 打分。
- 结合相关性、新颖性、来源权威、趋势信号、可行动性、多样性。
- 过滤 negative topics。
- 输出 top candidates 给 LLM 精筛。

推荐评分：

```text
FinalScore =
  0.35 * RelevanceToProfile
+ 0.20 * NoveltyToUserMemory
+ 0.15 * SourceAuthority
+ 0.15 * TrendSignal
+ 0.10 * Actionability
+ 0.05 * Diversity
- 0.25 * NegativeTopicPenalty
```

权重 v1 可以写死，v2 根据用户反馈微调。

### 9.5 LLM Precision Filter

职责：

- 对 top 30-50 候选 item 进行结构化判断。
- 判断是否值得推送给该用户。
- 输出相关理由、新颖性、重要性、建议动作、标签、风险。

注意：这个节点不是泛泛摘要，而是回答“这条信息是否值得这个用户今天看”。

### 9.6 Daily Digest Agent

职责：

- 把筛选后的 item 组织成日报。
- 控制长度和栏目。
- 为每条 item 生成 Evidence Card。
- 标注不确定性和弱证据。

输出：

- Telegram / Email 摘要版
- Web 完整版
- digest metadata

### 9.7 Interactive Research Copilot

提供三种对话入口：

1. Item Chat：当前条目 + 原文/摘要 + 相关资料 + 用户画像。
2. Digest Chat：当天 digest + 当天候选池 + 用户画像。
3. Knowledge Base Chat：个人知识库 + research notes + 历史收藏 + 用户画像。

典型问题：

- 这篇论文的核心贡献是什么？
- 和 Voyager / Reflexion / MemGPT 有什么关系？
- 对我的 future prediction agent 是否有帮助？
- 如果要复现，最小实验怎么做？
- 今天哪些内容只是 hype，可以跳过？
- 最近两周 agent memory 有什么新进展？

### 9.8 Conversation Distiller

职责：

- 不把完整聊天直接写进长期知识库。
- 从对话中抽取稳定、有用、可复用的 research note。
- 生成 profile update candidate。
- v1 中保存问答时会生成结构化蒸馏笔记：问题、结论摘要、相关来源、上下文摘录和 conversation id。后续可替换为 LLM distiller，但长期知识库不再保存裸回答。
- profile update candidate 不在知识库 GET 页面自动生成，而由显式用户操作、反馈事件或周期 consolidation 触发，避免读接口产生写入副作用。

蒸馏输出：

- research note title
- insight
- source item / conversation
- potential use
- tags
- confidence
- suggested profile update

### 9.9 Weekly Memory Consolidator

借鉴 Claude Code Dream / memory consolidation 思路，每周执行一次：

- 汇总本周收藏、深读、忽略、不相关反馈和高价值问答。
- 合并重复兴趣。
- 降权短期热点。
- 发现 profile 中矛盾或过时的主题。
- 生成 profile update candidates。
- 把稳定兴趣写入 profile memory。

目标不是“记得更多”，而是让长期推荐更稳定、更少噪声。

## 10. 数据模型

### 10.1 UserProfile

核心字段：

- user_id
- role / major
- primary_topics
- secondary_topics
- negative_topics
- seed_papers
- seed_authors
- seed_repos
- preferred_sources
- digest_language
- technical_depth
- include_code_links
- include_action_suggestions
- push_channel
- push_time
- timezone

### 10.2 InformationItem

核心字段：

- item_id
- source
- source_type：paper / repo / blog / news / discussion / social / newsletter
- title
- url
- authors
- published_at
- abstract_or_summary
- raw_text_pointer
- categories
- topics
- entities
- paper_url
- code_url
- discussion_url
- source_reliability
- evidence_role
- metadata

### 10.3 UserItemScore

核心字段：

- user_id
- item_id
- relevance
- novelty
- authority
- trend
- actionability
- diversity
- negative_penalty
- final_score
- llm_relevance_reason
- recommended_action

### 10.4 Digest

核心字段：

- digest_id
- user_id
- date
- summary
- sections
- item_ids
- push_status
- created_at

### 10.5 Feedback

核心字段：

- user_id
- item_id
- digest_id
- action：like / dislike / save / deep_read / ignore / not_relevant
- note
- created_at

### 10.6 Conversation 与 ResearchNote

Conversation 保存原始问答，用于追溯；ResearchNote 保存蒸馏后的长期知识。

ResearchNote 字段：

- note_id
- user_id
- title
- content
- source_type：item / conversation / manual
- source_id
- tags
- importance
- confidence
- created_at

### 10.7 ProfileUpdateCandidate

核心字段：

- candidate_id
- user_id
- topic
- update_type：increase_weight / decrease_weight / add_negative / add_watchlist / add_seed_paper
- reason
- evidence_id
- confidence
- status：pending / accepted / rejected / auto_applied
- created_at

### 10.8 ProfileMemory 与 Digest Cache

核心字段：

- user_id
- memory_key：interest / negative / preferred_source / deprioritized_source
- memory_value
- weight
- source_candidate_id
- created_at

运行时使用 base profile + accepted profile memory 形成 effective profile。digest_runs 记录 profile_version，profile memory 变化后不会继续复用旧日报缓存。

## 11. 知识库设计

知识库分三层，不把所有内容直接塞进向量库。

### 11.1 Raw Store

保存原始资料和元数据：

- title
- url
- source
- abstract
- raw text pointer
- crawl time
- license / usage note

### 11.2 Semantic Index

用于搜索、推荐和 RAG：

- v1 已实现 SQLite FTS5 `knowledge_fts`，覆盖 viewed/saved/liked items、notes、conversations、wiki_pages，并保留 SQL LIKE 回退。
- embeddings
- topics
- entities
- generated summary
- canonical item cluster
- related items

### 11.3 Research Memory

保存真正与用户长期相关的内容：

- saved papers
- deep-read items
- user notes
- distilled research notes
- feedback history
- profile memory
- author / repo / venue watchlist

Raw Store 是资料库，Semantic Index 是检索层，Research Memory 是个性化长期记忆。

## 12. 关键流程

### 12.1 每日运行流程

1. Scheduler 在用户设定时间前运行。
2. Daily Orchestrator 读取所有 profile。
3. Arxiv Core Radar Agent 抓取核心 arXiv 论文。
4. 其他 connectors 抓取 GitHub、HN、HF Papers、RSS。
5. Normalizer 统一为 InformationItem。
6. Deduplicator 按 URL、arXiv id、normalized title、embedding similarity 去重。
7. Tagger 生成主题标签和来源角色。
8. 写入 shared candidate pool。
9. 为每个用户执行 Personal Ranker。
10. LLM Precision Filter 对 top candidates 精筛。
11. Daily Digest Agent 生成日报。
12. 推送到 Telegram / Email / Web。
13. 用户反馈和点击行为写入 Feedback。
14. 收藏和高价值交互进入 Knowledge Base。

### 12.2 Item Chat 流程

1. 用户打开某个 item 并提问。
2. Intent Router 判断问题类型：explain / compare / related_work / implementation / idea_generation / skepticism / summarize_comments。
3. Context Builder 读取当前 item、相关 item、用户画像、历史收藏、相关 research notes。
4. LLM 生成回答，并附上证据。
5. Conversation Distiller 判断是否需要生成 research note。
6. 如果对话体现出新兴趣，生成 profile update candidate。

### 12.3 Feedback-to-Profile 流程

1. 用户点击 like/save/deep_read/ignore/not_relevant。
2. 系统更新 item-level feedback。
3. 统计近期主题、作者、来源偏好变化。
4. 低风险变化自动进入临时权重。
5. 高影响变化生成候选，等待用户确认或每周 consolidation 处理。

### 12.4 Weekly Consolidation 流程

1. 读取过去一周反馈、收藏、深读、对话摘要。
2. 找出稳定兴趣、短期热点、重复偏好、负面偏好。
3. 生成 profile memory diff。
4. 更新 research profile 或等待用户确认。
5. 输出一份 weekly learning report：系统本周学到了什么、调低了什么、建议关注什么。

## 13. 去重、聚类与标签

### 13.1 去重策略

按优先级处理：

1. arXiv id 精确匹配。
2. URL canonicalization。
3. normalized title 匹配。
4. DOI / OpenReview id / GitHub repo id 匹配。
5. embedding similarity 聚类。
6. LLM 判断是否为同一工作的论文、repo、blog、讨论。

同一工作可以形成 item cluster：

- paper
- code repo
- author blog
- HN discussion
- X signal
- 中文解读

日报中优先展示 cluster，而不是重复展示多个链接。

### 13.2 标签体系

默认标签：

- LLM
- Agent
- Multi-Agent
- Tool Use
- Reasoning
- Planning
- Memory
- RAG
- Evaluation
- Benchmark
- Alignment
- RL for LLM
- Code Agent
- Long Context
- Data Synthesis
- Model Training
- Inference / Serving
- Self-Improvement
- Future Prediction
- Simulation

标签来源：

- arXiv category
- keyword rules
- embedding similarity
- LLM classifier
- 用户自定义标签

## 14. 推荐与摘要原则

### 14.1 推荐原则

系统不追求“最热”，而追求“对这个用户最有信息增量”。

优先推荐：

- 与 primary topics 强相关。
- 对用户已有知识有新颖性。
- 有论文、代码、数据、benchmark 或高质量讨论。
- 来源可信或可交叉验证。
- 能产生具体行动：读 method、跑代码、加入 related work、形成 project idea。

降权：

- 与 negative topics 接近。
- 与近期已推送内容高度重复。
- 只有营销话术、没有技术细节。
- X/知乎等弱信号且无法找到论文/repo/blog 佐证。
- 只有热度但和用户方向无关。

### 14.2 摘要原则

摘要不是“把 abstract 翻译成中文”，而是结构化回答：

- 这是什么？
- 为什么重要？
- 为什么和该用户相关？
- 它相对已有工作的新意是什么？
- 证据来自哪里？
- 用户今天应该做什么？

对于弱证据来源，必须显式标注不确定性。

## 15. 交互式研究问答

Interactive Research Copilot 是 ResearchRadar 的核心区别点。

### 15.1 上下文范围

| 对话入口 | 上下文 | 适合问题 |
| --- | --- | --- |
| Item Chat | 当前 item + 相关 item + 用户画像 | 解释、对比、复现建议、claim 检查 |
| Digest Chat | 今日 digest + 当天候选池 | 今天读什么、哪些能跳过、有哪些趋势 |
| KB Chat | 个人知识库 + notes + 收藏 + profile | 两周进展、阅读路线、project idea |

### 15.2 回答要求

- 优先基于当前 item、原文摘要、链接、相关资料和用户知识库。
- 不知道就说不确定，并建议下一步验证。
- 给出证据引用，不把 X/HN 评论当作论文结论。
- 回答结束后判断是否值得蒸馏为 research note。

### 15.3 Conversation-to-Knowledge

完整聊天只作为 raw log；长期知识只保存蒸馏结果。

一次有价值问答应沉淀为：

- research insight
- related source
- potential use
- relevant tags
- confidence
- profile update candidate

这样能避免聊天记录污染长期知识库。

## 16. 多用户支持

ResearchRadar 采用 shared pool + personalized view：

- 每天统一抓取信息源。
- 全局去重、聚类、打标签。
- 每个用户单独 ranking、摘要、推送。
- 公共 item 与个人 note 分离。
- 用户反馈只更新自己的 profile，不影响其他用户，除非管理员选择共享某些 seed/source。

好处：

- 不重复抓取 arXiv/GitHub/RSS。
- 同一天同一批候选内容可以展示不同个性化结果。
- 作业 demo 很直观：三个 profile 输出三份不同日报。

## 17. 安全、合规与可靠性

### 17.1 数据源合规

- arXiv、HN、GitHub、RSS 优先使用官方 API 或公开 feed。
- X 只做官方 API watchlist，不做浏览器自动化或绕限制抓取。
- 微信公众号不作为核心源，使用 watchlist/RSS/手动导入兜底，抓不到不阻塞日报。
- 对可能有再分发限制的来源，长期保存 URL、metadata、短摘和系统笔记，避免镜像大规模全文。

### 17.2 Prompt Injection 防护

网页、README、评论、RSS 正文都视为不可信内容：

- Source content 只能作为被分析文本，不能改变系统指令。
- LLM 输出结构化字段时必须经过 schema 校验。
- 任何外部内容不能触发工具调用、删除、发信、修改 profile。
- 高影响 profile 更新需要候选状态和用户确认。

### 17.3 可靠性

- 每次运行有 run_id 和 trace。
- connector 失败不阻塞整个日报，只标记 source unavailable。
- 使用时间窗口 + 去重保证任务可重跑。
- arXiv 使用 UTC 日期窗口，建议抓前一 UTC 日或 36 小时窗口后按 arXiv id 去重。
- 成本控制：先规则/embedding 粗筛，再对 top candidates 调 LLM。

## 18. 技术栈建议

v1 推荐简单、可交付：

- 后端：Python + FastAPI
- 调度：APScheduler / cron，后续可换 Celery Beat
- 数据库：SQLite 起步，展示版可迁移 PostgreSQL
- 向量检索：Chroma / pgvector
- LLM：任意支持结构化输出和 tool/function calling 的 API
- 前端：简单 Web UI，或 FastAPI templates + 少量交互
- 推送：Telegram Bot / Email
- 配置：YAML user profiles

不建议 v1 一开始上复杂 LangGraph。可以先用显式 workflow 跑通；如果需要展示 Agent Systems 能力，再把 Daily Orchestrator、Memory Consolidator 等节点迁移到 LangGraph 或类似状态机。

## 19. MVP 范围

### 19.1 必须完成

1. 多用户 UserProfile 配置。
2. arXiv core categories 每日抓取。
3. GitHub + HN + RSS 至少各一个 connector。
4. InformationItem 标准化。
5. 去重与基础标签。
6. 个性化 ranking。
7. LLM 生成 Evidence Card。
8. Web 查看 full radar 和 personal digest。
9. 用户反馈：like/save/ignore/not relevant。
10. item-level chat。
11. 对话蒸馏为 research note。
12. 基础知识库搜索。

### 19.2 可以延期

- X Watchlist。
- 微信公众号 watchlist。
- OpenReview / Semantic Scholar 深度推荐。
- 每周 memory consolidation 自动更新 profile。
- 复杂 trace viewer。
- 多 Agent 可视化编排。

## 20. 三周实施计划

### Week 1：最小闭环

目标：每天能抓、能排、能生成一份你能看的日报。

- Day 1：确定 UserProfile、InformationItem、Digest、Feedback 数据结构。
- Day 2：实现 arXiv core connector 和 UTC 日期窗口。
- Day 3：实现 GitHub/HN/RSS connector 的最简版本。
- Day 4：标准化、去重、基础标签。
- Day 5：embedding/规则相关性排序。
- Day 6：LLM Evidence Card 和 personal digest。
- Day 7：Web 页面或 Telegram 推送，跑你的默认 profile。

### Week 2：交互与知识库

目标：从“日报系统”升级为“交互式研究知识库”。

- 支持多用户 profile。
- 支持收藏、忽略、不相关反馈。
- 支持 item-level chat。
- 保存 conversation raw log。
- 蒸馏 research note。
- 知识库搜索。
- 展示同一天不同用户不同 digest。

### Week 3：Agent 感、评测与展示

目标：让作业展示能清楚说明为什么这是 Agent，而不是爬虫 + 摘要器。

- 加入 query expansion：根据 profile/反馈调整次日 query。
- 加入 profile update candidate。
- 加入 weekly memory consolidation 的最简报告。
- 做 baseline 对比。
- 做失败案例分析。
- 完成 README、项目报告和 demo 脚本。

## 21. 评测设计

### 21.1 Baseline

- Baseline 1：关键词 RSS。
- Baseline 2：arXiv search + LLM summary。
- Baseline 3：Hugging Face Daily Papers 原始榜单。
- Baseline 4：直接问 LLM 今天有什么新进展。
- Ours：ResearchRadar personalized agent。

### 21.2 指标

| 指标 | 含义 |
| --- | --- |
| Relevance@10 | 前 10 条中用户认为相关的比例 |
| Novelty@10 | 前 10 条中用户觉得之前不知道的比例 |
| Actionability | 用户愿意点击、收藏、深读或复现的比例 |
| Duplication Rate | 日报中重复或近重复内容比例 |
| Evidence Accuracy | 推荐理由是否被来源支持 |
| Diversity | 是否避免全部推荐同一小方向 |
| Time Saved | 相比手动浏览节省的时间 |
| Feedback Learning | 反馈后推荐是否发生合理变化 |

### 21.3 Demo 场景

1. 设置三个用户画像：你、CV 同学、Robotics 同学。
2. 同一天抓同一批 shared candidate pool。
3. 展示三份不同 personal digest。
4. 打开一篇 Agent memory 论文，提问“这和我的 future prediction agent 有什么关系？”
5. 系统基于 item + 用户画像 + 知识库回答。
6. 点击“沉淀为 research note”。
7. 展示 profile update candidate。
8. 展示下一次日报中相关主题权重提升。

## 22. 作业报告中的创新表述

可以直接写成：

ResearchRadar 的创新点在于将多源研究信息采集、用户研究画像、个性化 novelty-aware ranking、证据化摘要、交互式研究问答、conversation-to-knowledge 蒸馏和长期 memory consolidation 整合为一个闭环。与传统 RSS 阅读器不同，它不是以信息源为中心，而是以用户研究意图为中心；与普通论文推荐系统不同，它同时整合论文、代码、博客、社区讨论和中文技术动态；与简单日报机器人不同，它会根据用户的收藏、忽略、深读和追问持续更新个人研究画像，使系统从“每天推送信息”升级为“持续维护个人研究视野”。

## 23. 当前推荐版本

最终建议第一版命名为：

ResearchRadar v1：Personal AI Research Radar Agent

核心卖点：

- Core AI / ML / Agent arXiv full radar。
- 多源异质信息融合。
- 多用户个性化 research profile。
- Novelty-aware ranking。
- Evidence Card。
- Interactive Research Copilot。
- Conversation-to-Knowledge。
- Feedback-to-Profile learning。
- Weekly memory consolidation。

v1 成功标准：

连续运行 3-5 天后，你愿意每天打开它；同一天对不同 profile 能产生明显不同的高质量 digest；你能围绕一条推荐完成追问、理解、收藏、沉淀，并在之后的知识库里重新找回这条思考。
