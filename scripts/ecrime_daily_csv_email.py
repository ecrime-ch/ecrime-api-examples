#!/usr/bin/env python3
"""Email a daily CSV report of newly listed eCrime.ch victims."""

from __future__ import annotations

import argparse
import csv
import io
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import requests


API_URL = "https://ecrime.ch/api/v1"

CSV_COLUMNS = [
    "id",
    "first_seen",
    "leak_site",
    "leak_title",
    "name",
    "website",
    "country",
    "sector",
    "employees",
    "revenue",
    "leak_url",
    "data_leak",
]


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fetch_events(api_key: str, start: datetime, end: datetime) -> list[dict]:
    start_timestamp = int(start.timestamp())
    end_timestamp = int(end.timestamp())
    url = (
        f"{API_URL}/events/list/"
        f"from/{start_timestamp}/to/{end_timestamp}/"
    )

    response = requests.get(
        url,
        headers={"X-API-Key": api_key},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()

    if str(payload.get("status")) != "200":
        raise RuntimeError(
            f"eCrime API error: {payload.get('message', 'unknown error')}"
        )

    # Duplicate records are not separate victims.
    return [
        event
        for event in payload.get("data", [])
        if event.get("duplicate") is None
    ]


def create_csv(events: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for event in events:
        writer.writerow(
            {
                column: "" if event.get(column) is None else event.get(column)
                for column in CSV_COLUMNS
            }
        )

    # The UTF-8 BOM helps Microsoft Excel recognize the encoding.
    return output.getvalue().encode("utf-8-sig")


def send_email(
    *,
    csv_data: bytes,
    filename: str,
    event_count: int,
    start: datetime,
    end: datetime,
) -> None:
    smtp_host = required_env("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = required_env("SMTP_USER")
    smtp_password = required_env("SMTP_PASSWORD")
    mail_from = os.environ.get("MAIL_FROM", smtp_user).strip()
    recipients = [
        address.strip()
        for address in required_env("MAIL_TO").split(",")
        if address.strip()
    ]

    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = (
        f"eCrime.ch daily victim report — {event_count} new "
        f"event{'s' if event_count != 1 else ''}"
    )
    message.set_content(
        "Hello,\n\n"
        f"Attached are the {event_count} new name-and-shame "
        f"event{'s' if event_count != 1 else ''} observed between "
        f"{start:%Y-%m-%d %H:%M UTC} and {end:%Y-%m-%d %H:%M UTC}.\n\n"
        "Kind regards,\n"
        "eCrime.ch\n"
    )
    message.add_attachment(
        csv_data,
        maintype="text",
        subtype="csv",
        filename=filename,
    )

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Email a CSV containing newly listed eCrime.ch victims."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Reporting window in hours (default: 24).",
    )
    parser.add_argument(
        "--dry-run",
        type=Path,
        metavar="FILE",
        help="Write the CSV to FILE without sending email.",
    )
    args = parser.parse_args()

    if args.hours <= 0:
        parser.error("--hours must be positive")

    api_key = required_env("ECRIME_API_KEY")
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=args.hours)

    events = fetch_events(api_key, start, end)
    csv_data = create_csv(events)
    filename = f"ecrime_new_victims_{end:%Y-%m-%d}.csv"

    if args.dry_run:
        args.dry_run.write_bytes(csv_data)
        print(f"Wrote {len(events)} event(s) to {args.dry_run}")
        return 0

    send_email(
        csv_data=csv_data,
        filename=filename,
        event_count=len(events),
        start=start,
        end=end,
    )
    print(f"Sent {filename} with {len(events)} event(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
