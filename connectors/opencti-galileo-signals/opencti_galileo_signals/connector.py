"""Import Galileo Signals observed domains into OpenCTI."""

from __future__ import annotations

import csv
import argparse
import io
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

import requests
from stix2 import Bundle, DomainName, EmailAddress, EmailMessage, ExternalReference, Identity, Indicator, Relationship, Report

try:
    from pycti import OpenCTIConnectorHelper
except ImportError:  # pragma: no cover - exercised in connector runtime
    OpenCTIConnectorHelper = None


LOGGER = logging.getLogger("galileo-opencti")
STIX_NAMESPACE = uuid.UUID("7c8a667f-4b79-42f9-8d8b-9a6e7c7c9de1")
CONFIDENCE_SCORES = {"low": 35, "medium": 65, "high": 90}
TLP_MARKINGS = {
    "TLP:CLEAR": "marking-definition--94868c89-83c2-464b-929b-a1a8aa3c8487",
    "TLP:WHITE": "marking-definition--94868c89-83c2-464b-929b-a1a8aa3c8487",
    "TLP:GREEN": "marking-definition--34098fce-860f-48ae-8e50-ebd3cc5e41da",
    "TLP:AMBER": "marking-definition--f88d31f6-486f-44da-b317-01333bde0b82",
    "TLP:AMBER+STRICT": "marking-definition--826578e1-40ad-459f-bc73-ede076f81f37",
    "TLP:RED": "marking-definition--5e57c739-391a-4eb3-b6be-7d15ca92d5ed",
}


@dataclass(frozen=True)
class ConnectorConfig:
    opencti_url: str
    opencti_token: str
    connector_id: str
    connector_name: str
    connector_scope: str
    connector_log_level: str
    feed_url: str
    api_token: str
    auth_header: str
    auth_scheme: str
    first_seen: str
    confidence: str
    source: str
    domain_age: str
    include_context: bool
    use_rollup: bool
    email_detail_base_url: str
    size: int
    scan_size: int
    interval: int
    run_once: bool
    dry_run: bool
    verify_ssl: bool
    tlp: str
    create_reports: bool
    request_timeout: int

    @classmethod
    def from_env(cls) -> "ConnectorConfig":
        return cls(
            opencti_url=env("OPENCTI_URL", required=not env_bool("GALILEO_OPENCTI_DRY_RUN", False)),
            opencti_token=env("OPENCTI_TOKEN", required=not env_bool("GALILEO_OPENCTI_DRY_RUN", False)),
            connector_id=env("CONNECTOR_ID", "galileo-signals-observed-domains"),
            connector_name=env("CONNECTOR_NAME", "Galileo Signals"),
            connector_scope=env("CONNECTOR_SCOPE", "galileo,galileo-signals,domain-name,indicator"),
            connector_log_level=env("CONNECTOR_LOG_LEVEL", "info"),
            feed_url=env("GALILEO_FEED_URL", "https://galileosignals.com/api/observed_domains"),
            api_token=env("GALILEO_API_TOKEN", required=True),
            auth_header=env("GALILEO_AUTH_HEADER", "Authorization"),
            auth_scheme=env("GALILEO_AUTH_SCHEME", "Bearer"),
            first_seen=env("GALILEO_FIRST_SEEN", "24hours"),
            confidence=env("GALILEO_CONFIDENCE", "medium"),
            source=env("GALILEO_SOURCE", ""),
            domain_age=env("GALILEO_DOMAIN_AGE", ""),
            include_context=env_bool("GALILEO_INCLUDE_CONTEXT", False),
            use_rollup=env_bool("GALILEO_USE_ROLLUP", True),
            email_detail_base_url=env("GALILEO_EMAIL_DETAIL_BASE_URL", "https://galileosignals.com/email"),
            size=env_int("GALILEO_SIZE", 500),
            scan_size=env_int("GALILEO_SCAN_SIZE", 0, allow_zero=True),
            interval=env_int("GALILEO_INTERVAL", 3600),
            run_once=env_bool("GALILEO_RUN_ONCE", False),
            dry_run=env_bool("GALILEO_OPENCTI_DRY_RUN", False),
            verify_ssl=env_bool("GALILEO_VERIFY_SSL", True),
            tlp=env("GALILEO_TLP", "TLP:AMBER").upper(),
            create_reports=env_bool("GALILEO_CREATE_REPORTS", True),
            request_timeout=env_int("GALILEO_REQUEST_TIMEOUT", 60),
        )

    def pycti_config(self) -> dict[str, Any]:
        return {
            "opencti": {"url": self.opencti_url, "token": self.opencti_token},
            "connector": {
                "id": self.connector_id,
                "type": "EXTERNAL_IMPORT",
                "name": self.connector_name,
                "scope": self.connector_scope,
                "log_level": self.connector_log_level,
                "duration_period": os.getenv("CONNECTOR_DURATION_PERIOD", "PT1H"),
            },
        }


