from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .collectors import Collector
from .config import AppConfig
from .db import Database


@dataclass(frozen=True)
class SourceRunOutcome:
    source_id: str
    changed: int
    items_found: int
    status: str
    error: str | None


class CrawlManager:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.collector = Collector(config.settings)
        self.lock = asyncio.Lock()
        self.status = {
            "running": False,
            "message": "idle",
            "last_started_at": None,
            "last_finished_at": None,
        }

    async def catch_up(self) -> None:
        today = datetime.now(ZoneInfo(self.config.timezone)).date()
        covered = self.db.covered_days()
        if not covered:
            days = max(self.config.initial_backfill_days, 1)
            start = today - timedelta(days=days - 1)
            await self.crawl_range(start, today, run_postprocess=False)
            await self.repair_recent_source_gaps(today, run_postprocess=False)
            return

        yesterday = today - timedelta(days=1)
        missing = []
        cursor = min(yesterday, date.fromisoformat(max(covered)) + timedelta(days=1))
        while cursor <= yesterday:
            if cursor.isoformat() not in covered:
                missing.append(cursor)
            cursor += timedelta(days=1)
        if missing:
            await self.crawl_dates(missing, run_postprocess=False)
        await self.repair_recent_source_gaps(today, run_postprocess=False)

    async def repair_recent_source_gaps(self, today: date | None = None, *, run_postprocess: bool = False) -> None:
        arxiv_source = next((source for source in self.config.sources if source.get("id") == "arxiv_core" and source.get("enabled", True)), None)
        if not arxiv_source:
            return
        local_today = today or datetime.now(ZoneInfo(self.config.timezone)).date()
        yesterday = local_today - timedelta(days=1)
        window_days = max(1, min(int(self.config.settings.get("crawl", {}).get("source_gap_repair_days", 10)), 31))
        start = yesterday - timedelta(days=window_days - 1)
        min_items = max(1, int(self.config.settings.get("crawl", {}).get("source_gap_repair_min_items", 20)))
        dates_with_enough_items = self.db.dates_with_source_items("arxiv_core", start, yesterday, min_count=min_items)
        dates = []
        cursor = start
        while cursor <= yesterday:
            day_key = cursor.isoformat()
            if day_key not in dates_with_enough_items:
                dates.append(cursor)
            cursor += timedelta(days=1)
        if not dates:
            return

        async with self.lock:
            self.status.update(
                {
                    "running": True,
                    "message": f"repairing arXiv gaps ({len(dates)} day(s))",
                    "last_started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            try:
                for index, target in enumerate(dates, start=1):
                    self.status["message"] = f"repairing arXiv {target.isoformat()} ({index}/{len(dates)})"
                    await self.run_config_source(arxiv_source, target)
                if run_postprocess:
                    await asyncio.to_thread(self.llm_postprocess_recent)
                    await asyncio.to_thread(self.translate_recent_summaries)
            finally:
                self.status.update(
                    {
                        "running": False,
                        "message": "idle",
                        "last_finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

    async def crawl_range(self, start: date, end: date, *, run_postprocess: bool = True) -> None:
        days = []
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor += timedelta(days=1)
        await self.crawl_dates(days, run_postprocess=run_postprocess)

    async def crawl_dates(self, dates: list[date], *, run_postprocess: bool = True) -> None:
        async with self.lock:
            self.status.update(
                {
                    "running": True,
                    "message": f"crawling {len(dates)} day(s)",
                    "last_started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            try:
                for index, target in enumerate(dates, start=1):
                    self.status["message"] = f"crawling {target.isoformat()} ({index}/{len(dates)})"
                    await self.crawl_day(target, run_postprocess=run_postprocess)
            finally:
                self.status.update(
                    {
                        "running": False,
                        "message": "idle",
                        "last_finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

    async def crawl_day(self, target: date, *, run_postprocess: bool = True) -> int:
        self.db.mark_day_started(target)
        total_found = 0
        successful_runs = 0
        errors: list[str] = []
        warnings: list[str] = []
        source_tasks = [self.run_config_source(source, target) for source in self.config.sources if source.get("enabled", True)]
        extra_tasks = [self.run_github(target), self.run_hackernews(target)]
        results = await asyncio.gather(*(source_tasks + extra_tasks), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            total_found += result.items_found
            if result.status in {"success", "partial"}:
                successful_runs += 1
            if result.status == "error":
                errors.append(format_source_issue(result))
            elif result.status not in {"success", "skipped"} or result.error:
                warnings.append(format_source_issue(result))
        if errors:
            status = "error" if successful_runs == 0 else "partial"
        elif warnings:
            status = "partial"
        else:
            status = "success"
        issues = errors + warnings
        self.db.mark_day_finished(target, status, total_found, "\n".join(issues) if issues else None)
        if run_postprocess:
            await asyncio.to_thread(self.llm_postprocess_recent)
            await asyncio.to_thread(self.translate_recent_summaries)
        return total_found

    async def run_config_source(self, source: dict, target: date) -> SourceRunOutcome:
        started = datetime.now(timezone.utc).isoformat()
        if source.get("type") == "aihot" and self.db.source_run_exists(target, source["id"]):
            return SourceRunOutcome(
                source["id"],
                0,
                0,
                "skipped",
                "public snapshot already collected for this target date",
            )
        try:
            result = await self.collector.collect_source(source, target)
        except Exception as exc:
            return self.record_source_error(target, source["id"], started, exc)
        return self.record_collect_result(target, started, result)

    async def run_github(self, target: date) -> SourceRunOutcome:
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = await self.collector.collect_github(target)
        except Exception as exc:
            return self.record_source_error(target, "github", started, exc)
        return self.record_collect_result(target, started, result)

    async def run_hackernews(self, target: date) -> SourceRunOutcome:
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = await self.collector.collect_hackernews(target)
        except Exception as exc:
            return self.record_source_error(target, "hackernews", started, exc)
        return self.record_collect_result(target, started, result)

    def record_collect_result(self, target: date, started: str, result) -> SourceRunOutcome:
        changed = self.db.upsert_items(result.items)
        finished = datetime.now(timezone.utc).isoformat()
        self.db.add_source_run(target, result.source_id, result.status, started, finished, len(result.items), result.error)
        return SourceRunOutcome(result.source_id, changed, len(result.items), result.status, result.error)

    def record_source_error(self, target: date, source_id: str, started: str, exc: Exception) -> SourceRunOutcome:
        finished = datetime.now(timezone.utc).isoformat()
        error = f"{type(exc).__name__}: {exc}"
        self.db.add_source_run(target, source_id, "error", started, finished, 0, error)
        return SourceRunOutcome(source_id, 0, 0, "error", error)

    async def scheduler_loop(self) -> None:
        while True:
            run_tz_name = self.config.daily_time_timezone
            await asyncio.sleep(seconds_until_next_run(run_tz_name, self.config.daily_time, self.config.daily_weekdays))
            target = datetime.now(ZoneInfo(run_tz_name)).date()
            await self.crawl_dates([target], run_postprocess=True)
            await self.repair_recent_source_gaps(run_postprocess=True)

    def translate_recent_summaries(self) -> None:
        try:
            from .translation import translate_missing

            translate_missing(limit=300)
        except Exception:
            pass

    def llm_postprocess_recent(self) -> None:
        try:
            from .llm_postprocess import run_llm_postprocess

            run_llm_postprocess(self.config, self.db)
        except Exception:
            pass


def seconds_until_next_run(tz_name: str, hhmm: str, weekdays: set[int] | None = None) -> float:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    while next_run <= now or (weekdays is not None and next_run.weekday() not in weekdays):
        next_run += timedelta(days=1)
    return max((next_run - now).total_seconds(), 1.0)


def format_source_issue(outcome: SourceRunOutcome) -> str:
    detail = outcome.error or outcome.status
    return f"{outcome.source_id}: {detail}"
