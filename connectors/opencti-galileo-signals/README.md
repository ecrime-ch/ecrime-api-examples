# Galileo Signals OpenCTI Connector

External-import connector for feeding Galileo Signals observed-domain indicators
into OpenCTI.

The connector polls the Galileo observed-domains feed, maps each row to STIX
2.1 objects, and submits a STIX bundle to OpenCTI through `pycti`.

## What It Imports

For each Galileo observed domain the connector creates:

- `domain-name` cyber-observable
- `indicator` with a STIX pattern such as `[domain-name:value = 'example.com']`
- `relationship` from the indicator to the observable
- one batch `report` containing the imported indicators
- source identity `Galileo Signals`
- configurable TLP marking

Galileo fields such as `first_seen`, `last_seen`, `observations`,
`source_counts`, `confidence`, `confidence_reasons`, registration metadata, and
sample context are preserved as STIX custom properties.

## Install With Docker Compose

Copy the sample environment file and fill in your OpenCTI and Galileo values:

```bash
cd connectors/opencti-galileo-signals
cp .env.example .env
```

Edit `.env`, then start the connector:

```bash
docker compose up -d --build
```

For a one-shot test without sending data to OpenCTI:

```bash
GALILEO_OPENCTI_DRY_RUN=true docker compose run --rm galileo-opencti
```

## Environment Variables

Required:

- `OPENCTI_URL`
- `OPENCTI_TOKEN`
- `GALILEO_API_TOKEN`

Common:

- `GALILEO_FEED_URL`: defaults to
  `https://galileosignals.com/api/observed_domains`
- `GALILEO_AUTH_HEADER`: defaults to `Authorization`
- `GALILEO_AUTH_SCHEME`: defaults to `Bearer`; set empty for raw token headers
- `GALILEO_FIRST_SEEN`: defaults to `24hours`
- `GALILEO_CONFIDENCE`: defaults to `medium`; set empty for all confidence levels
- `GALILEO_SOURCE`: optional `sender`, `extracted`, or `redirect`
- `GALILEO_DOMAIN_AGE`: optional `24hours`, `7days`, or `30days`
- `GALILEO_INCLUDE_CONTEXT`: defaults to `false`
- `GALILEO_SIZE`: defaults to `500`
- `GALILEO_INTERVAL`: poll interval in seconds, default `3600`
- `GALILEO_RUN_ONCE`: run one collection and exit, default `false`
- `GALILEO_OPENCTI_DRY_RUN`: fetch and map only, default `false`
- `GALILEO_VERIFY_SSL`: defaults to `true`
- `GALILEO_TLP`: defaults to `TLP:AMBER`
- `GALILEO_CREATE_REPORTS`: defaults to `true`
- `CONNECTOR_LOG_LEVEL`: defaults to `info`

## Native Python Install

```bash
cd connectors/opencti-galileo-signals
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
opencti-galileo-signals
```

## Notes

- The connector deduplicates STIX IDs deterministically from observable values
  and Galileo timestamps.
- OpenCTI connector state stores the most recent successful run timestamp and is
  used for auditability. The Galileo feed itself is queried by time-window
  parameters such as `first_seen=24hours`.
- Use `GALILEO_OPENCTI_DRY_RUN=true` before enabling writes in a shared OpenCTI
  test instance.
