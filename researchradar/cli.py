from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone

from .config import load_config
from .crawler import CrawlManager
from .db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="researchradar")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")

    crawl = sub.add_parser("crawl")
    crawl.add_argument("--days", type=int, default=1)
    crawl.add_argument("--start", type=str)
    crawl.add_argument("--end", type=str)

    sub.add_parser("catch-up")
    sub.add_parser("stats")

    translate = sub.add_parser("translate-missing")
    translate.add_argument("--limit", type=int, default=500)
    translate.add_argument("--batch-size", type=int, default=32)

    postprocess = sub.add_parser("llm-postprocess")
    postprocess.add_argument("--limit", type=int)
    postprocess.add_argument("--days", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    db = Database(config.db_path)
    db.initialize()
    manager = CrawlManager(config, db)

    if args.command == "init-db":
        print(f"Initialized database: {config.db_path}")
        return

    if args.command == "stats":
        print(db.stats())
        return

    if args.command == "translate-missing":
        from .translation import translate_missing

        count = translate_missing(limit=max(args.limit, 1), batch_size=max(args.batch_size, 1))
        print(f"Translated {count} item summaries.")
        return

    if args.command == "llm-postprocess":
        from .llm_postprocess import run_llm_postprocess

        count = run_llm_postprocess(config, db, limit=args.limit, days=args.days)
        print(f"LLM postprocessed {count} items.")
        return

    if args.command == "catch-up":
        asyncio.run(manager.catch_up())
        print("Catch-up finished.")
        return

    if args.command == "crawl":
        if args.start:
            start = date.fromisoformat(args.start)
            end = date.fromisoformat(args.end) if args.end else start
        else:
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=max(args.days, 1) - 1)
        asyncio.run(manager.crawl_range(start, end, run_postprocess=True))
        print(f"Crawl finished: {start.isoformat()} to {end.isoformat()}")
        return


if __name__ == "__main__":
    main()
