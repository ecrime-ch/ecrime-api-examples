#!/usr/bin/env python3
"""Generate a portable monthly ransomware report for a country or region."""

from __future__ import annotations

import argparse
import collections
import csv
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

import requests


DEFAULT_API_URL = "https://ecrime.ch/api/v1"


@dataclass(frozen=True)
class Window:
    start: date
    end: date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="Reporting month: YYYY-MM")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--country", help="Single country, for example Switzerland")
    scope.add_argument(
        "--countries",
        help="Comma-separated countries, for example Germany,Austria,Switzerland",
    )
    parser.add_argument("--scope-name", help="Display name for a multi-country region")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Parent output directory (default: ./output)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ECRIME_API_KEY"),
        help="Defaults to the ECRIME_API_KEY environment variable",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("ECRIME_API_URL", DEFAULT_API_URL),
    )
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    if not args.api_key:
        parser.error("Set ECRIME_API_KEY or pass --api-key")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def month_window(month: str) -> Window:
    try:
        start = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("--month must use YYYY-MM format") from exc

    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    return Window(start, end)


def previous_month(start: date, offset: int) -> date:
    year = start.year
    month = start.month - offset
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def parse_countries(args: argparse.Namespace) -> list[str]:
    if args.country:
        return [args.country.strip()]
    countries = [value.strip() for value in args.countries.split(",")]
    return [value for value in countries if value]