def env(name: str, default: str = "", *, required: bool = False) -> str:
    value = os.getenv(name, default).strip()
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, allow_zero: bool = False) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise SystemExit(f"{name} must be positive")
    return parsed


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def build_feed_url(config: ConnectorConfig) -> str:
    params: dict[str, str] = {"format": "json", "size": str(config.size)}
    if config.first_seen:
        params["first_seen"] = config.first_seen
    if config.confidence:
        params["confidence"] = config.confidence
    if config.source:
        params["source"] = config.source
    if config.domain_age:
        params["domain_age"] = config.domain_age
    if config.include_context:
        params["include"] = "context"
    if not config.use_rollup:
        params["rollup"] = "false"
    if config.scan_size > 0:
        params["scan_size"] = str(config.scan_size)
    separator = "&" if "?" in config.feed_url else "?"
    return f"{config.feed_url}{separator}{urlencode(params)}"


def auth_headers(config: ConnectorConfig) -> dict[str, str]:
    token = config.api_token
    if config.auth_scheme:
        token = f"{config.auth_scheme} {token}"
    return {
        config.auth_header: token,
        "Accept": "application/json, text/csv;q=0.9, application/x-ndjson;q=0.8",
        "User-Agent": "galileo-signals-opencti-connector/1.0",
    }


def fetch_feed(config: ConnectorConfig) -> list[dict[str, Any]]:
    url = build_feed_url(config)
    LOGGER.info("Fetching Galileo observed domains feed from %s", url)
    response = requests.get(
        url,
        headers=auth_headers(config),
        timeout=config.request_timeout,
        verify=config.verify_ssl,
    )
    response.raise_for_status()
    return parse_feed(response.text, response.headers.get("content-type", ""))


def parse_feed(body: str, content_type: str = "") -> list[dict[str, Any]]:
    content_type = content_type.lower()
    stripped = body.lstrip()
    if "text/csv" in content_type:
        return list(csv.DictReader(io.StringIO(body)))
    if "ndjson" in content_type or looks_like_jsonl(stripped):
        return [json.loads(line) for line in body.splitlines() if line.strip()]
    if stripped.startswith("{") or stripped.startswith("["):
        payload = json.loads(body)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        for key in ("items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        raise ValueError("JSON feed did not contain an items, data, or results list")
    if "," in body.splitlines()[0]:
        return list(csv.DictReader(io.StringIO(body)))
    return [{"domain": line.strip()} for line in body.splitlines() if line.strip()]


def looks_like_jsonl(body: str) -> bool:
    if "\n" not in body:
        return False
    first = body.splitlines()[0].strip()
    return first.startswith("{") and first.endswith("}")


def make_stix_id(stix_type: str, *parts: object) -> str:
    material = "|".join(str(part).strip().lower() for part in parts if part is not None)
    return f"{stix_type}--{uuid.uuid5(STIX_NAMESPACE, material)}"


def normalize_domain(value: object) -> str:
    domain = str(value or "").strip().lower().strip(".")
    if "/" in domain:
        domain = domain.split("/", 1)[0]
    if "@" in domain:
        domain = domain.rsplit("@", 1)[-1]
    return domain


def parse_datetime(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), timezone.utc).isoformat().replace("+00:00", "Z")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def split_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [f"{key}:{val}" for key, val in value.items() if val not in (None, "")]
    return [part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()]


