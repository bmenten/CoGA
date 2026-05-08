from datetime import datetime, timezone
import json

from backend.app.services.small_variant_review_pg import _json_payload, _postgres_bigint_or_none


def test_json_payload_serializes_datetime_values() -> None:
    payload = {
        "review": {
            "updated_by": "analyst@example.com",
            "updated_at": datetime(2026, 4, 28, 12, 30, tzinfo=timezone.utc),
        }
    }

    parsed = json.loads(_json_payload(payload))
    assert parsed["review"]["updated_by"] == "analyst@example.com"
    assert parsed["review"]["updated_at"] == "2026-04-28T12:30:00+00:00"


def test_postgres_bigint_or_none_drops_clickhouse_uint64_overflow() -> None:
    assert _postgres_bigint_or_none(9_223_372_036_854_775_807) == 9_223_372_036_854_775_807
    assert _postgres_bigint_or_none(17_002_747_318_664_912_484) is None
