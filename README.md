# ResearchRadar

ResearchRadar is a local browser-based personal AI research source agent. It crawls Core AI / ML / Agent arXiv papers plus important company/lab blogs, GitHub, Hacker News, and RSS sources, then serves a local Web dashboard for reading, filtering, feedback, and item-level research chat.

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
http://<server-local-ip>:8765
```

The startup command prints the exact URLs. If the server's private IP is not reachable from your browser, use SSH port forwarding:

```bash
ssh -L 8765:127.0.0.1:8765 <user>@<server>
```

Then open:

```text
http://127.0.0.1:8765
```

On this machine you can check the local IP with:

```bash
hostname -I
```

## API Keys

Put local keys in `.env`. This file is ignored by git.

Supported keys:

- `OPENROUTER_API_KEY`
- `OPENROUTER_BASE_URL`
- `OPENROUTER_MODEL`
- `JINA_API_KEY`
- `SERPER_API_KEY`

OpenRouter is called through plain OpenAI-compatible `/chat/completions`, without reasoning payload fields, so it works with stricter compatible gateways.

## Crawling Behavior

- On first server startup, ResearchRadar backfills `initial_backfill_days` from `config/settings.yaml` (default 14 days).
- On later startups, it checks missed crawl days and runs catch-up immediately.
- While running, it crawls every day at `crawl.daily_time`.
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
- `data/researchradar.sqlite3`: local database, ignored by git.
- `logs/server.log`: background server log, ignored by git.

## Autostart

Simple user-level autostart:

```bash
scripts/install_autostart.sh
```

Systemd template is available at `deploy/researchradar.service` if you prefer a system service.
