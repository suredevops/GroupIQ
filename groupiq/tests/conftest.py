"""
Pytest configuration and shared fixtures for GroupIQ tests.
Uses moto to mock all AWS services locally.
"""
import json
import os
import sys
import pytest
import boto3
from moto import mock_aws
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "intake"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "proposal_generator"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "negotiation_agent"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "notification"))


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Set up environment variables for all tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BOOKINGS_TABLE", "groupiq-bookings-test")
    monkeypatch.setenv("PRICING_TABLE", "groupiq-pricing-rules-test")
    monkeypatch.setenv("NEGOTIATIONS_TABLE", "groupiq-negotiations-test")
    monkeypatch.setenv("PROPOSALS_BUCKET", "groupiq-proposals-test")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
    monkeypatch.setenv("MAX_DISCOUNT_PERCENT", "15")
    monkeypatch.setenv("ESCALATION_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:groupiq-escalation-test")
    monkeypatch.setenv("SES_SENDER_EMAIL", "test@groupiq.local")
    monkeypatch.setenv("ENVIRONMENT", "test")


@pytest.fixture
def dynamodb_tables():
    """Create DynamoDB tables for testing."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Bookings table
        dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "event_date", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "event_date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Negotiations table
        dynamodb.create_table(
            TableName="groupiq-negotiations-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "turn_number", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "turn_number", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Pricing rules table
        dynamodb.create_table(
            TableName="groupiq-pricing-rules-test",
            KeySchema=[
                {"AttributeName": "property_id", "KeyType": "HASH"},
                {"AttributeName": "rule_type", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "property_id", "AttributeType": "S"},
                {"AttributeName": "rule_type", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Seed pricing rules
        pricing_table = dynamodb.Table("groupiq-pricing-rules-test")
        pricing_table.put_item(Item={
            "property_id": "MRIOTT-NYC-001",
            "rule_type": "room_rate",
            "base_rate": Decimal("299"),
            "peak_rate": Decimal("399"),
            "floor_rate": Decimal("254"),
        })
        pricing_table.put_item(Item={
            "property_id": "MRIOTT-NYC-001",
            "rule_type": "fnb_pricing",
            "breakfast_per_person": Decimal("45"),
            "lunch_per_person": Decimal("65"),
            "dinner_per_person": Decimal("95"),
        })
        pricing_table.put_item(Item={
            "property_id": "MRIOTT-NYC-001",
            "rule_type": "negotiation_bounds",
            "max_room_discount_percent": Decimal("15"),
            "max_fnb_discount_percent": Decimal("10"),
        })

        yield dynamodb


@pytest.fixture
def s3_bucket():
    """Create S3 bucket for testing."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="groupiq-proposals-test")
        yield s3


@pytest.fixture
def sample_inquiry():
    """Sample group booking inquiry payload."""
    return {
        "contact_name": "Sarah Johnson",
        "contact_email": "sarah@techcorp.com",
        "contact_phone": "+1-555-0142",
        "company_name": "TechCorp Inc.",
        "event_type": "conference",
        "event_date": "2026-09-15",
        "event_end_date": "2026-09-18",
        "num_rooms": 75,
        "num_nights": 3,
        "property_id": "MRIOTT-NYC-001",
        "fnb_required": True,
        "meeting_space_required": True,
        "special_requests": "Keynote ballroom for 500, 4 breakout rooms",
        "budget_indication": "$150,000 - $200,000",
    }


@pytest.fixture
def api_gateway_event(sample_inquiry):
    """Simulate an API Gateway HTTP API event."""
    return {
        "version": "2.0",
        "routeKey": "POST /inquiries",
        "rawPath": "/prod/inquiries",
        "body": json.dumps(sample_inquiry),
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/prod/inquiries",
            },
            "requestId": "test-request-123",
        },
        "headers": {"content-type": "application/json"},
        "pathParameters": {},
    }