def safe_name(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result or "report"


def is_duplicate(event: dict) -> bool:
    return event.get("duplicate") not in (None, "", 0, "0", False)


def fetch_events(
    session: requests.Session,
    *,
    api_url: str,
    api_key: str,
    window: Window,
    timeout: int,
) -> list[dict]:
    url = (
        f"{api_url.rstrip('/')}/events/list/"
        f"from/{window.start.isoformat()}/to/{window.end.isoformat()}/"
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
    return [
        event
        for event in payload.get("data", [])
        if not is_duplicate(event)
    ]


def filter_countries(events: Iterable[dict], countries: list[str]) -> list[dict]:
    allowed = {country.casefold() for country in countries}
    return [
        event
        for event in events
        if str(event.get("country") or "").casefold() in allowed
    ]


def has_data_leak(event: dict) -> bool:
    if event.get("data_leak"):
        return True
    return any(
        status.get("name") == "data_leak"
        for status in event.get("status", [])
        if isinstance(status, dict)
    )


def top_values(events: Iterable[dict], field: str, limit: int = 8) -> list[tuple[str, int]]:
    counts: collections.Counter[str] = collections.Counter()
    for event in events:
        value = str(event.get(field) or "").strip()
        if value:
            counts[value] += 1
    return counts.most_common(limit)


def percentage(value: int, total: int) -> float:
    return round(value * 100 / total, 1) if total else 0.0


def build_summary(
    *,
    scope_name: str,
    countries: list[str],
    month: str,
    events: list[dict],
    trend: list[dict],
) -> dict:
    leaked = sum(has_data_leak(event) for event in events)
    return {
        "scope": scope_name,
        "countries": countries,
        "month": month,
        "total_events": len(events),
        "data_leak_events": leaked,
        "data_leak_share": percentage(leaked, len(events)),
        "top_actors": top_values(events, "leak_site"),
        "top_sectors": top_values(events, "sector"),
        "top_employee_bands": top_values(events, "employees"),
        "monthly_trend": trend,
    }


def build_trend(
    session: requests.Session,
    *,
    api_url: str,
    api_key: str,
    timeout: int,
    countries: list[str],
    reporting_start: date,
) -> list[dict]:
    trend = []
    for offset in range(5, -1, -1):
        start = previous_month(reporting_start, offset)
        window = month_window(start.strftime("%Y-%m"))
        events = fetch_events(
            session,
            api_url=api_url,
            api_key=api_key,
            window=window,
            timeout=timeout,
        )
        scoped = filter_countries(events, countries)
        trend.append(
            {
                "month": start.strftime("%Y-%m"),
                "events": len(scoped),
                "data_leak_events": sum(has_data_leak(event) for event in scoped),
            }
        )
    return trend


def incident_rows(events: list[dict]) -> list[dict]:
    rows = []
    for event in sorted(
        events,
        key=lambda item: str(item.get("first_seen") or ""),
        reverse=True,
    ):
        rows.append(
            {
                "id": event.get("id", ""),
                "first_seen": event.get("first_seen", ""),
                "organization": event.get("name") or event.get("leak_title") or "",
                "country": event.get("country", ""),
                "sector": event.get("sector", ""),
                "actor": event.get("leak_site", ""),
                "website": event.get("website", ""),
                "employees": event.get("employees", ""),
                "revenue": event.get("revenue", ""),
                "data_leak": "yes" if has_data_leak(event) else "no",
            }
        )
    return rows


def render_bar_chart(
    title: str,
    subtitle: str,
    values: list[tuple[str, int]],
    color: str,
) -> str:
    width = 1200
    height = 630
    maximum = max((value for _, value in values), default=1)
    rows = []

    for index, (label, value) in enumerate(values[:6]):
        y = 155 + index * 72
        bar_width = int(value / maximum * 590) if maximum else 0
        rows.append(
            f'<text x="70" y="{y - 10}" fill="#dce5f2" font-size="20" '
            f'font-family="Arial, sans-serif">{escape(label)}</text>'
            f'<rect x="70" y="{y}" width="620" height="18" rx="9" fill="#28354b"/>'
            f'<rect x="70" y="{y}" width="{bar_width}" height="18" rx="9" '
            f'fill="{color}"/>'
            f'<text x="720" y="{y + 15}" fill="#ffffff" font-size="20" '
            f'font-family="Arial, sans-serif">{value}</text>'
        )

    return (
        '<svg width="1200" height="630" viewBox="0 0 1200 630" '
        'xmlns="http://www.w3.org/2000/svg">'
        '<rect width="1200" height="630" rx="24" fill="#10182a"/>'
        f'<text x="70" y="70" fill="#ffffff" font-size="36" font-weight="700" '
        f'font-family="Arial, sans-serif">{escape(title)}</text>'
        f'<text x="70" y="108" fill="#92a2ba" font-size="20" '
        f'font-family="Arial, sans-serif">{escape(subtitle)}</text>'
        f'{"".join(rows)}</svg>'
    )


def render_trend_chart(scope_name: str, trend: list[dict]) -> str:
    values = [item["events"] for item in trend]
    maximum = max(values, default=1)
    bars = []

    for index, item in enumerate(trend):
        x = 105 + index * 165
        bar_height = int(item["events"] / maximum * 300) if maximum else 0
        y = 450 - bar_height
        bars.append(
            f'<rect x="{x}" y="{y}" width="95" height="{bar_height}" '
            'rx="10" fill="#e03a4e"/>'
            f'<text x="{x + 47}" y="{y - 12}" fill="#ffffff" font-size="20" '
            f'text-anchor="middle" font-family="Arial, sans-serif">'
            f'{item["events"]}</text>'
            f'<text x="{x + 47}" y="490" fill="#92a2ba" font-size="18" '
            f'text-anchor="middle" font-family="Arial, sans-serif">'
            f'{escape(item["month"])}</text>'
        )

    return (
        '<svg width="1200" height="630" viewBox="0 0 1200 630" '
        'xmlns="http://www.w3.org/2000/svg">'
        '<rect width="1200" height="630" rx="24" fill="#10182a"/>'
        '<text x="70" y="70" fill="#ffffff" font-size="36" font-weight="700" '
        'font-family="Arial, sans-serif">Six-month claim trend</text>'
        f'<text x="70" y="108" fill="#92a2ba" font-size="20" '
        f'font-family="Arial, sans-serif">{escape(scope_name)}</text>'
        '<path d="M70 450H1130" stroke="#34425a" stroke-width="2"/>'
        f'{"".join(bars)}</svg>'
    )


def markdown_table(rows: list[dict], limit: int = 15) -> str:
    output = [
        "| Date | Organization | Country | Sector | Actor | Data leak |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows[:limit]:
        cells = [
            str(row["first_seen"])[:10],
            str(row["organization"]),
            str(row["country"]),
            str(row["sector"]),
            str(row["actor"]),
            str(row["data_leak"]),
        ]
        output.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(output)


def render_markdown(summary: dict, incidents: list[dict]) -> str:
    actor = summary["top_actors"][0][0] if summary["top_actors"] else "n/a"
    sector = summary["top_sectors"][0][0] if summary["top_sectors"] else "n/a"
    return f"""# Regional Ransomware Report: {summary["scope"]}

Reporting month: **{summary["month"]}**

## Executive summary

- Observed claims: **{summary["total_events"]}**
- Claims with a public data-leak indicator: **{summary["data_leak_events"]}** ({summary["data_leak_share"]}%)
- Most represented actor: **{actor}**
- Most represented sector: **{sector}**

The figures represent events recorded by eCrime.ch for the configured countries.
They should be interpreted as observed name-and-shame activity rather than a
complete census of ransomware incidents.

## Recent incidents

{markdown_table(incidents)}

## Recommended review

- Review exposure and detection coverage for the most represented actors.
- Prioritize preparedness in the most affected sectors.
- Track data-leak indicators separately from raw claim counts.
- Compare monthly activity over time before drawing conclusions from short-term changes.
"""


def render_html(summary: dict, incidents: list[dict]) -> str:
    actor_rows = "".join(
        f"<li>{html.escape(name)}: <strong>{count}</strong></li>"
        for name, count in summary["top_actors"]
    )
    sector_rows = "".join(
        f"<li>{html.escape(name)}: <strong>{count}</strong></li>"
        for name, count in summary["top_sectors"]
    )
    table_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['first_seen'])[:10])}</td>"
        f"<td>{html.escape(str(row['organization']))}</td>"
        f"<td>{html.escape(str(row['country']))}</td>"
        f"<td>{html.escape(str(row['sector']))}</td>"
        f"<td>{html.escape(str(row['actor']))}</td>"
        f"<td>{html.escape(str(row['data_leak']))}</td>"
        "</tr>"
        for row in incidents[:25]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Regional ransomware report — {html.escape(summary["scope"])}</title>
  <style>
    body {{ font: 16px/1.55 system-ui,sans-serif; margin: 0; background: #f4f6fa; color: #172033; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 48px 24px; }}
    header {{ background: #10182a; color: white; padding: 36px; border-radius: 18px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 16px; margin: 24px 0; }}
    .metric, section {{ background: white; padding: 22px; border-radius: 14px; box-shadow: 0 5px 20px #17203312; }}
    .metric strong {{ display: block; font-size: 30px; }}
    .columns {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: top; }}
    img {{ max-width: 100%; border-radius: 14px; margin: 20px 0; }}
    @media (max-width: 700px) {{ .columns {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body><main>
  <header>
    <h1>Regional Ransomware Report</h1>
    <p>{html.escape(summary["scope"])} · {html.escape(summary["month"])}</p>
  </header>
  <div class="metrics">
    <div class="metric"><strong>{summary["total_events"]}</strong>observed claims</div>
    <div class="metric"><strong>{summary["data_leak_events"]}</strong>data-leak indicators</div>
    <div class="metric"><strong>{summary["data_leak_share"]}%</strong>data-leak share</div>
  </div>
  <img src="monthly_trend.svg" alt="Six-month claim trend">
  <div class="columns">
    <section><h2>Top actors</h2><ol>{actor_rows}</ol></section>
    <section><h2>Top sectors</h2><ol>{sector_rows}</ol></section>
  </div>
  <img src="top_actors.svg" alt="Most represented actors">
  <img src="top_sectors.svg" alt="Most represented sectors">
  <section>
    <h2>Recent incidents</h2>
    <table>
      <thead><tr><th>Date</th><th>Organization</th><th>Country</th><th>Sector</th><th>Actor</th><th>Data leak</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </section>
</main></body></html>
"""


def write_outputs(
    output_dir: Path,
    *,
    summary: dict,
    incidents: list[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_markdown(summary, incidents),
        encoding="utf-8",
    )
    (output_dir / "report.html").write_text(
        render_html(summary, incidents),
        encoding="utf-8",
    )
    (output_dir / "monthly_trend.svg").write_text(
        render_trend_chart(summary["scope"], summary["monthly_trend"]),
        encoding="utf-8",
    )
    (output_dir / "top_actors.svg").write_text(
        render_bar_chart(
            "Most represented ransomware actors",
            f'{summary["scope"]} · {summary["month"]}',
            summary["top_actors"],
            "#e03a4e",
        ),
        encoding="utf-8",
    )
    (output_dir / "top_sectors.svg").write_text(
        render_bar_chart(
            "Most represented sectors",
            f'{summary["scope"]} · {summary["month"]}',
            summary["top_sectors"],
            "#38bdf8",
        ),
        encoding="utf-8",
    )

    fieldnames = list(incidents[0]) if incidents else [
        "id",
        "first_seen",
        "organization",
        "country",
        "sector",
        "actor",
        "website",
        "employees",
        "revenue",
        "data_leak",
    ]
    with (output_dir / "incidents.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(incidents)


def main() -> int:
    args = parse_args()
    countries = parse_countries(args)
    if not countries:
        raise SystemExit("No countries were supplied")

    window = month_window(args.month)
    scope_name = args.scope_name or (
        countries[0] if len(countries) == 1 else ", ".join(countries)
    )
    destination = args.output_dir / safe_name(scope_name) / args.month

    session = requests.Session()
    session.headers["User-Agent"] = "ecrime-api-examples-regional-report/1.0"

    events = fetch_events(
        session,
        api_url=args.api_url,
        api_key=args.api_key,
        window=window,
        timeout=args.timeout,
    )
    scoped_events = filter_countries(events, countries)
    trend = build_trend(
        session,
        api_url=args.api_url,
        api_key=args.api_key,
        timeout=args.timeout,
        countries=countries,
        reporting_start=window.start,
    )
    summary = build_summary(
        scope_name=scope_name,
        countries=countries,
        month=args.month,
        events=scoped_events,
        trend=trend,
    )
    incidents = incident_rows(scoped_events)
    write_outputs(destination, summary=summary, incidents=incidents)

    print(f"Generated report for {scope_name}: {destination}")
    print(f"Observed events: {summary['total_events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
