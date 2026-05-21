"""
Test the common utilities module.
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "common"))

from utils import (
    generate_booking_id,
    utc_now_iso,
    build_response,
    BookingStatus,
    DecimalEncoder,
)
from decimal import Decimal


class TestBookingIdGeneration:
    def test_format(self):
        bid = generate_booking_id()
        assert bid.startswith("GRP-")
        parts = bid.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 8  # hex suffix

    def test_uniqueness(self):
        ids = {generate_booking_id() for _ in range(100)}
        assert len(ids) == 100


class TestBuildResponse:
    def test_success_response(self):
        resp = build_response(200, {"key": "value"})
        assert resp["statusCode"] == 200
        assert resp["headers"]["Content-Type"] == "application/json"
        body = json.loads(resp["body"])
        assert body["key"] == "value"

    def test_cors_header(self):
        resp = build_response(200, {})
        assert resp["headers"]["Access-Control-Allow-Origin"] == "*"


class TestDecimalEncoder:
    def test_decimal_serialization(self):
        data = {"price": Decimal("299.99"), "rooms": 10}
        result = json.dumps(data, cls=DecimalEncoder)
        parsed = json.loads(result)
        assert parsed["price"] == 299.99
        assert parsed["rooms"] == 10


class TestBookingStatus:
    def test_all_statuses_defined(self):
        assert BookingStatus.INQUIRY_RECEIVED == "INQUIRY_RECEIVED"
        assert BookingStatus.PROPOSAL_GENERATED == "PROPOSAL_GENERATED"
        assert BookingStatus.NEGOTIATING == "NEGOTIATING"
        assert BookingStatus.ACCEPTED == "ACCEPTED"
        assert BookingStatus.ESCALATED == "ESCALATED"


class TestUtcNow:
    def test_iso_format(self):
        ts = utc_now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")
