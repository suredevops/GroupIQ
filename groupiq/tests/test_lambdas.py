"""
Unit tests for the GroupIQ Intake Lambda.
"""
import json
import sys
import os
import pytest
import boto3
from moto import mock_aws
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "intake"))


class TestIntakeValidation:
    """Test inquiry validation logic."""

    def test_valid_inquiry_passes(self, sample_inquiry):
        from handler import validate_inquiry
        errors = validate_inquiry(sample_inquiry)
        assert errors == []

    def test_missing_required_fields(self):
        from handler import validate_inquiry
        errors = validate_inquiry({})
        assert len(errors) == 7
        assert any("contact_name" in e for e in errors)
        assert any("num_rooms" in e for e in errors)

    def test_minimum_rooms_check(self):
        from handler import validate_inquiry
        inquiry = {
            "contact_name": "Test",
            "contact_email": "test@test.com",
            "event_type": "wedding",
            "event_date": "2026-10-01",
            "num_rooms": 5,
            "num_nights": 2,
            "property_id": "PROP-001",
        }
        errors = validate_inquiry(inquiry)
        assert any("minimum 10 rooms" in e for e in errors)

    def test_invalid_num_rooms(self):
        from handler import validate_inquiry
        inquiry = {
            "contact_name": "Test",
            "contact_email": "test@test.com",
            "event_type": "wedding",
            "event_date": "2026-10-01",
            "num_rooms": "abc",
            "num_nights": 2,
            "property_id": "PROP-001",
        }
        errors = validate_inquiry(inquiry)
        assert any("valid integer" in e for e in errors)


class TestIntakeLambda:
    """Test the full Lambda handler with mocked DynamoDB."""

    @mock_aws
    def test_new_inquiry_success(self, api_gateway_event, monkeypatch):
        import boto3
        from decimal import Decimal

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create tables
        dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
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

        # Seed pricing
        pricing_table = dynamodb.Table("groupiq-pricing-rules-test")
        pricing_table.put_item(Item={
            "property_id": "MRIOTT-NYC-001",
            "rule_type": "room_rate",
            "base_rate": Decimal("299"),
        })

        from handler import lambda_handler
        response = lambda_handler(api_gateway_event, None)

        assert response["statusCode"] == 201
        body = json.loads(response["body"])
        assert "booking_id" in body
        assert body["booking_id"].startswith("GRP-")
        assert body["status"] == "INQUIRY_RECEIVED"
        assert body["estimated_revenue"] == 299 * 75 * 3

    @mock_aws
    def test_invalid_body_returns_400(self, monkeypatch):
        import boto3

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        event = {
            "body": "not valid json{{{",
            "requestContext": {"http": {"method": "POST"}},
        }

        from handler import lambda_handler
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400

    @mock_aws
    def test_get_booking_not_found(self, monkeypatch):
        import boto3

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "pathParameters": {"bookingId": "GRP-NONEXISTENT"},
        }

        from handler import lambda_handler
        response = lambda_handler(event, None)
        assert response["statusCode"] == 404


class TestProposalGenerator:
    """Test proposal generator with mocked Bedrock."""

    @mock_aws
    def test_proposal_generation_flow(self, monkeypatch):
        import boto3
        from decimal import Decimal
        from unittest.mock import MagicMock, patch
        import importlib

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="groupiq-proposals-test")

        # Insert a booking
        table = dynamodb.Table("groupiq-bookings-test")
        table.put_item(Item={
            "booking_id": "GRP-20260915-TEST0001",
            "version": 1,
            "status": "INQUIRY_RECEIVED",
            "contact_name": "Sarah Johnson",
            "contact_email": "sarah@test.com",
            "event_type": "conference",
            "event_date": "2026-09-15",
            "num_rooms": 75,
            "num_nights": 3,
            "property_id": "MRIOTT-NYC-001",
            "base_room_rate": Decimal("299"),
            "estimated_revenue": Decimal("67275"),
            "pricing_rules": json.dumps({"room_rate": {"base_rate": 299}}),
        })

        mock_proposal = {
            "executive_summary": "Thank you for choosing Marriott for your conference.",
            "room_block": {"tiers": [
                {"name": "Standard", "rate": 269, "commitment": "80% pickup"},
                {"name": "Premium", "rate": 254, "commitment": "90% pickup"},
            ]},
            "fnb_packages": [
                {"name": "Essential", "price_per_person": 85, "description": "Breakfast + lunch"},
                {"name": "Premium", "price_per_person": 150, "description": "Full day package"},
            ],
            "meeting_space": {"ballroom": 8000, "breakout_rooms": 6000},
            "value_adds": ["Complimentary suite upgrade for organizer"],
            "terms": {"cutoff_days": 30, "attrition": "20%"},
            "total_investment": {"min": 150000, "max": 200000, "recommended": 175000},
        }

        mock_bedrock_response = {
            "body": MagicMock(read=MagicMock(return_value=json.dumps({
                "content": [{"text": json.dumps(mock_proposal)}]
            }).encode()))
        }

        # Load the proposal_generator handler with correct module name
        proposal_gen_path = os.path.join(os.path.dirname(__file__), "..", "lambdas", "proposal_generator")
        sys.path.insert(0, proposal_gen_path)
        if "handler" in sys.modules:
            del sys.modules["handler"]
        import importlib
        import handler as proposal_handler_mod
        importlib.reload(proposal_handler_mod)

        with patch.object(proposal_handler_mod, "get_bedrock_client") as mock_get_bedrock:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = mock_bedrock_response
            mock_get_bedrock.return_value = mock_client

            result = proposal_handler_mod.lambda_handler({"booking_id": "GRP-20260915-TEST0001"}, None)

        assert result["status"] == "PROPOSAL_GENERATED"
        assert result["booking_id"] == "GRP-20260915-TEST0001"
        assert "proposal_s3_key" in result


