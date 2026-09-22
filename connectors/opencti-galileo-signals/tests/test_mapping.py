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
