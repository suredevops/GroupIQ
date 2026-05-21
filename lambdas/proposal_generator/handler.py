"""
GroupIQ Proposal Generator Lambda
Uses Amazon Bedrock (Claude) to generate a customized group booking proposal
with dynamic room blocks, F&B packages, and pricing.
"""
import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

import sys
sys.path.insert(0, "/opt/python")
from utils import (
    build_response,
    utc_now_iso,
    get_dynamodb_resource,
    get_bedrock_client,
    get_s3_client,
    BookingStatus,
    DecimalEncoder,
)

BOOKINGS_TABLE = os.environ["BOOKINGS_TABLE"]
PRICING_TABLE = os.environ["PRICING_TABLE"]
PROPOSALS_BUCKET = os.environ["PROPOSALS_BUCKET"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]


PROPOSAL_SYSTEM_PROMPT = """You are GroupIQ, an expert hotel group sales AI for Marriott International.
Your job is to generate compelling, customized group booking proposals.

Given the booking details and property pricing rules, create a detailed proposal that includes:
1. Executive Summary — personalized greeting and event acknowledgment
2. Room Block — tiered pricing based on commitment level (e.g., 80% pickup = best rate)
3. F&B Packages — 2-3 options ranging from basic to premium
4. Meeting Space — if requested, include AV and setup options
5. Value-Adds — complimentary upgrades, welcome amenities, loyalty points
6. Terms — cutoff dates, attrition policy, cancellation terms
7. Total Estimated Investment — breakdown by category

Rules:
- Never exceed the max_discount from pricing rules
- Always offer at least 2 tier options (Good/Better/Best)
- Include urgency (proposal valid for 7 days)
- Personalize based on event_type (wedding vs conference vs corporate)
- Calculate RevPAR impact and ensure profitability

Output MUST be valid JSON with the following structure:
{
  "executive_summary": "string",
  "room_block": { "tiers": [...] },
  "fnb_packages": [...],
  "meeting_space": {...} or null,
  "value_adds": [...],
  "terms": {...},
  "total_investment": { "min": number, "max": number, "recommended": number }
}"""


def get_booking(booking_id: str) -> dict:
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)
    response = table.query(
        KeyConditionExpression=Key("booking_id").eq(booking_id),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def invoke_bedrock(booking: dict, pricing_rules: dict) -> dict:
    """Call Bedrock Claude to generate the proposal."""
    client = get_bedrock_client()

    user_message = f"""Generate a group booking proposal for the following inquiry:

Event Type: {booking['event_type']}
Event Date: {booking['event_date']}
Contact: {booking['contact_name']} ({booking.get('company_name', 'Individual')})
Number of Rooms: {booking['num_rooms']}
Number of Nights: {booking['num_nights']}
F&B Required: {booking.get('fnb_required', False)}
Meeting Space Required: {booking.get('meeting_space_required', False)}
Special Requests: {booking.get('special_requests', 'None')}
Budget Indication: {booking.get('budget_indication', 'Not specified')}

Property Pricing Rules:
{json.dumps(pricing_rules, cls=DecimalEncoder, indent=2)}

Base Room Rate: ${booking['base_room_rate']}/night
"""

    request_body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.3,
        "system": PROPOSAL_SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": user_message}
        ],
    })

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=request_body,
    )

    response_body = json.loads(response["body"].read())
    content = response_body["content"][0]["text"]

    # Extract JSON from response (handle markdown code blocks)
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    return json.loads(content)


def store_proposal_s3(booking_id: str, proposal: dict) -> str:
    """Store the full proposal JSON in S3."""
    s3 = get_s3_client()
    key = f"proposals/{booking_id}/proposal_v1.json"
    s3.put_object(
        Bucket=PROPOSALS_BUCKET,
        Key=key,
        Body=json.dumps(proposal, indent=2, cls=DecimalEncoder),
        ContentType="application/json",
        Metadata={"booking_id": booking_id, "generated_at": utc_now_iso()},
    )
    return key


def lambda_handler(event, context):
    """Generate a customized group booking proposal using Bedrock."""
    booking_id = event.get("booking_id")
    if not booking_id:
        return {"error": "booking_id is required", "status": "FAILED"}

    booking = get_booking(booking_id)
    if not booking:
        return {"error": f"Booking {booking_id} not found", "status": "FAILED"}

    pricing_rules = json.loads(booking.get("pricing_rules", "{}"))

    proposal = invoke_bedrock(booking, pricing_rules)

    s3_key = store_proposal_s3(booking_id, proposal)

    # Update booking record with proposal details
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)

    expiry_date = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    table.put_item(Item={
        **booking,
        "version": int(booking["version"]) + 1,
        "status": BookingStatus.PROPOSAL_GENERATED,
        "proposal_s3_key": s3_key,
        "proposal_summary": {
            "total_min": Decimal(str(proposal["total_investment"]["min"])),
            "total_max": Decimal(str(proposal["total_investment"]["max"])),
            "total_recommended": Decimal(str(proposal["total_investment"]["recommended"])),
            "num_tiers": len(proposal["room_block"]["tiers"]),
            "num_fnb_options": len(proposal["fnb_packages"]),
        },
        "proposal_expiry": expiry_date,
        "updated_at": utc_now_iso(),
    })

    return {
        "booking_id": booking_id,
        "status": BookingStatus.PROPOSAL_GENERATED,
        "proposal_s3_key": s3_key,
        "proposal_summary": proposal["total_investment"],
        "expiry": expiry_date,
    }
