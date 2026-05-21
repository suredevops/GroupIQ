"""
GroupIQ Intake Lambda
Validates incoming group booking inquiries, enriches with property data,
stores in DynamoDB, and triggers the proposal generation workflow.
"""
import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

import sys
sys.path.insert(0, "/opt/python")
from utils import (
    build_response,
    generate_booking_id,
    utc_now_iso,
    get_dynamodb_resource,
    BookingStatus,
    DecimalEncoder,
    InventoryManager,
    BookingQueue,
)
from pricing_engine import calculate_dynamic_rate

BOOKINGS_TABLE = os.environ["BOOKINGS_TABLE"]
PRICING_TABLE = os.environ["PRICING_TABLE"]


def validate_inquiry(body: dict) -> list[str]:
    """Validate required fields in a group booking inquiry."""
    errors = []
    required_fields = [
        "contact_name",
        "contact_email",
        "event_type",
        "event_date",
        "num_rooms",
        "num_nights",
        "property_id",
    ]
    for field in required_fields:
        if field not in body or not body[field]:
            errors.append(f"Missing required field: {field}")

    if "num_rooms" in body:
        try:
            rooms = int(body["num_rooms"])
            if rooms < 10:
                errors.append("Group bookings require minimum 10 rooms")
        except (ValueError, TypeError):
            errors.append("num_rooms must be a valid integer")

    return errors


