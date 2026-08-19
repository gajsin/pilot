from __future__ import annotations

import argparse
import asyncio
from app.cli import cmd_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Pilot Metrics & Ingestion Report")
    parser.add_argument("--category", type=int, default=13987)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--pages", type=int, default=None)
    parser.add_argument("--db-url", type=str, default=None)
    args = parser.parse_args()

    pages = args.pages if args.pages is not None else max(1, (args.limit + 9) // 10)
    asyncio.run(cmd_report(args.category, args.limit, pages, args.db_url))


if __name__ == "__main__":
    main()
