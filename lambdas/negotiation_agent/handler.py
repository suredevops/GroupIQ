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
    get_ses_client,
    BookingStatus,
    InventoryManager,
    BookingQueue,
    DecimalEncoder,
)

BOOKINGS_TABLE = os.environ["BOOKINGS_TABLE"]
NEGOTIATIONS_TABLE = os.environ["NEGOTIATIONS_TABLE"]
PRICING_TABLE = os.environ["PRICING_TABLE"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
MAX_DISCOUNT_PERCENT = float(os.environ.get("MAX_DISCOUNT_PERCENT", "15"))
ESCALATION_TOPIC_ARN = os.environ["ESCALATION_TOPIC_ARN"]
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "noreply@groupiq.local")


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


def send_notification_email(booking: dict, negotiation_result: dict) -> str:
    """Send email notification to the customer for any negotiation outcome."""
    ses = get_ses_client()
    decision = negotiation_result.get("decision", "")
    contact_name = booking.get("contact_name", "Guest")
    contact_email = booking.get("contact_email", "")
    event_type = booking.get("event_type", "event").title()
    event_date = booking.get("event_date", "TBD")
    num_rooms = int(booking.get("num_rooms", 0))
    num_nights = int(booking.get("num_nights", 0))
    message = negotiation_result.get("message_to_client", "")

    if decision == "ACCEPT":
        final_price = negotiation_result.get("final_price", "N/A")
        discount_pct = negotiation_result.get("discount_applied_percent", 0)
        total_room_cost = float(final_price) * num_rooms * num_nights if final_price != "N/A" else 0
        subject = f"Booking Confirmed! Your {event_type} at Marriott — {booking['booking_id']}"
        banner_color = "#16a34a"
        banner_text = "Booking Confirmed!"
        status_text = "CONFIRMED"
        details_html = f"""
            <tr><td style="padding:8px 0; color:#666;">Confirmed Rate</td><td style="padding:8px 0; font-weight:600;">${final_price}/night ({discount_pct}% discount)</td></tr>
            <tr><td style="padding:8px 0; color:#666;">Estimated Total</td><td style="padding:8px 0; font-weight:600; color:#16a34a; font-size:18px;">${total_room_cost:,.2f}</td></tr>"""
        next_steps = """
            <div style="background: #ecfdf5; border: 1px solid #86efac; padding: 15px; border-radius: 8px; margin-top: 20px;">
                <p style="margin:0; color: #166534;"><strong>Next Steps:</strong></p>
                <ol style="color: #166534; margin: 10px 0 0; padding-left: 20px;">
                    <li>You'll receive a formal contract within 24 hours</li>
                    <li>A deposit of 20% will be required to secure the booking</li>
                    <li>Your dedicated event coordinator will reach out shortly</li>
                </ol>
            </div>"""

    elif decision == "COUNTER":
        counter = negotiation_result.get("counter_proposal", {}) or {}
        counter_rate = counter.get("room_rate", "N/A")
        subject = f"Counter Proposal for Your {event_type} — {booking['booking_id']}"
        banner_color = "#d97706"
        banner_text = "Counter Proposal"
        status_text = "NEGOTIATING"
        extras = []
        if counter.get("includes_breakfast"):
            extras.append("Complimentary breakfast included")
        if counter.get("late_checkout"):
            extras.append("Late checkout included")
        extras_html = "".join(f"<li>{e}</li>" for e in extras)
        details_html = f"""
            <tr><td style="padding:8px 0; color:#666;">Proposed Rate</td><td style="padding:8px 0; font-weight:600; color:#d97706;">${counter_rate}/night</td></tr>
            <tr><td style="padding:8px 0; color:#666;">Added Value</td><td style="padding:8px 0; font-weight:600;"><ul style="margin:0;padding-left:16px;">{extras_html}</ul></td></tr>"""
        next_steps = """
            <div style="background: #fffbeb; border: 1px solid #fcd34d; padding: 15px; border-radius: 8px; margin-top: 20px;">
                <p style="margin:0; color: #92400e;"><strong>What's Next:</strong></p>
                <ol style="color: #92400e; margin: 10px 0 0; padding-left: 20px;">
                    <li>Review the counter-proposal above</li>
                    <li>Reply to accept, or submit another counter-offer</li>
                    <li>This offer is valid for 48 hours</li>
                </ol>
            </div>"""

    else:  # ESCALATE
        subject = f"Your Booking Request Update — {booking['booking_id']}"
        banner_color = "#dc2626"
        banner_text = "Escalated to Sales Team"
        status_text = "ESCALATED"
        details_html = ""
        next_steps = """
            <div style="background: #fef2f2; border: 1px solid #fca5a5; padding: 15px; border-radius: 8px; margin-top: 20px;">
                <p style="margin:0; color: #991b1b;"><strong>What Happens Now:</strong></p>
                <ol style="color: #991b1b; margin: 10px 0 0; padding-left: 20px;">
                    <li>A senior sales manager has been assigned to your request</li>
                    <li>They will reach out within 24 hours with a custom package</li>
                    <li>No action needed from you at this time</li>
                </ol>
            </div>"""

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1B1464 0%, #8b1a2b 100%); color: white; padding: 30px; text-align: center;">
            <h1 style="margin:0;">{banner_text}</h1>
            <p style="margin:5px 0 0; opacity:0.9;">GroupIQ — Marriott International</p>
        </div>
        <div style="padding: 30px; background: #f9f9f9;">
            <h2 style="color: #1B1464;">Dear {contact_name},</h2>
            <p style="font-size: 16px; line-height: 1.6;">{message}</p>

            <div style="background: white; border-radius: 8px; padding: 20px; margin: 20px 0; border-left: 4px solid {banner_color};">
                <h3 style="color: #333; margin-top: 0;">Booking Details</h3>
                <table style="width:100%; border-collapse: collapse;">
                    <tr><td style="padding:8px 0; color:#666;">Booking ID</td><td style="padding:8px 0; font-weight:600;">{booking['booking_id']}</td></tr>
                    <tr><td style="padding:8px 0; color:#666;">Event</td><td style="padding:8px 0; font-weight:600;">{event_type} — {event_date}</td></tr>
                    <tr><td style="padding:8px 0; color:#666;">Rooms</td><td style="padding:8px 0; font-weight:600;">{num_rooms} rooms x {num_nights} nights</td></tr>
                    <tr><td style="padding:8px 0; color:#666;">Status</td><td style="padding:8px 0; font-weight:600; color:{banner_color};">{status_text}</td></tr>
                    {details_html}
                </table>
            </div>
            {next_steps}
            <p style="margin-top: 25px; color: #666; font-size: 13px; border-top: 1px solid #e5e7eb; padding-top: 15px;">
                If you have any questions, reply to this email or contact our group bookings team.<br>
                Powered by GroupIQ — AI-Assisted Group Booking Platform
            </p>
        </div>
    </body>
    </html>"""

    text_body = f"Dear {contact_name},\n\n{message}\n\nBooking ID: {booking['booking_id']}\nEvent: {event_type} on {event_date}\nRooms: {num_rooms} x {num_nights} nights\nStatus: {status_text}\n\nThank you,\nGroupIQ Team"

    response = ses.send_email(
        Source=SES_SENDER_EMAIL,
        Destination={"ToAddresses": [contact_email]},
        Message={
            "Subject": {"Data": subject},
            "Body": {
                "Html": {"Data": html_body},
                "Text": {"Data": text_body},
            },
        },
    )
    return response["MessageId"]


def check_property_availability(booking: dict) -> dict:
    """Check available properties and rooms at the requested location."""
    dynamodb = get_dynamodb_resource()
    pricing_table = dynamodb.Table(PRICING_TABLE)

    property_id = booking.get("property_id", "")
    location = property_id.split("-")[1] if "-" in property_id else "NYC"
    num_rooms_requested = int(booking.get("num_rooms", 0))
    event_date = booking.get("event_date", "")

    # Scan pricing table for all properties in this location
    response = pricing_table.scan()
    all_items = response.get("Items", [])

    # Group by property_id and filter by location
    properties = {}
    for item in all_items:
        pid = item.get("property_id", "")
        if location.upper() in pid.upper():
            if pid not in properties:
                properties[pid] = {"property_id": pid, "rules": []}
            properties[pid]["rules"].append(item)

    # Build availability info for each property in the location
    available_properties = []
    for pid, data in properties.items():
        room_rule = next((r for r in data["rules"] if r.get("rule_type") == "room_rate"), None)
        if room_rule:
            total_capacity = 500  # Default capacity per property
            available_rooms = max(0, total_capacity - num_rooms_requested)
            available_properties.append({
                "property_id": pid,
                "location": location.upper(),
                "base_rate": float(room_rule.get("base_rate", 0)),
                "peak_rate": float(room_rule.get("peak_rate", 0)),
                "floor_rate": float(room_rule.get("floor_rate", 0)),
                "total_capacity": total_capacity,
                "estimated_available_rooms": available_rooms,
                "can_accommodate": num_rooms_requested <= total_capacity,
            })

    # Add nearby Marriott properties for the location
    nearby_properties = _get_nearby_marriott_properties(location, num_rooms_requested, event_date)

    return {
        "requested_location": location.upper(),
        "requested_rooms": num_rooms_requested,
        "event_date": event_date,
        "matching_properties": available_properties,
        "nearby_alternatives": nearby_properties,
        "total_properties_in_location": len(available_properties) + len(nearby_properties),
        "sufficient_capacity": any(p["can_accommodate"] for p in available_properties),
    }


def _get_nearby_marriott_properties(location: str, num_rooms: int, event_date: str) -> list:
    """Return nearby Marriott brand properties with estimated availability."""
    location_properties = {
        "NYC": [
            {"property_id": "MRIOTT-NYC-001", "name": "Marriott Marquis NYC", "brand": "Marriott", "total_rooms": 500, "location": "Times Square, NYC"},
            {"property_id": "WESTIN-NYC-001", "name": "Westin New York", "brand": "Westin", "total_rooms": 450, "location": "Midtown, NYC"},
            {"property_id": "SHRATN-NYC-001", "name": "Sheraton New York", "brand": "Sheraton", "total_rooms": 400, "location": "Midtown West, NYC"},
            {"property_id": "COURTY-NYC-001", "name": "Courtyard Manhattan", "brand": "Courtyard", "total_rooms": 300, "location": "Central Park, NYC"},
            {"property_id": "RITZ-NYC-001", "name": "The Ritz-Carlton NYC", "brand": "Ritz-Carlton", "total_rooms": 250, "location": "Central Park South, NYC"},
        ],
        "LAX": [
            {"property_id": "MRIOTT-LAX-001", "name": "Marriott LAX Airport", "brand": "Marriott", "total_rooms": 350, "location": "LAX, Los Angeles"},
            {"property_id": "WESTIN-LAX-001", "name": "Westin Bonaventure", "brand": "Westin", "total_rooms": 400, "location": "Downtown, Los Angeles"},
            {"property_id": "SHRATN-LAX-001", "name": "Sheraton Universal", "brand": "Sheraton", "total_rooms": 300, "location": "Universal City, LA"},
        ],
        "CHI": [
            {"property_id": "MRIOTT-CHI-001", "name": "Marriott Magnificent Mile", "brand": "Marriott", "total_rooms": 400, "location": "Michigan Ave, Chicago"},
            {"property_id": "SHRATN-CHI-001", "name": "Sheraton Grand Chicago", "brand": "Sheraton", "total_rooms": 350, "location": "River North, Chicago"},
        ],
        "MIA": [
            {"property_id": "MRIOTT-MIA-001", "name": "Marriott Biscayne Bay", "brand": "Marriott", "total_rooms": 300, "location": "Biscayne Bay, Miami"},
            {"property_id": "RITZ-MIA-001", "name": "The Ritz-Carlton Miami", "brand": "Ritz-Carlton", "total_rooms": 200, "location": "South Beach, Miami"},
        ],
        # India — Hyderabad
        "HYD": [
            {"property_id": "MRIOTT-HYD-001", "name": "Marriott Hyderabad", "brand": "Marriott", "total_rooms": 320, "location": "HITEC City, Hyderabad", "lat": 17.4156, "lng": 78.4736, "rooms": {"luxury": 45, "premium": 110, "standard": 165}},
            {"property_id": "WESTIN-HYD-001", "name": "Westin Hyderabad Mindspace", "brand": "Westin", "total_rooms": 294, "location": "Mindspace, HITEC City, Hyderabad", "lat": 17.4432, "lng": 78.3814, "rooms": {"luxury": 40, "premium": 104, "standard": 150}},
            {"property_id": "SHRATN-HYD-001", "name": "Sheraton Hyderabad", "brand": "Sheraton", "total_rooms": 264, "location": "Gachibowli, Hyderabad", "lat": 17.4225, "lng": 78.3410, "rooms": {"luxury": 30, "premium": 94, "standard": 140}},
            {"property_id": "COURTY-HYD-001", "name": "Courtyard by Marriott Hyderabad", "brand": "Courtyard", "total_rooms": 187, "location": "HITEC City, Hyderabad", "lat": 17.4486, "lng": 78.3908, "rooms": {"luxury": 0, "premium": 57, "standard": 130}},
            {"property_id": "FOURPT-HYD-001", "name": "Four Points by Sheraton Hyderabad", "brand": "Four Points", "total_rooms": 160, "location": "Banjara Hills, Hyderabad", "lat": 17.4115, "lng": 78.4483, "rooms": {"luxury": 0, "premium": 45, "standard": 115}},
        ],
        # India — Bengaluru
        "BLR": [
            {"property_id": "MRIOTT-BLR-001", "name": "Marriott Bengaluru Whitefield", "brand": "Marriott", "total_rooms": 395, "location": "Whitefield, Bengaluru", "lat": 12.9698, "lng": 77.7500, "rooms": {"luxury": 55, "premium": 140, "standard": 200}},
            {"property_id": "SHRATN-BLR-001", "name": "Sheraton Grand Bengaluru", "brand": "Sheraton", "total_rooms": 230, "location": "Brigade Gateway, Bengaluru", "lat": 13.0128, "lng": 77.5554, "rooms": {"luxury": 30, "premium": 80, "standard": 120}},
            {"property_id": "WESTIN-BLR-001", "name": "The Westin Bengaluru", "brand": "Westin", "total_rooms": 220, "location": "Koramangala, Bengaluru", "lat": 12.9352, "lng": 77.6245, "rooms": {"luxury": 30, "premium": 75, "standard": 115}},
            {"property_id": "COURTY-BLR-001", "name": "Courtyard by Marriott Bengaluru", "brand": "Courtyard", "total_rooms": 179, "location": "Outer Ring Road, Bengaluru", "lat": 12.9564, "lng": 77.7010, "rooms": {"luxury": 0, "premium": 54, "standard": 125}},
            {"property_id": "FOURPT-BLR-001", "name": "Four Points by Sheraton Bengaluru", "brand": "Four Points", "total_rooms": 172, "location": "Whitefield, Bengaluru", "lat": 12.9700, "lng": 77.7490, "rooms": {"luxury": 0, "premium": 50, "standard": 122}},
            {"property_id": "RITZ-BLR-001", "name": "The Ritz-Carlton Bengaluru", "brand": "Ritz-Carlton", "total_rooms": 277, "location": "Residency Road, Bengaluru", "lat": 12.9716, "lng": 77.6099, "rooms": {"luxury": 130, "premium": 97, "standard": 50}},
        ],
        # India — Mumbai
        "BOM": [
            {"property_id": "MRIOTT-BOM-001", "name": "JW Marriott Mumbai Juhu", "brand": "JW Marriott", "total_rooms": 355, "location": "Juhu Beach, Mumbai", "lat": 19.0968, "lng": 72.8263, "rooms": {"luxury": 75, "premium": 130, "standard": 150}},
            {"property_id": "WESTIN-BOM-001", "name": "The Westin Mumbai Garden City", "brand": "Westin", "total_rooms": 270, "location": "Goregaon, Mumbai", "lat": 19.1663, "lng": 72.8623, "rooms": {"luxury": 35, "premium": 95, "standard": 140}},
            {"property_id": "SHRATN-BOM-001", "name": "Sheraton Grand Mumbai", "brand": "Sheraton", "total_rooms": 245, "location": "Powai, Mumbai", "lat": 19.1197, "lng": 72.9074, "rooms": {"luxury": 30, "premium": 85, "standard": 130}},
            {"property_id": "COURTY-BOM-001", "name": "Courtyard by Marriott Mumbai", "brand": "Courtyard", "total_rooms": 190, "location": "Andheri, Mumbai", "lat": 19.1136, "lng": 72.8697, "rooms": {"luxury": 0, "premium": 60, "standard": 130}},
            {"property_id": "RENAISS-BOM-001", "name": "Renaissance Mumbai Convention", "brand": "Renaissance", "total_rooms": 285, "location": "Powai, Mumbai", "lat": 19.1180, "lng": 72.9050, "rooms": {"luxury": 40, "premium": 100, "standard": 145}},
            {"property_id": "JWMARR-BOM-002", "name": "JW Marriott Mumbai Sahar", "brand": "JW Marriott", "total_rooms": 585, "location": "Sahar, Mumbai", "lat": 19.0990, "lng": 72.8740, "rooms": {"luxury": 120, "premium": 215, "standard": 250}},
        ],
        # India — Delhi/NCR
        "DEL": [
            {"property_id": "MRIOTT-DEL-001", "name": "Marriott Aerocity Delhi", "brand": "Marriott", "total_rooms": 331, "location": "Aerocity, New Delhi", "lat": 28.5535, "lng": 77.1203, "rooms": {"luxury": 45, "premium": 116, "standard": 170}},
            {"property_id": "JWMARR-DEL-001", "name": "JW Marriott New Delhi", "brand": "JW Marriott", "total_rooms": 523, "location": "Aerocity, New Delhi", "lat": 28.5530, "lng": 77.1198, "rooms": {"luxury": 110, "premium": 193, "standard": 220}},
            {"property_id": "WESTIN-DEL-001", "name": "The Westin Gurgaon", "brand": "Westin", "total_rooms": 310, "location": "Sector 29, Gurgaon", "lat": 28.4615, "lng": 77.0595, "rooms": {"luxury": 42, "premium": 108, "standard": 160}},
            {"property_id": "SHRATN-DEL-001", "name": "Sheraton New Delhi", "brand": "Sheraton", "total_rooms": 240, "location": "Saket, New Delhi", "lat": 28.5241, "lng": 77.2066, "rooms": {"luxury": 28, "premium": 82, "standard": 130}},
            {"property_id": "COURTY-DEL-001", "name": "Courtyard by Marriott Gurgaon", "brand": "Courtyard", "total_rooms": 200, "location": "Sohna Road, Gurgaon", "lat": 28.4300, "lng": 77.0500, "rooms": {"luxury": 0, "premium": 60, "standard": 140}},
            {"property_id": "RITZ-DEL-001", "name": "The Ritz-Carlton New Delhi", "brand": "Ritz-Carlton", "total_rooms": 218, "location": "Delhi Golf Course, New Delhi", "lat": 28.4498, "lng": 77.0734, "rooms": {"luxury": 100, "premium": 78, "standard": 40}},
        ],
        # India — Chennai
        "MAA": [
            {"property_id": "MRIOTT-MAA-001", "name": "Marriott Chennai", "brand": "Marriott", "total_rooms": 240, "location": "OMR, Chennai", "lat": 12.9010, "lng": 80.2279, "rooms": {"luxury": 30, "premium": 80, "standard": 130}},
            {"property_id": "WESTIN-MAA-001", "name": "The Westin Chennai Velachery", "brand": "Westin", "total_rooms": 218, "location": "Velachery, Chennai", "lat": 12.9756, "lng": 80.2186, "rooms": {"luxury": 28, "premium": 72, "standard": 118}},
            {"property_id": "SHRATN-MAA-001", "name": "Sheraton Grand Chennai", "brand": "Sheraton", "total_rooms": 185, "location": "Raja Annamalai Puram, Chennai", "lat": 12.9616, "lng": 80.2595, "rooms": {"luxury": 25, "premium": 60, "standard": 100}},
            {"property_id": "COURTY-MAA-001", "name": "Courtyard by Marriott Chennai", "brand": "Courtyard", "total_rooms": 170, "location": "Anna Salai, Chennai", "lat": 12.9500, "lng": 80.2400, "rooms": {"luxury": 0, "premium": 50, "standard": 120}},
            {"property_id": "FOURPT-MAA-001", "name": "Four Points by Sheraton Chennai", "brand": "Four Points", "total_rooms": 152, "location": "OMR, Chennai", "lat": 12.9205, "lng": 80.2330, "rooms": {"luxury": 0, "premium": 42, "standard": 110}},
        ],
        # India — Goa
        "GOA": [
            {"property_id": "MRIOTT-GOA-001", "name": "Marriott Resort & Spa Goa", "brand": "Marriott", "total_rooms": 180, "location": "Miramar Beach, Goa", "lat": 15.4760, "lng": 73.8112, "rooms": {"luxury": 30, "premium": 60, "standard": 90}},
            {"property_id": "WESTIN-GOA-001", "name": "The Westin Goa", "brand": "Westin", "total_rooms": 192, "location": "Anjuna, North Goa", "lat": 15.5762, "lng": 73.7406, "rooms": {"luxury": 35, "premium": 67, "standard": 90}},
            {"property_id": "WGOA-GOA-001", "name": "W Goa", "brand": "W Hotels", "total_rooms": 130, "location": "Vagator, North Goa", "lat": 15.5996, "lng": 73.7380, "rooms": {"luxury": 55, "premium": 50, "standard": 25}},
            {"property_id": "COURTY-GOA-001", "name": "Courtyard by Marriott Goa", "brand": "Courtyard", "total_rooms": 140, "location": "Colva, South Goa", "lat": 15.2796, "lng": 73.9215, "rooms": {"luxury": 0, "premium": 40, "standard": 100}},
        ],
        # India — Jaipur
        "JAI": [
            {"property_id": "MRIOTT-JAI-001", "name": "Marriott Jaipur", "brand": "Marriott", "total_rooms": 210, "location": "Tonk Road, Jaipur", "lat": 26.8550, "lng": 75.8050, "rooms": {"luxury": 28, "premium": 72, "standard": 110}},
            {"property_id": "JWMARR-JAI-001", "name": "JW Marriott Jaipur Resort & Spa", "brand": "JW Marriott", "total_rooms": 200, "location": "Ajmer Road, Jaipur", "lat": 26.9865, "lng": 75.7212, "rooms": {"luxury": 60, "premium": 80, "standard": 60}},
            {"property_id": "SHRATN-JAI-001", "name": "Sheraton Grand Jaipur", "brand": "Sheraton", "total_rooms": 175, "location": "Palace Road, Jaipur", "lat": 26.9830, "lng": 75.7250, "rooms": {"luxury": 22, "premium": 58, "standard": 95}},
            {"property_id": "FAIRFLD-JAI-001", "name": "Fairfield by Marriott Jaipur", "brand": "Fairfield", "total_rooms": 130, "location": "Ajmer Road, Jaipur", "lat": 26.9200, "lng": 75.7500, "rooms": {"luxury": 0, "premium": 35, "standard": 95}},
        ],
        # India — Pune
        "PNQ": [
            {"property_id": "MRIOTT-PNQ-001", "name": "Marriott Suites Pune", "brand": "Marriott", "total_rooms": 192, "location": "Senapati Bapat Road, Pune", "lat": 18.5348, "lng": 73.8372, "rooms": {"luxury": 24, "premium": 68, "standard": 100}},
            {"property_id": "JWMARR-PNQ-001", "name": "JW Marriott Pune", "brand": "JW Marriott", "total_rooms": 415, "location": "Senapati Bapat Road, Pune", "lat": 18.5365, "lng": 73.8310, "rooms": {"luxury": 85, "premium": 155, "standard": 175}},
            {"property_id": "WESTIN-PNQ-001", "name": "The Westin Pune", "brand": "Westin", "total_rooms": 230, "location": "Koregaon Park, Pune", "lat": 18.5362, "lng": 73.8996, "rooms": {"luxury": 30, "premium": 80, "standard": 120}},
            {"property_id": "COURTY-PNQ-001", "name": "Courtyard by Marriott Pune Hinjewadi", "brand": "Courtyard", "total_rooms": 175, "location": "Hinjewadi, Pune", "lat": 18.5900, "lng": 73.7400, "rooms": {"luxury": 0, "premium": 50, "standard": 125}},
            {"property_id": "FOURPT-PNQ-001", "name": "Four Points by Sheraton Pune", "brand": "Four Points", "total_rooms": 165, "location": "Nagar Road, Pune", "lat": 18.5700, "lng": 73.9100, "rooms": {"luxury": 0, "premium": 45, "standard": 120}},
        ],
        # India — Kolkata
        "CCU": [
            {"property_id": "MRIOTT-CCU-001", "name": "Marriott Kolkata", "brand": "Marriott", "total_rooms": 240, "location": "DLF IT Park, Kolkata", "lat": 22.5832, "lng": 88.4813, "rooms": {"luxury": 30, "premium": 80, "standard": 130}},
            {"property_id": "WESTIN-CCU-001", "name": "The Westin Kolkata Rajarhat", "brand": "Westin", "total_rooms": 210, "location": "New Town, Kolkata", "lat": 22.5795, "lng": 88.4680, "rooms": {"luxury": 28, "premium": 72, "standard": 110}},
            {"property_id": "JWMARR-CCU-001", "name": "JW Marriott Kolkata", "brand": "JW Marriott", "total_rooms": 280, "location": "Salt Lake, Kolkata", "lat": 22.5726, "lng": 88.4215, "rooms": {"luxury": 60, "premium": 100, "standard": 120}},
            {"property_id": "FOURPT-CCU-001", "name": "Four Points by Sheraton Kolkata", "brand": "Four Points", "total_rooms": 145, "location": "Park Street, Kolkata", "lat": 22.5480, "lng": 88.3590, "rooms": {"luxury": 0, "premium": 40, "standard": 105}},
        ],
    }

    props = location_properties.get(location.upper(), [])

    # If location not found, try partial matching (e.g., "HYD" in "MRIOTT-HYD-001")
    if not props:
        for loc_key, loc_props in location_properties.items():
            if location.upper() in loc_key or loc_key in location.upper():
                props = loc_props
                break

    results = []
    for prop in props:
        if prop["property_id"] == f"MRIOTT-{location.upper()}-001":
            continue
        estimated_occupancy = 0.65
        available = int(prop["total_rooms"] * (1 - estimated_occupancy))
        rooms = prop.get("rooms", {"luxury": 0, "premium": 0, "standard": prop["total_rooms"]})
        results.append({
            "property_id": prop["property_id"],
            "name": prop["name"],
            "brand": prop["brand"],
            "location": prop.get("address", prop.get("location", "")),
            "total_rooms": prop["total_rooms"],
            "estimated_available": available,
            "can_accommodate": available >= num_rooms,
            "room_types": rooms,
            "rooms_available": {
                "luxury": int(rooms["luxury"] * 0.35),
                "premium": int(rooms["premium"] * 0.35),
                "standard": int(rooms["standard"] * 0.35),
            },
            "google_maps_url": f"https://www.google.com/maps?q={prop.get('lat', 0)},{prop.get('lng', 0)}" if prop.get("lat") else None,
        })

    return results


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

    # ─── Concurrency Control for ACCEPT ───────────────────────────────────────
    inventory_result = None
    queue_result = None
    num_rooms = int(booking.get("num_rooms", 0))
    property_id = booking.get("property_id", "")
    event_date = booking.get("event_date", "")

    if negotiation_result["decision"] == "ACCEPT":
        inventory = InventoryManager()
        queue = BookingQueue()

        # Large bookings (100+ rooms) go through priority queue
        if queue.is_large_booking(num_rooms):
            queue_result = queue.enqueue(booking_id, num_rooms, property_id, event_date, priority=1)

        # Atomic room reservation — prevents double-booking
        inventory_result = inventory.reserve_rooms(property_id, event_date, num_rooms, booking_id)

        if not inventory_result.get("success"):
            # Insufficient inventory — cannot accept, escalate instead
            negotiation_result["decision"] = "ESCALATE"
            negotiation_result["message_to_client"] = (
                f"We apologize — while processing your booking for {num_rooms} rooms, "
                f"availability changed. Only {inventory_result.get('available', 0)} rooms remain. "
                f"Your request has been escalated to our sales team for priority handling."
            )
            new_status = BookingStatus.ESCALATED

    # Optimistic locking — conditional write to prevent concurrent status conflicts
    try:
        table.update_item(
            Key={"booking_id": booking_id, "version": int(booking["version"])},
            UpdateExpression="SET #s = :status, updated_at = :ts",
            ConditionExpression="#s <> :accepted_status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": new_status,
                ":ts": utc_now_iso(),
                ":accepted_status": BookingStatus.ACCEPTED,
            },
        )
    except Exception:
        # Another request already accepted this booking
        if negotiation_result["decision"] == "ACCEPT":
            return build_response(409, {
                "booking_id": booking_id,
                "error": "CONFLICT",
                "message": "This booking was already accepted by another concurrent request.",
            })

    if negotiation_result["decision"] == "ESCALATE":
        escalate_to_sales(booking, negotiation_result)

    # Send email notification for ALL scenarios (ACCEPT, COUNTER, ESCALATE)
    email_sent = False
    try:
        email_id = send_notification_email(booking, negotiation_result)
        email_sent = True
        table.update_item(
            Key={"booking_id": booking_id, "version": int(booking["version"])},
            UpdateExpression="SET notification_email_id = :eid",
            ExpressionAttributeValues={":eid": email_id},
        )
    except Exception:
        pass

    # Check property availability in the location
    availability = check_property_availability(booking)

    response_payload = {
        "booking_id": booking_id,
        "decision": negotiation_result["decision"],
        "message_to_client": negotiation_result["message_to_client"],
        "counter_proposal": negotiation_result.get("counter_proposal"),
        "turn_number": turn_number,
        "status": new_status,
        "email_sent": email_sent,
        "property_availability": availability,
        "concurrency": {
            "inventory_reserved": inventory_result.get("success") if inventory_result else None,
            "rooms_available_after": inventory_result.get("available_after") if inventory_result else None,
            "queued_for_processing": queue_result is not None,
            "queue_info": queue_result,
        },
    }

    if "body" in event:
        return build_response(200, response_payload)
    return response_payload