class TestNegotiationAgent:
    """Test negotiation agent with mocked Bedrock."""

    @mock_aws
    def test_accept_within_bounds(self, monkeypatch):
        import boto3
        from decimal import Decimal
        from unittest.mock import MagicMock, patch
        import importlib

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
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

        table = dynamodb.Table("groupiq-bookings-test")
        table.put_item(Item={
            "booking_id": "GRP-20260915-TEST0001",
            "version": 2,
            "status": "PROPOSAL_SENT",
            "contact_name": "Sarah Johnson",
            "contact_email": "sarah@test.com",
            "event_type": "conference",
            "event_date": "2026-09-15",
            "num_rooms": 75,
            "num_nights": 3,
            "base_room_rate": Decimal("299"),
            "estimated_revenue": Decimal("67275"),
            "proposal_summary": {
                "total_recommended": Decimal("175000"),
            },
        })

        mock_negotiation_result = {
            "decision": "ACCEPT",
            "rationale": "Counter-offer is within 10% discount, acceptable.",
            "counter_proposal": None,
            "final_price": 165000,
            "discount_applied_percent": 5.7,
            "message_to_client": "We're happy to accept your proposal at $165,000.",
        }

        mock_bedrock_response = {
            "body": MagicMock(read=MagicMock(return_value=json.dumps({
                "content": [{"text": json.dumps(mock_negotiation_result)}]
            }).encode()))
        }

        # Load the negotiation_agent handler with correct module
        neg_agent_path = os.path.join(os.path.dirname(__file__), "..", "lambdas", "negotiation_agent")
        sys.path.insert(0, neg_agent_path)
        if "handler" in sys.modules:
            del sys.modules["handler"]
        import handler as negotiation_handler_mod
        importlib.reload(negotiation_handler_mod)

        with patch.object(negotiation_handler_mod, "get_bedrock_client") as mock_get_bedrock:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = mock_bedrock_response
            mock_get_bedrock.return_value = mock_client

            result = negotiation_handler_mod.lambda_handler({
                "booking_id": "GRP-20260915-TEST0001",
                "counter_offer": {
                    "requested_price": 165000,
                    "requested_room_rate": 270,
                    "message": "Can we do $165K?",
                },
            }, None)

        assert result["decision"] == "ACCEPT"
        assert result["status"] == "ACCEPTED"
        assert "message_to_client" in result


class TestReminder:
    """Tests for the Reminder Lambda."""

    @mock_aws
    def test_no_upcoming_bookings(self, sample_inquiry):
        """Should return 0 reminders when no bookings match the target date."""
        from importlib import import_module
        sys.modules.pop("handler", None)
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "reminder"))
        import handler as reminder_handler

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        result = reminder_handler.lambda_handler({"target_date": "2099-01-01"}, None)
        body = json.loads(result["body"])
        assert result["statusCode"] == 200
        assert body["reminders_sent"] == 0
        assert body["target_date"] == "2099-01-01"

    @mock_aws
    def test_sends_reminders_for_upcoming_bookings(self, sample_inquiry):
        """Should find bookings and send reminders for matching event date."""
        sys.modules.pop("handler", None)
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "reminder"))
        import handler as reminder_handler

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="groupiq-bookings-test",
            KeySchema=[
                {"AttributeName": "booking_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "booking_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        table.put_item(Item={
            "booking_id": "GRP-20260518-TEST0001",
            "version": 1,
            "status": "ACCEPTED",
            "contact_name": "Test Person",
            "contact_email": "test@example.com",
            "event_type": "conference",
            "event_date": "2026-05-20",
            "num_rooms": 50,
            "num_nights": 3,
            "property_id": "MRIOTT-NYC-001",
        })

        ses = boto3.client("ses", region_name="us-east-1")
        ses.verify_email_identity(EmailAddress="test@groupiq.local")

        sns = boto3.client("sns", region_name="us-east-1")
        sns.create_topic(Name="test-topic")

        result = reminder_handler.lambda_handler({"target_date": "2026-05-20"}, None)
        body = json.loads(result["body"])
        assert result["statusCode"] == 200
        assert body["reminders_sent"] == 1
        assert body["details"][0]["booking_id"] == "GRP-20260518-TEST0001"
        assert body["total_rooms"] == 50
