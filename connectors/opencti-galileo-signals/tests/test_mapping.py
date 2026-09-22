import json

from opencti_galileo_signals.connector import parse_feed, stix_bundle_from_items


def test_parse_json_items_feed():
    payload = json.dumps(
        {
            "items": [
                {
                    "domain": "Example.COM",
                    "first_seen": "2026-09-22T12:00:00Z",
                    "confidence": "high",
                    "sources": ["sender", "redirect"],
                }
            ]
        }
    )

    rows = parse_feed(payload, "application/json")

    assert rows[0]["domain"] == "Example.COM"


def test_stix_bundle_contains_domain_indicator_and_relationship():
    rows = [
        {
            "domain": "Example.COM",
            "first_seen": "2026-09-22T12:00:00Z",
            "last_seen": "2026-09-22T13:00:00Z",
            "observations": 3,
            "confidence": "high",
            "sources": ["sender"],
        }
    ]

    bundle = stix_bundle_from_items(
        rows,
        connector_name="Galileo Signals",
        feed_url="https://galileosignals.com/api/observed_domains",
        tlp="TLP:AMBER",
        create_report=True,
    )

    types = {obj["type"] for obj in json.loads(bundle.serialize())["objects"]}
    assert {"identity", "domain-name", "indicator", "relationship", "report"} <= types


def test_stix_bundle_contains_email_evidence_when_context_is_present():
    rows = [
        {
            "domain": "hyundai.myvehicle-email.com",
            "first_seen": "2026-09-22T12:00:00Z",
            "last_seen": "2026-09-22T13:00:00Z",
            "confidence": "medium",
            "sources": ["sender_domain", "extracted_domain", "redirect_chain_domain"],
            "sample_emails": [
                {
                    "id": "20260922120000.ABC123.example",
                    "from_email": "alerts@hyundai.myvehicle-email.com",
                    "subject": "Hyundai service notice",
                    "date": "2026-09-22T12:00:00Z",
                },
                {
                    "id": "20260922123000.DEF456.example",
                    "from_email": "alerts@hyundai.myvehicle-email.com",
                    "subject": "Vehicle account update",
                    "observed_at": "2026-09-22T12:30:00Z",
                },
            ],
        }
    ]

    bundle = stix_bundle_from_items(
        rows,
        connector_name="Galileo Signals",
        feed_url="https://galileosignals.com/api/observed_domains?include=context",
        email_detail_base_url="https://galileosignals.com/email",
        tlp="TLP:AMBER",
        create_report=True,
    )

    objects = json.loads(bundle.serialize())["objects"]
    email_messages = [obj for obj in objects if obj["type"] == "email-message"]
    email_addresses = [obj for obj in objects if obj["type"] == "email-addr"]
    assert len(email_messages) == 2
    assert len(email_addresses) == 1
    assert email_messages[0]["x_galileo_email_id"] == "20260922120000.ABC123.example"
    assert email_messages[0]["subject"] == "Hyundai service notice"
    assert email_messages[0]["from_ref"] == email_addresses[0]["id"]
    assert "Galileo Signals sample email evidence" in email_messages[0]["body"]
    assert email_messages[0]["external_references"][0]["url"].endswith(
        "/20260922120000.ABC123.example"
    )

    relationships = [obj for obj in objects if obj["type"] == "relationship"]
    email_ids = {email["id"] for email in email_messages}
    assert any(
        rel["relationship_type"] == "based-on" and rel["target_ref"] in email_ids
        for rel in relationships
    )
    assert any(
        rel["relationship_type"] == "related-to" and rel["source_ref"] in email_ids
        for rel in relationships
    )


def test_sparse_row_still_maps_to_valid_stix():
    bundle = stix_bundle_from_items(
        [{"domain": "example.org"}],
        connector_name="Galileo Signals",
        feed_url="https://galileosignals.com/api/observed_domains",
        tlp="TLP:AMBER",
        create_report=False,
    )

    objects = json.loads(bundle.serialize())["objects"]
    assert any(obj["type"] == "indicator" for obj in objects)


def test_equal_first_and_last_seen_omits_invalid_until():
    bundle = stix_bundle_from_items(
        [
            {
                "domain": "same-time.example",
                "first_seen": "2026-09-22T12:00:00Z",
                "last_seen": "2026-09-22T12:00:00Z",
            }
        ],
        connector_name="Galileo Signals",
        feed_url="https://galileosignals.com/api/observed_domains",
        tlp="TLP:AMBER",
        create_report=False,
    )

    indicators = [
        obj for obj in json.loads(bundle.serialize())["objects"] if obj["type"] == "indicator"
    ]
    assert "valid_until" not in indicators[0]
