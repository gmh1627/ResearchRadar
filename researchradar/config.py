from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
LOCAL_NO_PROXY_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "127.0.0.0/8")


def ensure_local_proxy_bypass() -> None:
    """Keep local ResearchRadar traffic out of HTTP_PROXY/HTTPS_PROXY."""
    for key in ("NO_PROXY", "no_proxy"):
        values = split_no_proxy(os.environ.get(key, ""))
        seen = {value.lower() for value in values}
        for host in LOCAL_NO_PROXY_HOSTS:
            if host.lower() not in seen:
                values.append(host)
                seen.add(host.lower())
        os.environ[key] = ",".join(values)


def split_no_proxy(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def profiles_path() -> Path:
    return CONFIG_DIR / "profiles.yaml"


def load_env_file(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


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
    def daily_time_timezone(self) -> str:
        crawl = self.settings.get("crawl", {})
        return str(crawl.get("daily_time_timezone", crawl.get("timezone", "Asia/Shanghai")))

    @property
    def daily_weekdays(self) -> set[int] | None:
        raw = self.settings.get("crawl", {}).get("daily_weekdays")
        if not raw:
            return None
        name_to_weekday = {
            "mon": 0,
            "monday": 0,
            "tue": 1,
            "tuesday": 1,
            "wed": 2,
            "wednesday": 2,
            "thu": 3,
            "thursday": 3,
            "fri": 4,
            "friday": 4,
            "sat": 5,
            "saturday": 5,
            "sun": 6,
            "sunday": 6,
        }
        weekdays: set[int] = set()
        values = raw if isinstance(raw, list) else str(raw).split(",")
        for value in values:
            key = str(value).strip().lower()
            if key == "":
                continue
            weekdays.add(name_to_weekday.get(key, int(key) if key.isdigit() else -1))
        return {day for day in weekdays if 0 <= day <= 6} or None

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
        ranking = self.settings.get("ranking", {})
        return int(ranking.get("digest_max_items", ranking.get("digest_item_count", 36)))


def load_config() -> AppConfig:
    load_env_file()
    ensure_local_proxy_bypass()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    settings = load_yaml(CONFIG_DIR / "settings.yaml")
    sources = load_yaml(CONFIG_DIR / "sources.yaml").get("sources", [])
    profiles = load_yaml(CONFIG_DIR / "profiles.yaml").get("profiles", [])
    return AppConfig(settings=settings, sources=sources, profiles=profiles)