def split_context_values(value: object, *, split_commas: bool = False) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [f"{key}:{val}" for key, val in value.items() if val not in (None, "")]
    text = str(value).strip()
    separators = ["|", ";"]
    if split_commas:
        separators.append(",")
    for separator in separators:
        text = text.replace(separator, "\n")
    return [part.strip() for part in text.splitlines() if part.strip()]


def email_detail_url(base_url: str, email_id: str) -> str:
    base = base_url.strip() or "https://galileosignals.com/email"
    encoded_id = quote(email_id, safe="")
    if "{id}" in base:
        return base.replace("{id}", encoded_id)
    return f"{base.rstrip('/')}/{encoded_id}"


def normalize_email(value: object) -> str:
    email = str(value or "").strip().lower().strip("<>")
    if "@" not in email:
        return ""
    return email


def sample_email_records(item: dict[str, Any]) -> list[dict[str, str]]:
    structured = item.get("sample_emails")
    records: list[dict[str, str]] = []
    if isinstance(structured, list):
        for entry in structured:
            if not isinstance(entry, dict):
                continue
            email_id = str(entry.get("id") or entry.get("email_id") or "").strip()
            from_email = normalize_email(entry.get("from_email") or entry.get("from"))
            subject = str(entry.get("subject") or "").strip()
            if not email_id or not from_email or not subject:
                continue
            records.append(
                {
                    "id": email_id,
                    "from_email": from_email,
                    "subject": subject,
                    "date": str(entry.get("date") or "").strip(),
                    "observed_at": str(entry.get("observed_at") or "").strip(),
                }
            )
            if len(records) >= 5:
                return records
    return records


def int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def confidence_score(value: object) -> int:
    if value in (None, ""):
        return CONFIDENCE_SCORES["medium"]
    text = str(value).strip().lower()
    if text in CONFIDENCE_SCORES:
        return CONFIDENCE_SCORES[text]
    parsed = int_or_none(value)
    if parsed is None:
        return CONFIDENCE_SCORES["medium"]
    return max(0, min(parsed, 100))


