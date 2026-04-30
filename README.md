# ResearchRadar

ResearchRadar is a local browser-based personal AI research source agent. It crawls Core AI / ML / Agent arXiv papers plus important company/lab blogs, GitHub, Hacker News, and RSS sources, then serves a local Web dashboard for reading, filtering, feedback, and item-level research chat.

## Current Capabilities

- Daily catch-up crawler with per-source run status and partial-failure reporting.
- Categorized personal digest ranked by research profile, source authority, recency, trend signals, and user feedback, capped by `ranking.digest_max_items`.
- Feedback learning across similar tags and sources, not only the exact same item.
- Item Evidence Card with source reliability, evidence role, authors/categories, arXiv/PDF/HN/code links, and date semantics.
- Date labels distinguish reliable publish time from first-discovered time for undated pages.
- Dashboard pagination via “load more” for arXiv, blogs, and the full radar.
- Resizable left navigation and right detail columns, with widths remembered in the browser.
- Knowledge workbench with the full research profile, a graph of viewed/saved/Q&A/note-linked items, removable saved/deep-read queues, removable Q&A, notes, and item-level note taking.
- Item-level research chat with optional answer-to-note saving, visible failure messages, and local fallback answers when the external model is slow or unavailable.
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

The startup command prints the exact local URL.

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

## Crawling Behavior

- On first server startup, ResearchRadar backfills `initial_backfill_days` from `config/settings.yaml` (default 14 days).
- On later startups, it checks missed crawl days and runs catch-up immediately.
- While running, it crawls every day at `crawl.daily_time` in `crawl.timezone` (currently 07:30 Asia/Shanghai) and fetches the previous calendar day.
- Each source run records `success`, `partial`, `skipped`, or `error`; a daily crawl is marked `partial` if any enabled source degrades or fails.
- Undated page items are sorted by first-discovered time and labeled that way in the UI, so old pages are not silently treated as fresh publications.
- If SSH disconnects, the background process keeps running.
- `scripts/install_autostart.sh` installs a crontab `@reboot` entry so the service starts when the server boots.

Manual crawl:

```bash
.venv/bin/python -m researchradar crawl --days 14
```

## Important Files

- `config/sources.yaml`: source list, including company and lab blogs.
- `config/profiles.yaml`: user research profiles.
- `config/settings.yaml`: crawl and server settings.
- `data/researchradar.sqlite3`: local database, ignored by git. This is the only database file used by the app.
- `logs/server.log`: background server log, ignored by git.

## Notes

ResearchRadar is intended as a local personal dashboard. If you expose `0.0.0.0:8765` beyond localhost, put it behind SSH tunneling, a firewall, or a reverse proxy with authentication because crawl and chat endpoints can trigger external API calls.

The dashboard uses MathJax from jsDelivr to typeset formulas in dynamic content. If the machine is offline, the same text remains readable, but formulas will stay in their original LaTeX form.

## Autostart

Simple user-level autostart:

```bash
scripts/install_autostart.sh
```

Systemd template is available at `deploy/researchradar.service` if you prefer a system service.
