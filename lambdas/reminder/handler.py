"""
GroupIQ Reminder Lambda
Scans bookings with event dates approaching (2 days before) and sends
reminder notifications to both the client and hotel operations team.

Triggered by: EventBridge scheduled rule (daily at 9 AM UTC)
              or manually via the web server endpoint.
"""
import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr

import sys
sys.path.insert(0, "/opt/python")
from utils import (
    build_response,
    utc_now_iso,
    get_dynamodb_resource,
    get_ses_client,
    get_sns_client,
    BookingStatus,
    DecimalEncoder,
)

BOOKINGS_TABLE = os.environ["BOOKINGS_TABLE"]
SES_SENDER_EMAIL = os.environ["SES_SENDER_EMAIL"]
ESCALATION_TOPIC_ARN = os.environ["ESCALATION_TOPIC_ARN"]
REMINDER_DAYS_BEFORE = int(os.environ.get("REMINDER_DAYS_BEFORE", "2"))


def get_upcoming_bookings(target_date: str) -> list:
    """Find all ACCEPTED bookings with event_date matching the target date."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)

    response = table.scan(
        FilterExpression=Attr("event_date").eq(target_date) & Attr("status").eq(BookingStatus.ACCEPTED),
    )
    return response.get("Items", [])


def get_all_upcoming_bookings(target_date: str) -> list:
    """Find all bookings (any active status) with event_date matching the target date."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(BOOKINGS_TABLE)

    active_statuses = [
        BookingStatus.ACCEPTED,
        BookingStatus.PROPOSAL_SENT,
        BookingStatus.NEGOTIATING,
        BookingStatus.INQUIRY_RECEIVED,
    ]

    all_bookings = []
    for status in active_statuses:
        response = table.scan(
            FilterExpression=Attr("event_date").eq(target_date) & Attr("status").eq(status),
        )
        all_bookings.extend(response.get("Items", []))

    return all_bookings


