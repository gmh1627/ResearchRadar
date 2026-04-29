from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class AppConfig:
    settings: dict[str, Any]
    sources: list[dict[str, Any]]
    profiles: list[dict[str, Any]]

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "researchradar.sqlite3"

    @property
    def host(self) -> str:
        return str(self.settings.get("server", {}).get("host", "0.0.0.0"))

    @property
    def port(self) -> int:
        return int(self.settings.get("server", {}).get("port", 8765))

    @property
    def timezone(self) -> str:
        return str(self.settings.get("crawl", {}).get("timezone", "Asia/Shanghai"))

    @property
    def daily_time(self) -> str:
        return str(self.settings.get("crawl", {}).get("daily_time", "07:30"))

    @property
    def initial_backfill_days(self) -> int:
        return int(self.settings.get("crawl", {}).get("initial_backfill_days", 14))

    @property
    def request_timeout(self) -> float:
        return float(self.settings.get("crawl", {}).get("request_timeout_seconds", 25))

    @property
    def user_agent(self) -> str:
        return str(self.settings.get("crawl", {}).get("user_agent", "ResearchRadar/0.1"))

    @property
    def digest_item_count(self) -> int:
        return int(self.settings.get("ranking", {}).get("digest_item_count", 12))


def load_config() -> AppConfig:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    settings = load_yaml(CONFIG_DIR / "settings.yaml")
    sources = load_yaml(CONFIG_DIR / "sources.yaml").get("sources", [])
    profiles = load_yaml(CONFIG_DIR / "profiles.yaml").get("profiles", [])
    return AppConfig(settings=settings, sources=sources, profiles=profiles)
