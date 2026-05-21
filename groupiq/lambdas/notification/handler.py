"""
GroupIQ Notification Lambda
Sends proposal emails via SES and handles escalation notifications via SNS.
"""
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

import sys
sys.path.insert(0, "/opt/python")
from utils import (
    build_response,
    utc_now_iso,
    get_dynamodb_resource,
    get_s3_client,
    get_ses_client,
    get_sns_client,
    BookingStatus,
    DecimalEncoder,
)

PROPOSALS_BUCKET = os.environ["PROPOSALS_BUCKET"]
SES_SENDER_EMAIL = os.environ["SES_SENDER_EMAIL"]
ESCALATION_TOPIC_ARN = os.environ["ESCALATION_TOPIC_ARN"]


def get_proposal_from_s3(s3_key: str) -> dict:
    """Retrieve the proposal JSON from S3."""
    s3 = get_s3_client()
    response = s3.get_object(Bucket=PROPOSALS_BUCKET, Key=s3_key)
    return json.loads(response["Body"].read().decode("utf-8"))


def send_proposal_email(booking: dict, proposal: dict) -> str:
    """Send a formatted proposal email to the client."""
    ses = get_ses_client()

    # Build the HTML email body
    tiers_html = ""
    for tier in proposal.get("room_block", {}).get("tiers", []):
        tiers_html += f"""
        <tr>
            <td style="padding:8px; border:1px solid #ddd;">{tier.get('name', 'Standard')}</td>
            <td style="padding:8px; border:1px solid #ddd;">${tier.get('rate', 'N/A')}/night</td>
            <td style="padding:8px; border:1px solid #ddd;">{tier.get('commitment', 'N/A')}</td>
        </tr>"""

    fnb_html = ""
    for pkg in proposal.get("fnb_packages", []):
        fnb_html += f"<li><strong>{pkg.get('name', 'Package')}</strong>: ${pkg.get('price_per_person', 'N/A')}/person — {pkg.get('description', '')}</li>"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
        <div style="background: #1B1464; color: white; padding: 20px; text-align: center;">
            <h1>GroupIQ Proposal</h1>
            <p>Marriott International</p>
        </div>

        <div style="padding: 20px;">
            <h2>Dear {booking['contact_name']},</h2>
            <p>{proposal.get('executive_summary', 'Thank you for your group booking inquiry.')}</p>

            <h3>Room Block Options</h3>
            <table style="width:100%; border-collapse:collapse;">
                <tr style="background:#f5f5f5;">
                    <th style="padding:8px; border:1px solid #ddd;">Tier</th>
                    <th style="padding:8px; border:1px solid #ddd;">Rate</th>
                    <th style="padding:8px; border:1px solid #ddd;">Commitment</th>
                </tr>
                {tiers_html}
            </table>

            <h3>Food & Beverage Packages</h3>
            <ul>{fnb_html}</ul>

            <h3>Total Investment</h3>
            <p style="font-size: 18px; color: #1B1464;">
                <strong>${proposal.get('total_investment', {}).get('recommended', 'N/A')}</strong>
                (range: ${proposal.get('total_investment', {}).get('min', 'N/A')} — ${proposal.get('total_investment', {}).get('max', 'N/A')})
            </p>

            <div style="background:#FFF3CD; padding:15px; border-radius:5px; margin-top:20px;">
                <strong>This proposal is valid for 7 days.</strong>
                <p>Reply to discuss, counter-propose, or accept.</p>
            </div>

            <p style="margin-top:20px; color:#666; font-size:12px;">
                Powered by GroupIQ — AI-Assisted Group Booking Platform
            </p>
        </div>
    </body>
    </html>"""

    response = ses.send_email(
        Source=SES_SENDER_EMAIL,
        Destination={"ToAddresses": [booking["contact_email"]]},
        Message={
            "Subject": {"Data": f"Your Group Booking Proposal — {booking['event_type'].title()} ({booking['event_date']})"},
            "Body": {
                "Html": {"Data": html_body},
                "Text": {"Data": f"Dear {booking['contact_name']},\n\nYour group booking proposal is ready. Total recommended investment: ${proposal.get('total_investment', {}).get('recommended', 'N/A')}.\n\nThis proposal is valid for 7 days.\n\nReply to negotiate or accept."},
            },
        },
    )
    return response["MessageId"]


def lambda_handler(event, context):
    """Send proposal notification to the client."""
    booking_id = event.get("booking_id")
    action = event.get("action", "send_proposal")

    if not booking_id:
        return {"error": "booking_id is required", "status": "FAILED"}

    dynamodb = get_dynamodb_resource()
    bookings_table = dynamodb.Table(os.environ.get("BOOKINGS_TABLE", "groupiq-bookings-prod"))

    response = bookings_table.query(
        KeyConditionExpression=Key("booking_id").eq(booking_id),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return {"error": "Booking not found", "status": "FAILED"}

    booking = items[0]

    if action == "send_proposal":
        s3_key = booking.get("proposal_s3_key")
        if not s3_key:
            return {"error": "No proposal generated yet", "status": "FAILED"}

        proposal = get_proposal_from_s3(s3_key)
        message_id = send_proposal_email(booking, proposal)

        # Update status
        bookings_table.update_item(
            Key={"booking_id": booking_id, "version": int(booking["version"])},
            UpdateExpression="SET #s = :status, email_message_id = :mid, updated_at = :ts",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": BookingStatus.PROPOSAL_SENT,
                ":mid": message_id,
                ":ts": utc_now_iso(),
            },
        )

        return {
            "booking_id": booking_id,
            "status": BookingStatus.PROPOSAL_SENT,
            "email_message_id": message_id,
        }

    elif action == "send_counter_response":
        message = event.get("message_to_client", "")
        ses = get_ses_client()
        ses.send_email(
            Source=SES_SENDER_EMAIL,
            Destination={"ToAddresses": [booking["contact_email"]]},
            Message={
                "Subject": {"Data": f"Re: Your Group Booking — Update on {booking['booking_id']}"},
                "Body": {"Text": {"Data": message}},
            },
        )
        return {"booking_id": booking_id, "status": "COUNTER_SENT"}

    return {"error": f"Unknown action: {action}", "status": "FAILED"}
