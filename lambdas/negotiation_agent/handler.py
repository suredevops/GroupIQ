"""
GroupIQ Negotiation Agent Lambda
Handles counter-offers from event planners. Uses Bedrock to evaluate the request,
determine if it falls within pre-set negotiation bounds, and either accept,
counter-propose, or escalate to a human sales manager.
"""
import json
import os
from datetime import datetime, timezone
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
    get_sns_client,
    BookingStatus,
    DecimalEncoder,
)

BOOKINGS_TABLE = os.environ["BOOKINGS_TABLE"]
NEGOTIATIONS_TABLE = os.environ["NEGOTIATIONS_TABLE"]
PRICING_TABLE = os.environ["PRICING_TABLE"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
MAX_DISCOUNT_PERCENT = float(os.environ.get("MAX_DISCOUNT_PERCENT", "15"))
ESCALATION_TOPIC_ARN = os.environ["ESCALATION_TOPIC_ARN"]


NEGOTIATION_SYSTEM_PROMPT = """You are GroupIQ's negotiation engine for Marriott International group bookings.

You are evaluating a counter-offer from a client against the original proposal.

Your negotiation strategy:
1. ACCEPT if the counter-offer is within {max_discount}% of the recommended price
2. COUNTER if there's room to meet in the middle (offer a compromise that protects RevPAR)
3. ESCALATE if the request exceeds {max_discount}% discount or involves non-standard terms

Rules:
- Never go below the floor price (base_rate * 0.{floor_multiplier})
- Protect ancillary revenue (F&B, meeting space) — discount rooms before F&B
- High-value repeat clients get +3% flexibility
- Peak season dates get -5% flexibility (less room to discount)
- Always maintain a professional, warm tone
- Include a brief rationale for the decision

Output MUST be valid JSON:
{{
  "decision": "ACCEPT" | "COUNTER" | "ESCALATE",
  "rationale": "string explaining the decision",
  "counter_proposal": {{...}} or null,
  "final_price": number or null,
  "discount_applied_percent": number,
  "message_to_client": "string — the actual response to send"
}}"""


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


def get_negotiation_history(booking_id: str) -> list:
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(NEGOTIATIONS_TABLE)
    response = table.query(
        KeyConditionExpression=Key("booking_id").eq(booking_id),
        ScanIndexForward=True,
    )
    return response.get("Items", [])


def _local_mock_negotiation(booking: dict, counter_offer: dict) -> dict:
    """Mock negotiation logic for local development when Bedrock is unavailable."""
    base_rate = float(booking.get("base_room_rate", 299))
    floor_rate = base_rate * (1 - MAX_DISCOUNT_PERCENT / 100)
    requested_rate = float(counter_offer.get("proposed_rate", counter_offer.get("requested_room_rate", base_rate)))
    discount_pct = round((1 - requested_rate / base_rate) * 100, 1)

    if requested_rate >= floor_rate:
        return {
            "decision": "ACCEPT",
            "rationale": f"Requested rate ${requested_rate}/night is within {discount_pct}% discount (max allowed: {MAX_DISCOUNT_PERCENT}%). Acceptable for {booking['num_rooms']} rooms over {booking['num_nights']} nights.",
            "counter_proposal": None,
            "final_price": requested_rate,
            "discount_applied_percent": discount_pct,
            "message_to_client": f"Great news! We're happy to accommodate your request at ${requested_rate}/night for {booking['num_rooms']} rooms. That's a {discount_pct}% discount — an excellent value for your {booking['event_type']}. We'll send the updated proposal shortly.",
        }
    elif requested_rate >= floor_rate * 0.9:
        compromise_rate = round((requested_rate + floor_rate) / 2, 2)
        return {
            "decision": "COUNTER",
            "rationale": f"Requested rate ${requested_rate}/night exceeds maximum discount. Counter-proposing ${compromise_rate}/night as a compromise.",
            "counter_proposal": {"room_rate": compromise_rate, "includes_breakfast": True, "late_checkout": True},
            "final_price": compromise_rate,
            "discount_applied_percent": round((1 - compromise_rate / base_rate) * 100, 1),
            "message_to_client": f"Thank you for your proposal. While we can't quite reach ${requested_rate}/night, we'd like to offer ${compromise_rate}/night with complimentary breakfast and late checkout included — adding over $5,000 in value to your group package.",
        }
    else:
        return {
            "decision": "ESCALATE",
            "rationale": f"Requested rate ${requested_rate}/night requires {discount_pct}% discount, significantly exceeding the {MAX_DISCOUNT_PERCENT}% maximum. Escalating to sales.",
            "counter_proposal": None,
            "final_price": None,
            "discount_applied_percent": discount_pct,
            "message_to_client": "We appreciate your interest! Given the scope of your request, our senior sales manager will reach out within 24 hours with a custom package tailored to your needs.",
        }


def invoke_bedrock_negotiation(booking: dict, counter_offer: dict, history: list) -> dict:
    """Use Bedrock to evaluate the counter-offer and decide next action."""
    client = get_bedrock_client()

    floor_multiplier = 100 - int(MAX_DISCOUNT_PERCENT)
    system_prompt = NEGOTIATION_SYSTEM_PROMPT.format(
        max_discount=int(MAX_DISCOUNT_PERCENT),
        floor_multiplier=floor_multiplier,
    )

    history_text = ""
    if history:
        history_text = "\n\nNegotiation History:\n"
        for turn in history:
            history_text += f"Turn {turn['turn_number']}: {turn['action']} — {turn.get('summary', '')}\n"

    user_message = f"""Evaluate this counter-offer:

Original Proposal Summary:
- Recommended Total: ${booking.get('proposal_summary', {}).get('total_recommended', 'N/A')}
- Base Room Rate: ${booking.get('base_room_rate', 250)}/night
- Rooms: {booking['num_rooms']} | Nights: {booking['num_nights']}
- Event Type: {booking['event_type']}
- Event Date: {booking['event_date']}

Client Counter-Offer:
- Requested Price: ${counter_offer.get('requested_price', 'Not specified')}
- Requested Room Rate: ${counter_offer.get('requested_room_rate', 'Not specified')}
- Additional Requests: {counter_offer.get('additional_requests', 'None')}
- Client Message: {counter_offer.get('message', 'No message')}

Max Allowed Discount: {MAX_DISCOUNT_PERCENT}%
{history_text}"""

    request_body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "temperature": 0.2,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_message}
        ],
    })

    try:
        response = client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=request_body,
        )

        response_body = json.loads(response["body"].read())
        content = response_body["content"][0]["text"]

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        return json.loads(content)
    except Exception:
        return _local_mock_negotiation(booking, counter_offer)