def custom_properties(item: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    passthrough = {
        "last_seen": "x_galileo_last_seen",
        "observations": "x_galileo_observations",
        "source_counts": "x_galileo_source_counts",
        "sources": "x_galileo_sources",
        "registration_date": "x_galileo_registration_date",
        "registered_domain": "x_galileo_registered_domain",
        "registrar": "x_galileo_registrar",
        "domain_age_days": "x_galileo_domain_age_days",
        "domain_age_bucket": "x_galileo_domain_age_bucket",
        "confidence": "x_galileo_confidence",
        "confidence_reasons": "x_galileo_confidence_reasons",
        "top_10000": "x_galileo_top_10000",
        "blocklist_hits": "x_galileo_blocklist_hits",
        "virus_hits": "x_galileo_virus_hits",
        "max_phishing_score": "x_galileo_max_phishing_score",
        "max_brand_impersonation_confidence": "x_galileo_max_brand_impersonation_confidence",
        "sample_email_ids": "x_galileo_sample_email_ids",
        "last_subjects": "x_galileo_last_subjects",
        "updated_at": "x_galileo_updated_at",
    }
    for source, target in passthrough.items():
        value = item.get(source)
        if value not in (None, "", [], {}):
            properties[target] = value
    return properties


def stix_bundle_from_items(
    items: list[dict[str, Any]],
    *,
    connector_name: str,
    feed_url: str,
    email_detail_base_url: str = "https://galileosignals.com/email",
    tlp: str,
    create_report: bool,
) -> Bundle:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_identity = Identity(
        id=make_stix_id("identity", connector_name),
        name=connector_name,
        identity_class="organization",
    )
    objects: list[Any] = []
    object_ids: set[str] = set()
    marking_id = TLP_MARKINGS.get(tlp)
    object_marking_refs = [marking_id] if marking_id else []
    report_refs: list[str] = []

    def append_once(stix_object: Any) -> None:
        object_id = stix_object.id
        if object_id in object_ids:
            return
        objects.append(stix_object)
        object_ids.add(object_id)

    append_once(source_identity)

    for item in items:
        domain = normalize_domain(
            item.get("domain")
            or item.get("value")
            or item.get("observable_value")
            or item.get("indicator")
        )
        if not domain or "." not in domain:
            continue

        first_seen = parse_datetime(item.get("first_seen")) or now
        last_seen = parse_datetime(item.get("last_seen"))
        labels = ["galileo-signals", "phishing", "observed-domain"]
        labels.extend(f"galileo:{value.lower()}" for value in split_list(item.get("sources")))
        if item.get("confidence"):
            labels.append(f"confidence:{str(item['confidence']).strip().lower()}")

        observable = DomainName(
            id=make_stix_id("domain-name", domain),
            value=domain,
            object_marking_refs=object_marking_refs,
            allow_custom=True,
            **custom_properties(item),
        )
        indicator_kwargs: dict[str, Any] = {
            "id": make_stix_id("indicator", "domain-name", domain),
            "created_by_ref": source_identity.id,
            "name": f"Galileo observed domain: {domain}",
            "description": "Domain observed by Galileo Signals in spam-trap email telemetry.",
            "pattern": f"[domain-name:value = '{domain}']",
            "pattern_type": "stix",
            "valid_from": first_seen,
            "confidence": confidence_score(item.get("confidence")),
            "labels": sorted(set(labels)),
            "external_references": [
                ExternalReference(
                    source_name="Galileo Signals observed domains feed",
                    url=feed_url,
                    external_id=domain,
                )
            ],
            "object_marking_refs": object_marking_refs,
            "allow_custom": True,
            **custom_properties(item),
        }
        if last_seen is not None and last_seen > first_seen:
            indicator_kwargs["valid_until"] = last_seen
        indicator = Indicator(**indicator_kwargs)
        relationship = Relationship(
            id=make_stix_id("relationship", indicator.id, "based-on", observable.id),
            relationship_type="based-on",
            source_ref=indicator.id,
            target_ref=observable.id,
            created_by_ref=source_identity.id,
            object_marking_refs=object_marking_refs,
        )
        append_once(observable)
        append_once(indicator)
        append_once(relationship)
        report_refs.append(indicator.id)

        for sample in sample_email_records(item):
            email_id = sample["id"]
            detail_url = email_detail_url(email_detail_base_url, email_id)
            from_address = EmailAddress(
                id=make_stix_id("email-addr", sample["from_email"]),
                value=sample["from_email"],
                object_marking_refs=object_marking_refs,
            )
            email_kwargs: dict[str, Any] = {
                "id": make_stix_id("email-message", "galileo", email_id),
                "is_multipart": False,
                "from_ref": from_address.id,
                "subject": sample["subject"],
                "body": (
                    f"Galileo Signals sample email evidence for observed domain {domain}.\n\n"
                    f"Open the Galileo email detail: {detail_url}"
                ),
                "external_references": [
                    ExternalReference(
                        source_name="Galileo Signals email detail",
                        url=detail_url,
                        external_id=email_id,
                    )
                ],
                "object_marking_refs": object_marking_refs,
                "allow_custom": True,
                "x_galileo_email_id": email_id,
                "x_galileo_observed_domain": domain,
                "x_galileo_domain_sources": split_list(item.get("sources")),
            }
            email_date = parse_datetime(sample.get("date"))
            observed_at = parse_datetime(sample.get("observed_at"))
            if email_date is not None:
                email_kwargs["date"] = email_date
            if observed_at is not None:
                email_kwargs["x_galileo_observed_at"] = observed_at
            email_message = EmailMessage(**email_kwargs)
            email_indicator_relationship = Relationship(
                id=make_stix_id("relationship", indicator.id, "based-on", email_message.id),
                relationship_type="based-on",
                source_ref=indicator.id,
                target_ref=email_message.id,
                created_by_ref=source_identity.id,
                object_marking_refs=object_marking_refs,
            )
            email_domain_relationship = Relationship(
                id=make_stix_id("relationship", email_message.id, "related-to", observable.id),
                relationship_type="related-to",
                source_ref=email_message.id,
                target_ref=observable.id,
                created_by_ref=source_identity.id,
                object_marking_refs=object_marking_refs,
            )
            append_once(from_address)
            append_once(email_message)
            append_once(email_indicator_relationship)
            append_once(email_domain_relationship)
            report_refs.append(email_message.id)

    if create_report and report_refs:
        objects.append(
            Report(
                id=make_stix_id("report", connector_name, now),
                created_by_ref=source_identity.id,
                name=f"Galileo Signals observed domains import {now}",
                description="Automated Galileo Signals observed-domain import.",
                published=now,
                report_types=["threat-report"],
                object_refs=sorted(set(report_refs)),
                labels=["galileo-signals", "observed-domains"],
                external_references=[
                    ExternalReference(source_name="Galileo Signals observed domains feed", url=feed_url)
                ],
                object_marking_refs=object_marking_refs,
            )
        )

    return Bundle(objects=objects, allow_custom=True)


class GalileoOpenCTIConnector:
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config
        self.helper = None
        if not config.dry_run:
            if OpenCTIConnectorHelper is None:
                raise SystemExit("pycti is required unless GALILEO_OPENCTI_DRY_RUN=true")
            self.helper = OpenCTIConnectorHelper(config.pycti_config())

    def run(self) -> None:
        while True:
            self.run_once()
            if self.config.run_once:
                return
            time.sleep(self.config.interval)

    def run_once(self) -> None:
        work_id = None
        if self.helper is not None:
            work_id = self.helper.api.work.initiate_work(
                self.helper.connect_id,
                "Galileo Signals observed-domain import",
            )
        try:
            items = fetch_feed(self.config)
            bundle = stix_bundle_from_items(
                items,
                connector_name=self.config.connector_name,
                feed_url=build_feed_url(self.config),
                email_detail_base_url=self.config.email_detail_base_url,
                tlp=self.config.tlp,
                create_report=self.config.create_reports,
            )
            LOGGER.info("Mapped %s Galileo rows into %s STIX objects", len(items), len(bundle.objects))
            if self.config.dry_run:
                print(bundle.serialize(pretty=True))
                return
            assert self.helper is not None
            self.helper.send_stix2_bundle(bundle.serialize())
            self.helper.set_state(
                {
                    "last_run": datetime.now(timezone.utc).isoformat(),
                    "last_item_count": len(items),
                    "last_stix_object_count": len(bundle.objects),
                }
            )
            self.helper.api.work.to_processed(work_id, f"Imported {len(items)} Galileo rows")
        except Exception as exc:
            if self.helper is not None and work_id is not None:
                self.helper.api.work.to_processed(work_id, f"Galileo import failed: {exc}", in_error=True)
            raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print STIX without sending to OpenCTI.")
    parser.add_argument("--run-once", action="store_true", help="Run one import cycle and exit.")
    parser.add_argument("--version", action="version", version="opencti-galileo-signals 0.1.0")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        os.environ["GALILEO_OPENCTI_DRY_RUN"] = "true"
    config = ConnectorConfig.from_env()
    if args.run_once or args.dry_run:
        config = replace(config, run_once=True, dry_run=config.dry_run or args.dry_run)
    configure_logging(config.connector_log_level)
    try:
        GalileoOpenCTIConnector(config).run()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        LOGGER.exception("Connector failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
