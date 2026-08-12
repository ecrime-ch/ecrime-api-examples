# eCrime.ch API Examples

Small, practical examples for working with the
[eCrime.ch API](https://ecrime.ch/help/api/).

These scripts are intended as starting points for customers and integration
teams. They demonstrate common API workflows but are not production services.

## Support Policy

**No support is available for scripts published in this repository.**

The examples are provided as-is, without warranty. Users are responsible for
reviewing, testing, securing, adapting, and operating them in their own
environment. eCrime.ch support does not cover installation, customization,
SMTP configuration, scheduling, third-party dependencies, or troubleshooting
of these scripts.

For questions about the eCrime.ch API itself, consult the official
documentation and your applicable eCrime.ch support agreement.

## Requirements

- Python 3.10 or newer
- An eCrime.ch API key
- `requests`

Install the Python dependency:

```bash
python3 -m pip install -r requirements.txt
```

Keep API keys and other credentials in environment variables. Never commit
credentials to source control.

## Available Examples

### Publicly traded company events

[`scripts/list_publicly_traded_events.py`](scripts/list_publicly_traded_events.py)
lists events where the API response includes a non-empty `stock_symbol`.
This is useful as a starting point for polling or alerting on publicly traded
victims.

Look back over the previous 24 hours:

```bash
python3 scripts/list_publicly_traded_events.py
```

Write CSV for a fixed window:

```bash
python3 scripts/list_publicly_traded_events.py \
  --from 2026-08-01 \
  --to 2026-08-12 \
  --format csv > publicly_traded_events.csv
```

### Daily CSV report by email

[`scripts/ecrime_daily_csv_email.py`](scripts/ecrime_daily_csv_email.py)
retrieves newly listed victims from the previous 24 hours, removes duplicate
records, generates an Excel-friendly CSV file, and sends it through a
customer-provided SMTP server.

Copy the example environment file and enter your own settings:

```bash
cp .env.example .env
```

Load the settings into your shell:

```bash
set -a
. ./.env
set +a
```

Test CSV generation without sending email:

```bash
python3 scripts/ecrime_daily_csv_email.py --dry-run report.csv
```

Send the report:

```bash
python3 scripts/ecrime_daily_csv_email.py
```

Example cron entry for daily delivery at 08:00:

```text
0 8 * * * /usr/bin/python3 /path/to/ecrime-api-examples/scripts/ecrime_daily_csv_email.py
```

### Regional monthly report generator

[`scripts/generate_regional_monthly_report.py`](scripts/generate_regional_monthly_report.py)
creates a portable monthly report for one country or a comma-separated group
of countries.

The generator has no fixed local paths and does not require Chromium. It
creates:

- `report.html`
- `report.md`
- `summary.json`
- `incidents.csv`
- three SVG charts

Generate a country report:

```bash
python3 scripts/generate_regional_monthly_report.py \
  --month 2026-05 \
  --country Switzerland
```

Generate a regional report:

```bash
python3 scripts/generate_regional_monthly_report.py \
  --month 2026-05 \
  --countries "Germany,Austria,Switzerland" \
  --scope-name "DACH"
```

Results are written below `output/<scope>/<month>/` unless `--output-dir` is
specified.

## Security

- Do not place API keys, SMTP passwords, or exported data in this repository.
- Restrict access to any environment file containing credentials.
- Review CSV contents and recipients before enabling scheduled delivery.
- Treat leak URLs and customer-specific reporting output according to your
  organization’s handling requirements.
