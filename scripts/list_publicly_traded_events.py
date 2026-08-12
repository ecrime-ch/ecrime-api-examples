#!/usr/bin/env python3
"""List eCrime.ch events whose victim has stock information."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests


DEFAULT_API_URL = "https://ecrime.ch/api/v1"

CSV_COLUMNS = [
    "id",
    "first_seen",
    "leak_site",
    "leak_title",
    "name",
    "website",
    "country",
    "sector",
    "stock_symbol",
    "leak_url",
    "data_leak",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Look back this many hours (default: 24).",
    )
    window.add_argument(
        "--from",
        dest="from_date",
        help="Start date/time in YYYY-MM-DD or ISO-8601 format.",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        help="End date/time in YYYY-MM-DD or ISO-8601 format (default: now).",
    )
    parser.add_argument(
        "--include-duplicates",
        action="store_true",
        help="Include events marked as duplicate.",
    )
    parser.add_argument(
        "--format",
        choices=("table", "csv", "json"),
        default="table",
        help="Output format (default: table).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ECRIME_API_KEY"),
        help="Defaults to the ECRIME_API_KEY environment variable.",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("ECRIME_API_URL", DEFAULT_API_URL),
        help=f"Defaults to {DEFAULT_API_URL}.",
    )
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    if not args.api_key:
        parser.error("Set ECRIME_API_KEY or pass --api-key")
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.to_date and not args.from_date:
        parser.error("--to requires --from")
    return args


def parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        if len(normalized) == 10:
            return datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date/time: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def event_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    if args.from_date:
        start = parse_datetime(args.from_date)
        end = parse_datetime(args.to_date) if args.to_date else datetime.now(timezone.utc)
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=args.hours)
    return start.replace(microsecond=0), end.replace(microsecond=0)


def fetch_events(
    session: requests.Session,
    *,
    api_url: str,
    api_key: str,
    start: datetime,
    end: datetime,
    timeout: int,
) -> list[dict]:
    url = (
        f"{api_url.rstrip('/')}/events/list/"
        f"from/{int(start.timestamp())}/to/{int(end.timestamp())}/"
    )
    response = session.get(
        url,
        headers={"X-API-Key": api_key, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("status")) != "200":
        raise RuntimeError(
            f"eCrime API error: {payload.get('message', 'unknown error')}"
        )
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise RuntimeError("eCrime API response did not contain a data list")
    return [event for event in data if isinstance(event, dict)]


def has_stock_information(event: dict) -> bool:
    return bool(str(event.get("stock_symbol") or "").strip())


def is_duplicate(event: dict) -> bool:
    return event.get("duplicate") not in (None, "", 0, "0", False)


def publicly_traded_events(events: Iterable[dict], *, include_duplicates: bool) -> list[dict]:
    matches = []
    for event in events:
        if not include_duplicates and is_duplicate(event):
            continue
        if has_stock_information(event):
            matches.append(event)
    return matches


def event_row(event: dict) -> dict[str, object]:
    return {
        column: "" if event.get(column) is None else event.get(column)
        for column in CSV_COLUMNS
    }


def print_csv(events: list[dict]) -> None:
    writer = csv.DictWriter(sys.stdout, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        writer.writerow(event_row(event))


def print_json(events: list[dict]) -> None:
    print(json.dumps([event_row(event) for event in events], indent=2, ensure_ascii=False))


def print_table(events: list[dict], *, start: datetime, end: datetime) -> None:
    print(f"Publicly traded events from {start:%Y-%m-%d %H:%M UTC} to {end:%Y-%m-%d %H:%M UTC}")
    print(f"Matches: {len(events)}")
    if not events:
        return

    rows = [event_row(event) for event in events]
    columns = ["id", "first_seen", "stock_symbol", "name", "leak_title", "leak_site", "country"]
    widths = {
        column: min(
            max(len(column), *(len(str(row.get(column, ""))) for row in rows)),
            36,
        )
        for column in columns
    }

    print("  ".join(column.upper().ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        values = []
        for column in columns:
            value = str(row.get(column, ""))
            if len(value) > widths[column]:
                value = value[: widths[column] - 1] + "…"
            values.append(value.ljust(widths[column]))
        print("  ".join(values))


def main() -> int:
    args = parse_args()
    start, end = event_window(args)
    if start >= end:
        raise SystemExit("--from must be before --to")

    session = requests.Session()
    events = fetch_events(
        session,
        api_url=args.api_url,
        api_key=args.api_key,
        start=start,
        end=end,
        timeout=args.timeout,
    )
    matches = publicly_traded_events(
        events,
        include_duplicates=args.include_duplicates,
    )

    if args.format == "csv":
        print_csv(matches)
    elif args.format == "json":
        print_json(matches)
    else:
        print_table(matches, start=start, end=end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
