"""
Shared utilities, models, and constants for GroupIQ Lambda functions.
"""
import json
import uuid
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


def get_dynamodb_resource():
    return boto3.resource("dynamodb")


def get_bedrock_client():
    return boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def get_s3_client():
    return boto3.client("s3")


def get_ses_client():
    return boto3.client("ses")


def get_sns_client():
    return boto3.client("sns")


def generate_booking_id() -> str:
    return f"GRP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


class DecimalEncoder(json.JSONEncoder):
    """Handle DynamoDB Decimal types in JSON serialization."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


# Booking status constants
class BookingStatus:
    INQUIRY_RECEIVED = "INQUIRY_RECEIVED"
    PROPOSAL_GENERATED = "PROPOSAL_GENERATED"
    PROPOSAL_SENT = "PROPOSAL_SENT"
    COUNTER_RECEIVED = "COUNTER_RECEIVED"
    NEGOTIATING = "NEGOTIATING"
    ACCEPTED = "ACCEPTED"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"
    DECLINED = "DECLINED"


# Event type constants
class EventType:
    WEDDING = "wedding"
    CONFERENCE = "conference"
    CORPORATE = "corporate"
    SOCIAL = "social"
    OTHER = "other"
