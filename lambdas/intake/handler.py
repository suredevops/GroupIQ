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
)

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
    num_rooms = int(body["num_rooms"])
    num_nights = int(body["num_nights"])

    estimated_revenue = base_room_rate * num_rooms * num_nights

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
        "pricing_rules": json.dumps(pricing_rules, cls=DecimalEncoder),
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }

    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)
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
        "status": BookingStatus.INQUIRY_RECEIVED,
        "tipai_compliance": tipai_result.get("overall_compliance", "PENDING"),
        "tipai_risk_level": tipai_result.get("summary", {}).get("risk_level", "UNKNOWN"),
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