def record_negotiation_turn(booking_id: str, turn_number: int, action: str, details: dict):
    """Record a negotiation turn in the history table."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(NEGOTIATIONS_TABLE)
    table.put_item(Item={
        "booking_id": booking_id,
        "turn_number": turn_number,
        "action": action,
        "details": json.dumps(details, cls=DecimalEncoder),
        "summary": details.get("rationale", ""),
        "timestamp": utc_now_iso(),
    })


def escalate_to_sales(booking: dict, negotiation_result: dict):
    """Send SNS notification to sales team for manual intervention."""
    sns = get_sns_client()
    sns.publish(
        TopicArn=ESCALATION_TOPIC_ARN,
        Subject=f"[GroupIQ ESCALATION] {booking['booking_id']} - {booking['event_type']}",
        Message=json.dumps({
            "booking_id": booking["booking_id"],
            "contact": booking["contact_name"],
            "company": booking.get("company_name", "N/A"),
            "event_type": booking["event_type"],
            "event_date": booking["event_date"],
            "num_rooms": int(booking["num_rooms"]),
            "rationale": negotiation_result["rationale"],
            "estimated_revenue": float(booking.get("estimated_revenue", 0)),
        }, indent=2),
    )


def lambda_handler(event, context):
    """Handle a negotiation request (counter-offer from client)."""
    # Handle both API Gateway and Step Functions invocations
    if "body" in event:
        try:
            body = json.loads(event.get("body", "{}"))
        except json.JSONDecodeError:
            return build_response(400, {"error": "Invalid JSON body"})
        booking_id = event.get("pathParameters", {}).get("bookingId")
    else:
        body = event
        booking_id = event.get("booking_id")

    if not booking_id:
        return build_response(400, {"error": "bookingId is required"})

    booking = get_booking(booking_id)
    if not booking:
        return build_response(404, {"error": "Booking not found"})

    counter_offer = body.get("counter_offer", body)
    history = get_negotiation_history(booking_id)
    turn_number = len(history) + 1

    # Safety check — max 5 negotiation rounds before auto-escalation
    if turn_number > 5:
        escalate_to_sales(booking, {"rationale": "Maximum negotiation rounds exceeded"})
        return build_response(200, {
            "booking_id": booking_id,
            "decision": "ESCALATE",
            "message": "This negotiation has been escalated to our sales team for personalized attention.",
        })

    negotiation_result = invoke_bedrock_negotiation(booking, counter_offer, history)

    record_negotiation_turn(booking_id, turn_number, negotiation_result["decision"], negotiation_result)

    # Update booking status
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)

    new_status = {
        "ACCEPT": BookingStatus.ACCEPTED,
        "COUNTER": BookingStatus.NEGOTIATING,
        "ESCALATE": BookingStatus.ESCALATED,
    }.get(negotiation_result["decision"], BookingStatus.NEGOTIATING)

    table.update_item(
        Key={"booking_id": booking_id, "version": int(booking["version"])},
        UpdateExpression="SET #s = :status, updated_at = :ts",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":status": new_status,
            ":ts": utc_now_iso(),
        },
    )

    if negotiation_result["decision"] == "ESCALATE":
        escalate_to_sales(booking, negotiation_result)

    response_payload = {
        "booking_id": booking_id,
        "decision": negotiation_result["decision"],
        "message_to_client": negotiation_result["message_to_client"],
        "counter_proposal": negotiation_result.get("counter_proposal"),
        "turn_number": turn_number,
        "status": new_status,
    }

    if "body" in event:
        return build_response(200, response_payload)
    return response_payload