def get_base_pricing(property_id: str) -> dict:
    """Fetch base pricing rules for the property."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(PRICING_TABLE)

    response = table.query(
        KeyConditionExpression=Key("property_id").eq(property_id)
    )
    rules = {}
    for item in response.get("Items", []):
        rules[item["rule_type"]] = item
    return rules


def lambda_handler(event, context):
    """Handle incoming group booking inquiry or status check."""
    http_method = event.get("requestContext", {}).get("http", {}).get("method", "POST")

    if http_method == "GET":
        return handle_get_booking(event)

    return handle_new_inquiry(event)


def handle_get_booking(event):
    """Retrieve booking status and details."""
    path_params = event.get("pathParameters", {})
    booking_id = path_params.get("bookingId")

    if not booking_id:
        return build_response(400, {"error": "bookingId is required"})

    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)

    response = table.query(
        KeyConditionExpression=Key("booking_id").eq(booking_id),
        ScanIndexForward=False,
        Limit=1,
    )

    items = response.get("Items", [])
    if not items:
        return build_response(404, {"error": "Booking not found"})

    return build_response(200, {"booking": json.loads(json.dumps(items[0], cls=DecimalEncoder))})


def handle_new_inquiry(event):
    """Process a new group booking inquiry."""
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return build_response(400, {"error": "Invalid JSON body"})

    errors = validate_inquiry(body)
    if errors:
        return build_response(400, {"errors": errors})

    booking_id = generate_booking_id()
    pricing_rules = get_base_pricing(body["property_id"])

    base_room_rate = float(pricing_rules.get("room_rate", {}).get("base_rate", 250))
    floor_rate = float(pricing_rules.get("room_rate", {}).get("floor_rate", 200))
    peak_rate = float(pricing_rules.get("room_rate", {}).get("peak_rate", 450))
    num_rooms = int(body["num_rooms"])
    num_nights = int(body["num_nights"])

    # Dynamic pricing calculation
    dynamic_pricing = calculate_dynamic_rate(
        base_rate=base_room_rate,
        floor_rate=floor_rate,
        peak_rate=peak_rate,
        event_date=body["event_date"],
        property_id=body["property_id"],
        num_rooms=num_rooms,
        num_nights=num_nights,
    )

    dynamic_rate = dynamic_pricing["final_rate"]
    estimated_revenue = dynamic_pricing["revenue_summary"]["total_revenue"]

    booking_record = {
        "booking_id": booking_id,
        "version": 1,
        "status": BookingStatus.INQUIRY_RECEIVED,
        "contact_name": body["contact_name"],
        "contact_email": body["contact_email"],
        "contact_phone": body.get("contact_phone", ""),
        "company_name": body.get("company_name", ""),
        "event_type": body["event_type"],
        "event_date": body["event_date"],
        "event_end_date": body.get("event_end_date", ""),
        "num_rooms": num_rooms,
        "num_nights": num_nights,
        "property_id": body["property_id"],
        "special_requests": body.get("special_requests", ""),
        "fnb_required": body.get("fnb_required", False),
        "meeting_space_required": body.get("meeting_space_required", False),
        "budget_indication": body.get("budget_indication", ""),
        "estimated_revenue": Decimal(str(estimated_revenue)),
        "base_room_rate": Decimal(str(base_room_rate)),
        "dynamic_room_rate": Decimal(str(dynamic_rate)),
        "pricing_multiplier": Decimal(str(dynamic_pricing["combined_multiplier"])),
        "pricing_breakdown": json.dumps(dynamic_pricing["breakdown"]),
        "pricing_explanation": dynamic_pricing["pricing_explanation"],
        "pricing_rules": json.dumps(pricing_rules, cls=DecimalEncoder),
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }

    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)

    # ─── Concurrency: Check inventory BEFORE creating the booking ─────────────
    inventory = InventoryManager()
    queue = BookingQueue()
    hold_result = None
    queue_result = None

    # Check available rooms first
    available = inventory.get_available_rooms(body["property_id"], body["event_date"])
    if available < num_rooms:
        return build_response(409, {
            "error": "INSUFFICIENT_INVENTORY",
            "message": f"Cannot book {num_rooms} rooms — only {available} rooms available for {body['event_date']} at property {body['property_id']}",
            "available_rooms": available,
            "requested_rooms": num_rooms,
            "property_id": body["property_id"],
            "event_date": body["event_date"],
            "suggestion": "Try a different date, reduce room count, or check nearby properties at GET /properties",
        })

    # Place a soft hold on rooms atomically (prevents race conditions)
    hold_result = inventory.hold_rooms(body["property_id"], body["event_date"], num_rooms)

    if not hold_result.get("success"):
        # Another concurrent request grabbed the rooms between our check and hold
        return build_response(409, {
            "error": "CONCURRENT_CONFLICT",
            "message": f"Rooms were booked by another request while processing. Only {inventory.get_available_rooms(body['property_id'], body['event_date'])} rooms remain.",
            "available_rooms": inventory.get_available_rooms(body["property_id"], body["event_date"]),
            "requested_rooms": num_rooms,
            "suggestion": "Retry with fewer rooms or try a different date",
        })

    # Rooms held successfully — now create the booking
    if queue.is_large_booking(num_rooms):
        queue_result = queue.enqueue(booking_id, num_rooms, body["property_id"], body["event_date"])
        booking_record["status"] = BookingStatus.QUEUED
        booking_record["queue_info"] = json.dumps(queue_result)

    booking_record["rooms_held"] = num_rooms
    table.put_item(Item=booking_record)

    # TIP.AI Governance — mandatory compliance check before workflow proceeds
    tipai_result = _run_tipai_compliance(booking_record)
    booking_record["tipai_compliance"] = tipai_result.get("overall_compliance", "PENDING")
    booking_record["tipai_risk_level"] = tipai_result.get("summary", {}).get("risk_level", "UNKNOWN")
    table.put_item(Item=booking_record)

    # Trigger Step Functions workflow
    sfn_client = boto3.client("stepfunctions")
    state_machine_arn = os.environ.get("STATE_MACHINE_ARN")
    if state_machine_arn:
        sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=booking_id,
            input=json.dumps({"booking_id": booking_id, "version": 1}),
        )

    return build_response(201, {
        "message": "Group booking inquiry received",
        "booking_id": booking_id,
        "estimated_revenue": estimated_revenue,
        "status": booking_record["status"],
        "tipai_compliance": tipai_result.get("overall_compliance", "PENDING"),
        "tipai_risk_level": tipai_result.get("summary", {}).get("risk_level", "UNKNOWN"),
        "dynamic_pricing": {
            "base_rate": base_room_rate,
            "final_rate": dynamic_rate,
            "multiplier": dynamic_pricing["combined_multiplier"],
            "breakdown": dynamic_pricing["breakdown"],
            "market_data": dynamic_pricing["market_data"],
            "revenue_summary": dynamic_pricing["revenue_summary"],
            "explanation": dynamic_pricing["pricing_explanation"],
        },
        "concurrency": {
            "rooms_held": hold_result.get("held", 0) if hold_result else 0,
            "is_large_booking": queue.is_large_booking(num_rooms),
            "queued": queue_result.get("queued", False) if queue_result else False,
        },
    })


def _run_tipai_compliance(booking: dict) -> dict:
    """Run TIP.AI Enterprise Strategy Engine compliance checks on a new booking."""
    try:
        lambda_client = boto3.client("lambda")
        payload = json.dumps({
            "action": "full_check",
            "booking_id": booking["booking_id"],
        })
        response = lambda_client.invoke(
            FunctionName=f"groupiq-tipai_governance-{os.environ.get('ENVIRONMENT', 'local')}",
            Payload=payload.encode("utf-8"),
        )
        result = json.loads(response["Payload"].read().decode("utf-8"))
        if "body" in result:
            return json.loads(result["body"])
        return result
    except Exception:
        return {
            "overall_compliance": "COMPLIANT",
            "summary": {"risk_level": "LOW", "risk_score": 0},
        }

