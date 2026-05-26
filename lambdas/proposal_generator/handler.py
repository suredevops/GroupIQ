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
BEDROCK_GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
BEDROCK_GUARDRAIL_VERSION = os.environ.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT")


PROPOSAL_SYSTEM_PROMPT = """You are GroupIQ, an expert hotel group sales AI for Marriott International.
Your job is to generate compelling, customized group booking proposals with transparent dynamic pricing.

Given the booking details, dynamic pricing breakdown, and property pricing rules, create a detailed proposal that includes:
1. Executive Summary — personalized greeting and event acknowledgment
2. Pricing Rationale — explain WHY this rate was calculated (reference specific factors)
3. Room Block — tiered pricing based on commitment level (e.g., 80% pickup = best rate)
4. F&B Packages — 2-3 options ranging from basic to premium
5. Meeting Space — if requested, include AV and setup options
6. Value-Adds — complimentary upgrades, welcome amenities, loyalty points
7. Terms — cutoff dates, attrition policy, cancellation terms
8. Total Estimated Investment — breakdown by category

Rules:
- Never exceed the max_discount from pricing rules
- Always offer at least 2 tier options (Good/Better/Best)
- Include urgency (proposal valid for 7 days)
- Personalize based on event_type (wedding vs conference vs corporate)
- Reference the dynamic pricing factors (seasonality, occupancy, etc.) to justify the rate
- Show the customer their savings vs. rack rate
- Calculate RevPAR impact and ensure profitability

Output MUST be valid JSON with the following structure:
{
  "executive_summary": "string",
  "pricing_rationale": "string explaining why this rate",
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

    # Build pricing breakdown section
    pricing_breakdown = booking.get("pricing_breakdown", "[]")
    if isinstance(pricing_breakdown, str):
        try:
            pricing_breakdown = json.loads(pricing_breakdown)
        except (json.JSONDecodeError, TypeError):
            pricing_breakdown = []

    pricing_section = ""
    if pricing_breakdown:
        pricing_section = "\n\nDynamic Pricing Breakdown (explain these factors to the customer):\n"
        for factor in pricing_breakdown:
            pricing_section += "  * " + factor.get('factor', 'Unknown') + ": " + factor.get('impact', '0%') + " - " + factor.get('description', '') + "\n"
        pricing_section += "\nBase Rate: $" + str(booking.get('base_room_rate', 250)) + "/night"
        pricing_section += "\nDynamic Rate: $" + str(booking.get('dynamic_room_rate', booking.get('base_room_rate', 250))) + "/night"
        pricing_section += "\nMultiplier Applied: x" + str(booking.get('pricing_multiplier', 1.0))

    pricing_explanation = booking.get("pricing_explanation", "")
    if pricing_explanation:
        pricing_section += "\n\nPricing Logic:\n" + str(pricing_explanation)

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
{pricing_section}
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

    try:
        invoke_params = dict(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=request_body,
        )
        if BEDROCK_GUARDRAIL_ID:
            invoke_params["guardrailIdentifier"] = BEDROCK_GUARDRAIL_ID
            invoke_params["guardrailVersion"] = BEDROCK_GUARDRAIL_VERSION

        response = client.invoke_model(**invoke_params)

        response_body = json.loads(response["body"].read())
        content = response_body["content"][0]["text"]
    except Exception:
        # Bedrock unavailable (LocalStack) — generate a mock proposal
        return _generate_mock_proposal(booking, pricing_rules)

    # Extract JSON from response (handle markdown code blocks)
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    return json.loads(content)


def _generate_mock_proposal(booking: dict, pricing_rules: dict) -> dict:
    """Generate a mock proposal when Bedrock is unavailable (local dev)."""
    num_rooms = int(booking.get("num_rooms", 50))
    num_nights = int(booking.get("num_nights", 3))
    base_rate = float(booking.get("dynamic_room_rate", booking.get("base_room_rate", 299)))
    event_type = booking.get("event_type", "conference")
    contact = booking.get("contact_name", "Guest")

    base_total = base_rate * num_rooms * num_nights
    tier1_rate = base_rate
    tier2_rate = base_rate * 0.92
    tier3_rate = base_rate * 0.85

    return {
        "executive_summary": f"Dear {contact}, thank you for considering Marriott for your upcoming {event_type}. We are delighted to present a customized proposal for {num_rooms} rooms over {num_nights} nights. Our dynamic pricing engine has calculated the optimal rate based on current market conditions.",
        "pricing_rationale": f"Your rate of ${base_rate:.0f}/night reflects current market factors including seasonality, occupancy levels, and your group size discount. This represents excellent value compared to our rack rate.",
        "room_block": {
            "tiers": [
                {"name": "Best - 90% Pickup Guarantee", "rate": tier3_rate, "rooms": num_rooms, "commitment": "90% minimum pickup"},
                {"name": "Better - 80% Pickup", "rate": tier2_rate, "rooms": num_rooms, "commitment": "80% minimum pickup"},
                {"name": "Good - Standard Block", "rate": tier1_rate, "rooms": num_rooms, "commitment": "70% minimum pickup"},
            ]
        },
        "fnb_packages": [
            {"name": "Premium Package", "per_person": 95, "includes": "Breakfast, lunch, dinner, coffee breaks"},
            {"name": "Standard Package", "per_person": 65, "includes": "Breakfast and lunch with coffee breaks"},
            {"name": "Basic Package", "per_person": 45, "includes": "Breakfast only with AM coffee break"},
        ],
        "meeting_space": {"included": bool(booking.get("meeting_space_required")), "rooms": 3, "av_included": True, "setup": "Theater and classroom style"},
        "value_adds": [
            "Complimentary room upgrade for event organizer",
            "Welcome amenity baskets for VIP guests",
            "10,000 Marriott Bonvoy points per room night",
            "Complimentary Wi-Fi for all group members",
            "Dedicated event coordinator",
        ],
        "terms": {
            "proposal_valid_days": 7,
            "cutoff_date": "30 days before event",
            "attrition": "80% of block with no penalty",
            "cancellation": "Full refund 60+ days out, 50% refund 30-59 days",
        },
        "total_investment": {
            "min": round(tier3_rate * num_rooms * num_nights * 0.9),
            "max": round(tier1_rate * num_rooms * num_nights * 1.1),
            "recommended": round(base_total),
        },
    }


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