def send_client_reminder(booking: dict) -> str:
    """Send a reminder email to the client about their upcoming event."""
    ses = get_ses_client()

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1B1464; color: white; padding: 20px; text-align: center;">
            <h1>Event Reminder</h1>
            <p>Marriott International — GroupIQ</p>
        </div>

        <div style="padding: 20px;">
            <h2>Dear {booking['contact_name']},</h2>

            <p>This is a friendly reminder that your <strong>{booking['event_type'].title()}</strong>
            is coming up in <strong>{REMINDER_DAYS_BEFORE} days</strong>!</p>

            <div style="background:#f5f5f5; padding:15px; border-radius:5px; margin:15px 0;">
                <table style="width:100%;">
                    <tr><td><strong>Booking ID:</strong></td><td>{booking['booking_id']}</td></tr>
                    <tr><td><strong>Event Date:</strong></td><td>{booking['event_date']}</td></tr>
                    <tr><td><strong>Rooms Reserved:</strong></td><td>{booking['num_rooms']}</td></tr>
                    <tr><td><strong>Nights:</strong></td><td>{booking['num_nights']}</td></tr>
                    <tr><td><strong>Property:</strong></td><td>{booking['property_id']}</td></tr>
                    <tr><td><strong>Status:</strong></td><td>{booking['status']}</td></tr>
                </table>
            </div>

            <h3>Checklist Before Your Event:</h3>
            <ul>
                <li>Confirm final guest count with your coordinator</li>
                <li>Submit rooming list if not already provided</li>
                <li>Confirm AV and meeting space requirements</li>
                <li>Review F&B selections and dietary requirements</li>
                <li>Share arrival/departure schedule</li>
            </ul>

            <p>Your dedicated event coordinator is available to assist with any last-minute needs.</p>

            <div style="background:#D4EDDA; padding:15px; border-radius:5px; margin-top:20px;">
                <strong>Need to make changes?</strong>
                <p>Reply to this email or contact your event coordinator directly.</p>
            </div>

            <p style="margin-top:20px; color:#666; font-size:12px;">
                Powered by GroupIQ — AI-Assisted Group Booking Platform
            </p>
        </div>
    </body>
    </html>"""

    text_body = (
        f"Dear {booking['contact_name']},\n\n"
        f"Reminder: Your {booking['event_type']} is in {REMINDER_DAYS_BEFORE} days!\n\n"
        f"Booking ID: {booking['booking_id']}\n"
        f"Event Date: {booking['event_date']}\n"
        f"Rooms: {booking['num_rooms']} | Nights: {booking['num_nights']}\n\n"
        f"Please confirm your final guest count and any last-minute requirements.\n\n"
        f"— GroupIQ, Marriott International"
    )

    response = ses.send_email(
        Source=SES_SENDER_EMAIL,
        Destination={"ToAddresses": [booking["contact_email"]]},
        Message={
            "Subject": {"Data": f"Reminder: Your {booking['event_type'].title()} is in {REMINDER_DAYS_BEFORE} days — {booking['booking_id']}"},
            "Body": {
                "Html": {"Data": html_body},
                "Text": {"Data": text_body},
            },
        },
    )
    return response["MessageId"]


def send_ops_alert(bookings: list):
    """Send SNS alert to hotel operations team about upcoming events."""
    if not bookings:
        return

    sns = get_sns_client()

    summary_lines = []
    total_rooms = 0
    for b in bookings:
        rooms = int(b.get("num_rooms", 0))
        total_rooms += rooms
        summary_lines.append(
            f"  - {b['booking_id']} | {b['contact_name']} | {b['event_type']} | "
            f"{rooms} rooms x {b['num_nights']} nights | Status: {b['status']}"
        )

    message = (
        f"[GroupIQ OPERATIONS ALERT]\n\n"
        f"Events arriving in {REMINDER_DAYS_BEFORE} days:\n"
        f"Total bookings: {len(bookings)}\n"
        f"Total rooms needed: {total_rooms}\n\n"
        f"Details:\n" + "\n".join(summary_lines) + "\n\n"
        f"Please ensure:\n"
        f"  1. Room blocks are allocated and ready\n"
        f"  2. F&B orders are confirmed with kitchen\n"
        f"  3. Meeting/event spaces are set up\n"
        f"  4. Welcome packages prepared\n"
        f"  5. Front desk briefed on VIP arrivals\n"
    )

    sns.publish(
        TopicArn=ESCALATION_TOPIC_ARN,
        Subject=f"[GroupIQ OPS] {len(bookings)} events arriving in {REMINDER_DAYS_BEFORE} days — {total_rooms} rooms",
        Message=message,
    )


def lambda_handler(event, context):
    """
    Check for upcoming bookings and send reminders.

    Can be triggered by:
    - EventBridge scheduled rule (no event body needed)
    - Manual trigger via API with optional target_date override
    """
    target_date = event.get("target_date")
    if not target_date:
        reminder_date = datetime.now(timezone.utc) + timedelta(days=REMINDER_DAYS_BEFORE)
        target_date = reminder_date.strftime("%Y-%m-%d")

    upcoming_bookings = get_all_upcoming_bookings(target_date)

    if not upcoming_bookings:
        return build_response(200, {
            "message": "No upcoming bookings found",
            "target_date": target_date,
            "reminders_sent": 0,
        })

    reminders_sent = []
    errors = []

    for booking in upcoming_bookings:
        try:
            message_id = send_client_reminder(booking)
            reminders_sent.append({
                "booking_id": booking["booking_id"],
                "contact": booking["contact_name"],
                "email": booking["contact_email"],
                "message_id": message_id,
            })
        except Exception as e:
            errors.append({
                "booking_id": booking["booking_id"],
                "error": str(e),
            })

    try:
        send_ops_alert(upcoming_bookings)
    except Exception as e:
        errors.append({"ops_alert_error": str(e)})

    response_body = {
        "message": f"Reminders processed for {target_date}",
        "target_date": target_date,
        "reminders_sent": len(reminders_sent),
        "details": reminders_sent,
        "total_rooms": sum(int(b.get("num_rooms", 0)) for b in upcoming_bookings),
    }

    if errors:
        response_body["errors"] = errors

    return build_response(200, response_body)
