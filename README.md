# ResearchRadar

ResearchRadar is a local browser-based personal AI research source agent. It crawls Core AI / ML / Agent arXiv papers plus important company/lab blogs, GitHub, Hacker News, AIHOT, and RSS sources, then serves a local Web dashboard for dated radar browsing, personalized digests, feedback learning, knowledge search, and research chat across items, digests, and the personal knowledge base.

## Current Capabilities

- Daily catch-up crawler with per-source run status and partial-failure reporting.
- AIHOT public API source for curated X/KOL/product updates that ResearchRadar cannot collect directly.
- GPT-5.5 post-collection relevance filtering, Chinese summary generation, five-dimension analysis, and code-side final scoring.
- Categorized personal digest ranked by the effective research profile, source authority, recency, trend signals, and user feedback, capped by `ranking.digest_max_items`.
- Digest, arXiv, blog/lab, and full radar views support choosing a specific historical date from the data actually available locally.
- Feedback learning across similar tags and sources, with explicit profile-update candidates and accepted profile memory.
- Item Evidence Card with source reliability, evidence role, authors/categories, arXiv/PDF/HN/code links, and date semantics.
- Date labels distinguish reliable publish time from first-discovered time for undated pages.
- Dashboard pagination via “load more” for arXiv, blogs, and the full radar.
- Resizable left navigation and right detail columns, with widths remembered in the browser.
- Knowledge workbench with the effective research profile, explicit profile-learning controls, FTS-backed search across saved/viewed items, notes, conversations, and Wiki pages, a graph of viewed/saved/Q&A/note-linked items, removable saved/deep-read queues, removable Q&A, notes, and item-level note taking.
- Research chat works at item, digest, and knowledge-base scopes. Saved chat answers are distilled into structured research notes instead of raw chat dumps.
- LLM Wiki compilation uses the accepted profile memory and maintains overview, concept, source, index pages, and an append-only log.
- Safe Markdown rendering for summaries, notes, and Q&A, including headings, lists, quotes, code blocks, LaTeX math, `\href{...}{...}`, Markdown links, and plain http(s) URLs.

## Quick Start

```bash
cd /home/dataset-local/ResearchRadar
scripts/setup_venv.sh
# If .env already exists, keep it. Otherwise:
cp .env.example .env
scripts/start_background.sh
```

Open:

```text
http://127.0.0.1:8765
```

The startup command prints the exact local URL. The default port is `8765`; set `RESEARCHRADAR_PORT=8766` before starting if you want `http://127.0.0.1:8766`.

## API Keys

Put local keys in `.env`. This file is ignored by git.

Supported keys:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `JINA_API_KEY`
- `SERPER_API_KEY`

The default model path uses the OpenAI Responses API. For the Haoxiang gateway, set `OPENAI_BASE_URL=https://ie-crs.haoxiang.ai`; ResearchRadar will fall back to streamed Chat Completions if the Responses endpoint is unavailable or returns empty content.

Chat latency controls live in `config/settings.yaml` under `llm.chat_timeout_seconds`,
`llm.request_timeout_seconds`, `llm.max_model_attempts`, and `jina.timeout_seconds`.

The ingestion post-process also uses GPT-5.5 by default (`llm_postprocess.model`).
It first filters non-AI items, then asks the model for Chinese summaries, tags,
relevance/novelty/significance/actionability/credibility dimensions, reasons,
and next actions. The final quality score is still computed by ResearchRadar code
from those dimensions plus source authority, trend, recency, and user feedback.

## Crawling Behavior

- Server startup initializes the database and, when `crawl.scheduler_enabled` is true, starts one scheduler instance protected by `data/scheduler.lock`, then runs a background catch-up to repair missed days and recent arXiv gaps.
- Manual `crawl` and scheduled daily crawls run the GPT-5.5 post-process after collection.
- `catch-up` is still available as a CLI command for missed days, but intentionally skips the GPT-5.5 post-process to avoid a large surprise model bill.
- While running, it crawls every day at `crawl.daily_time` in `crawl.daily_time_timezone` (currently 20:10 America/New_York, about ten minutes after arXiv announcements) and fetches that announcement date.
- AIHOT is configured through `config/sources.yaml` as `aihot_public`, using `https://aihot.virxact.com/api/public/items` with `mode=selected`.
- Each source run records `success`, `partial`, `skipped`, or `error`; a daily crawl is marked `partial` if any enabled source degrades or fails.
- Undated page items are sorted by first-discovered time and labeled that way in the UI, so old pages are not silently treated as fresh publications.
- If SSH disconnects, the background process keeps running.
- `scripts/install_autostart.sh` installs a crontab `@reboot` entry so the service starts when the server boots.

Manual crawl:

```bash
.venv/bin/python -m researchradar crawl --days 14
```

Manual GPT-5.5 post-process:

```bash
.venv/bin/python -m researchradar llm-postprocess --days 3 --limit 80
```

## Important Files

- `config/sources.yaml`: source list, including company and lab blogs.
- `config/profiles.yaml`: user research profiles.
- `config/settings.yaml`: crawl, scheduler, ranking, LLM, and server settings.
- `data/researchradar.sqlite3`: local database, ignored by git. This is the only database file used by the app.
- `logs/server.log`: background server log, ignored by git.

## Notes

ResearchRadar is intended as a local personal dashboard. If you expose `0.0.0.0:8765` or a custom port such as `8766` beyond localhost, put it behind SSH tunneling, a firewall, or a reverse proxy with authentication because crawl and chat endpoints can trigger external API calls.

The dashboard uses MathJax from jsDelivr to typeset formulas in dynamic content. If the machine is offline, the same text remains readable, but formulas will stay in their original LaTeX form.

## Autostart

Simple user-level autostart:

```bash
scripts/install_autostart.sh
```

Systemd template is available at `deploy/researchradar.service` if you prefer a system service.
