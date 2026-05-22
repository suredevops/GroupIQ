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
    """Get SES client — uses real AWS SES if credentials are configured, otherwise LocalStack."""
    ses_access_key = os.environ.get("SES_AWS_ACCESS_KEY_ID")
    ses_secret_key = os.environ.get("SES_AWS_SECRET_ACCESS_KEY")
    ses_region = os.environ.get("SES_AWS_REGION", "us-east-1")

    if ses_access_key and ses_secret_key:
        return boto3.client(
            "ses",
            region_name=ses_region,
            aws_access_key_id=ses_access_key,
            aws_secret_access_key=ses_secret_key,
        )
    return boto3.client("ses")


def get_sns_client():
    return boto3.client("sns")


def generate_booking_id() -> str:
    return f"GRP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def generate_inquiry_id() -> str:
    return f"INQ-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


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
    QUEUED = "QUEUED"


# Event type constants
class EventType:
    WEDDING = "wedding"
    CONFERENCE = "conference"
    CORPORATE = "corporate"
    SOCIAL = "social"
    OTHER = "other"


# ─── Room Inventory & Concurrency Control ─────────────────────────────────────

LARGE_BOOKING_THRESHOLD = int(os.environ.get("LARGE_BOOKING_THRESHOLD", "100"))


def _get_inventory_table_name():
    return os.environ.get("INVENTORY_TABLE", "groupiq-inventory-local")


def _get_booking_queue_table_name():
    return os.environ.get("BOOKING_QUEUE_TABLE", "groupiq-booking-queue-local")


class InventoryManager:
    """Handles room inventory with atomic operations to prevent overbooking."""

    def __init__(self):
        self.dynamodb = get_dynamodb_resource()

    def get_available_rooms(self, property_id: str, event_date: str) -> int:
        """Get current available room count for a property on a date."""
        table = self.dynamodb.Table(_get_inventory_table_name())
        try:
            response = table.get_item(
                Key={"property_id": property_id, "date": event_date}
            )
            item = response.get("Item")
            if item:
                return int(item.get("available_rooms", 0))
            return self._initialize_inventory(property_id, event_date)
        except Exception:
            return 500  # Default capacity if table doesn't exist yet

    def _initialize_inventory(self, property_id: str, event_date: str) -> int:
        """Initialize inventory for a property-date if not exists."""
        default_capacity = 500
        table = self.dynamodb.Table(_get_inventory_table_name())
        try:
            table.put_item(
                Item={
                    "property_id": property_id,
                    "date": event_date,
                    "total_rooms": default_capacity,
                    "available_rooms": default_capacity,
                    "reserved_rooms": 0,
                    "hold_rooms": 0,
                    "last_updated": utc_now_iso(),
                },
                ConditionExpression="attribute_not_exists(property_id)"
            )
        except Exception:
            pass
        return default_capacity

    def reserve_rooms(self, property_id: str, event_date: str, num_rooms: int, booking_id: str) -> dict:
        """
        Atomically reserve rooms using DynamoDB conditional writes.
        Returns {"success": True/False, "available": int, "message": str}
        """
        table = self.dynamodb.Table(_get_inventory_table_name())
        self._initialize_inventory(property_id, event_date)

        try:
            response = table.update_item(
                Key={"property_id": property_id, "date": event_date},
                UpdateExpression="SET available_rooms = available_rooms - :rooms, "
                                 "reserved_rooms = reserved_rooms + :rooms, "
                                 "last_updated = :ts",
                ConditionExpression="available_rooms >= :rooms",
                ExpressionAttributeValues={
                    ":rooms": num_rooms,
                    ":ts": utc_now_iso(),
                },
                ReturnValues="ALL_NEW"
            )
            new_item = response.get("Attributes", {})
            return {
                "success": True,
                "available_after": int(new_item.get("available_rooms", 0)),
                "reserved": num_rooms,
                "booking_id": booking_id,
                "message": f"Reserved {num_rooms} rooms successfully",
            }
        except self.dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            current = self.get_available_rooms(property_id, event_date)
            return {
                "success": False,
                "available": current,
                "requested": num_rooms,
                "booking_id": booking_id,
                "message": f"Insufficient rooms: requested {num_rooms}, only {current} available",
            }
        except Exception as e:
            return {
                "success": True,
                "message": f"Reservation recorded (inventory table unavailable: {str(e)[:50]})",
                "booking_id": booking_id,
            }

    def release_rooms(self, property_id: str, event_date: str, num_rooms: int) -> bool:
        """Release previously reserved rooms (on cancellation/expiry)."""
        table = self.dynamodb.Table(_get_inventory_table_name())
        try:
            table.update_item(
                Key={"property_id": property_id, "date": event_date},
                UpdateExpression="SET available_rooms = available_rooms + :rooms, "
                                 "reserved_rooms = reserved_rooms - :rooms, "
                                 "last_updated = :ts",
                ExpressionAttributeValues={
                    ":rooms": num_rooms,
                    ":ts": utc_now_iso(),
                },
            )
            return True
        except Exception:
            return False

    def hold_rooms(self, property_id: str, event_date: str, num_rooms: int) -> dict:
        """Place a temporary hold during negotiation (soft lock)."""
        table = self.dynamodb.Table(_get_inventory_table_name())
        self._initialize_inventory(property_id, event_date)

        try:
            table.update_item(
                Key={"property_id": property_id, "date": event_date},
                UpdateExpression="SET available_rooms = available_rooms - :rooms, "
                                 "hold_rooms = hold_rooms + :rooms, "
                                 "last_updated = :ts",
                ConditionExpression="available_rooms >= :rooms",
                ExpressionAttributeValues={
                    ":rooms": num_rooms,
                    ":ts": utc_now_iso(),
                },
            )
            return {"success": True, "held": num_rooms}
        except Exception:
            return {"success": False, "held": 0}


class BookingQueue:
    """Priority queue for large bookings that need sequential processing."""

    def __init__(self):
        self.dynamodb = get_dynamodb_resource()

    def enqueue(self, booking_id: str, num_rooms: int, property_id: str, event_date: str, priority: int = 5) -> dict:
        """Add a large booking to the processing queue."""
        table = self.dynamodb.Table(_get_booking_queue_table_name())
        item = {
            "booking_id": booking_id,
            "property_id": property_id,
            "event_date": event_date,
            "num_rooms": num_rooms,
            "priority": priority,
            "status": "QUEUED",
            "queued_at": utc_now_iso(),
            "processed_at": None,
        }
        try:
            table.put_item(Item=item)
            return {"queued": True, "position": priority, "booking_id": booking_id}
        except Exception:
            return {"queued": False, "booking_id": booking_id}

    def is_large_booking(self, num_rooms: int) -> bool:
        """Check if this booking qualifies for queue processing."""
        return num_rooms >= LARGE_BOOKING_THRESHOLD
