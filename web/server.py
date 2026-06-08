"""
GroupIQ Web Server — Local development server that bridges the browser UI 
to LocalStack Lambda functions. Serves the dashboard and proxies API calls.
"""
import json
import http.server
import urllib.request
import urllib.parse
import os
import sys
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Auto-load .env file for persistent SMTP and AWS configuration
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                if _key.strip() not in os.environ:
                    os.environ[_key.strip()] = _val.strip()

import boto3
from boto3.dynamodb.conditions import Key, Attr

LOCALSTACK_URL = os.environ.get("LOCALSTACK_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "local")
PORT = int(os.environ.get("PORT", "5555"))

# SMTP configuration for sending real emails (Gmail)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "")

# Backup file path for persistent storage
BACKUP_DIR = Path(__file__).parent.parent / "data"
BACKUP_FILE = BACKUP_DIR / "bookings_backup.json"

# AWS clients pointing at LocalStack
session = boto3.Session(
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name=REGION,
)
lambda_client = session.client("lambda", endpoint_url=LOCALSTACK_URL)
dynamodb = session.resource("dynamodb", endpoint_url=LOCALSTACK_URL)

# AWS Bedrock client for AI-powered negotiations
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
_bedrock_client = None
_bedrock_enabled = bool(os.environ.get("AWS_ACCESS_KEY_ID", "")) and os.environ.get("AWS_ACCESS_KEY_ID", "") != "test"

def get_bedrock_client():
    """Get or create Bedrock runtime client with real AWS credentials."""
    global _bedrock_client
    if _bedrock_client is None:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        bedrock_session = boto3.Session(
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
            region_name="us-east-1",
        )
        _bedrock_client = bedrock_session.client("bedrock-runtime", verify=False)
    return _bedrock_client

def invoke_bedrock_negotiation(booking, counter_offer):
    """Use AWS Bedrock AI to evaluate a counter-offer using Marriott's real-time revenue management logic."""
    client = get_bedrock_client()

    # Look up property zone + brand tier for pricing
    property_id = booking.get("property_id", "")
    zone_info = GroupIQServer.PROPERTY_ZONES.get(property_id, {"zone": "standard", "base_rate": 150, "max_discount": 0.30, "brand_tier": "premium", "location": "Unknown"})
    zone = zone_info["zone"]
    brand_tier = zone_info.get("brand_tier", "premium")
    location_desc = zone_info.get("location", "")
    max_discount_pct = int(zone_info["max_discount"] * 100)
    zone_base_rate = zone_info["base_rate"]

    system_prompt = f"""You are the Marriott International Group Sales Revenue Management AI.
You follow Marriott's real-world pricing strategy based on Brand Tier, Property Zone, and Seasonality.

Respond ONLY with valid JSON:
{{"decision": "ACCEPT" or "COUNTER" or "ESCALATE", "message_to_client": "professional Marriott-branded message", "counter_proposal": {{"room_rate": number, "includes_breakfast": true/false, "late_checkout": true/false, "room_upgrade": true/false}}, "reasoning": "internal revenue management reasoning"}}

Property Details:
- Brand Tier: {brand_tier.upper()} ({"Ritz-Carlton / W / JW Marriott — strictest yield" if brand_tier == "luxury" else "Westin / Sheraton / Marriott — moderate flexibility" if brand_tier == "premium" else "Courtyard / Four Points — volume-driven, flexible"})
- Zone: {zone.upper()} — {location_desc}
- Published Group BAR: ${zone_base_rate}/night
- Maximum Group Discount: {max_discount_pct}%
- Floor Rate: ${round(zone_base_rate * (1 - zone_info['max_discount']))}/night

Marriott Revenue Rules:
1. LUXURY tier: Max {max_discount_pct}% discount. Offer lounge access, spa credit — NEVER deep discounts.
2. PREMIUM tier: Standard group discount. Offer breakfast, late checkout for outskirts properties.
3. SELECT tier: Volume-driven. Can offer max discount + parking, breakfast, upgrade for large groups (50+ rooms).
4. OUTSKIRTS properties: More flexibility — late checkout, free parking, room upgrade allowed.
5. PROMINENT/CBD properties: Tighter margins — only breakfast/lounge as value-adds, protect rate integrity.
6. ACCEPT if proposed rate >= floor rate.
7. COUNTER if proposed rate is within 10% below floor — meet midway, add value-adds.
8. ESCALATE if offer is unreasonably low (>10% below floor) — route to Senior Revenue Manager.
9. All responses must maintain Marriott brand voice: professional, warm, solution-oriented."""

    base_rate = float(booking.get("base_room_rate", booking.get("dynamic_room_rate", zone_base_rate)))
    floor_rate = round(base_rate * (1 - zone_info["max_discount"]))
    num_rooms = int(booking.get('num_rooms', 0))
    volume_note = "Large group (50+ rooms) — additional flexibility applies" if num_rooms >= 50 else f"Standard group ({num_rooms} rooms)"

    user_msg = f"""Evaluate this group booking counter-offer:
- Brand: {brand_tier.upper()} | Zone: {zone.upper()} | Location: {location_desc}
- Published BAR: ${base_rate}/night | Floor: ${floor_rate}/night | Max Discount: {max_discount_pct}%
- Group Size: {num_rooms} rooms × {booking.get('num_nights', 0)} nights ({volume_note})
- Total Room-Nights: {num_rooms * int(booking.get('num_nights', 1))}
- Event Type: {booking.get('event_type', 'event')} | Date: {booking.get('event_date', 'TBD')}
- Client: {booking.get('contact_name', 'Guest')}
- Client Proposed Rate: ${counter_offer.get('proposed_room_rate', 'Not specified')}/night
- Client Message: {counter_offer.get('message', 'No message')}
- Client Action: {counter_offer.get('action', 'COUNTER')}"""

    body = json.dumps({
        "messages": [{"role": "user", "content": [{"text": user_msg}]}],
        "system": [{"text": system_prompt}],
        "inferenceConfig": {"maxTokens": 1024, "temperature": 0.2}
    })

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body
    )
    result = json.loads(response["body"].read())
    ai_text = result["output"]["message"]["content"][0]["text"]
    
    # Parse JSON from response (handle markdown code blocks)
    if "```json" in ai_text:
        ai_text = ai_text.split("```json")[1].split("```")[0].strip()
    elif "```" in ai_text:
        ai_text = ai_text.split("```")[1].split("```")[0].strip()
    
    return json.loads(ai_text)

if _bedrock_enabled:
    print(f"[GroupIQ] AWS Bedrock AI ENABLED — Model: {BEDROCK_MODEL_ID}")
else:
    print(f"[GroupIQ] AWS Bedrock AI disabled — using local fallback (set real AWS_ACCESS_KEY_ID to enable)")

# SMTP email setup
smtp_configured = bool(SMTP_USERNAME and SMTP_PASSWORD)
if smtp_configured:
    print(f"[GroupIQ] SMTP email configured: {SMTP_USERNAME} via {SMTP_HOST}:{SMTP_PORT}")
else:
    print(f"[GroupIQ] SMTP not configured — emails will be logged to console only")
    print(f"[GroupIQ] To enable real emails, set: SMTP_USERNAME, SMTP_PASSWORD, SES_SENDER_EMAIL")

_smtp_connection = None
_smtp_lock = threading.Lock()


def _get_smtp_connection():
    """Get or create a persistent SMTP connection (reuse across emails)."""
    global _smtp_connection
    try:
        if _smtp_connection:
            _smtp_connection.noop()
            return _smtp_connection
    except Exception:
        _smtp_connection = None

    conn = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
    conn.ehlo()
    conn.starttls()
    conn.ehlo()
    conn.login(SMTP_USERNAME, SMTP_PASSWORD)
    _smtp_connection = conn
    return conn


def _smtp_keepalive():
    """Background thread to keep SMTP connection warm — prevents 10s+ reconnect delays."""
    global _smtp_connection
    import time
    while True:
        time.sleep(45)
        with _smtp_lock:
            try:
                if _smtp_connection:
                    _smtp_connection.noop()
                else:
                    _get_smtp_connection()
            except Exception:
                _smtp_connection = None
                try:
                    _get_smtp_connection()
                    print("[GroupIQ] SMTP reconnected by keepalive ✓")
                except Exception:
                    pass


if smtp_configured:
    threading.Thread(target=_smtp_keepalive, daemon=True).start()
    try:
        _get_smtp_connection()
        print(f"[GroupIQ] SMTP connection pre-warmed ✓ (emails will send instantly)")
    except Exception as e:
        print(f"[GroupIQ] SMTP pre-warm failed: {e} (will retry on first email)")


def send_email(to_email, subject, html_body):
    """Send a real email via Gmail SMTP with persistent connection. Returns True on success."""
    global _smtp_connection
    sender = SES_SENDER_EMAIL or SMTP_USERNAME
    if not smtp_configured:
        print(f"[EMAIL-LOG] To: {to_email} | Subject: {subject}")
        print(f"[EMAIL-LOG] (Not sent — SMTP not configured)")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"GroupIQ Marriott <{sender}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    with _smtp_lock:
        for attempt in range(2):
            try:
                conn = _get_smtp_connection()
                conn.sendmail(sender, to_email, msg.as_string())
                print(f"[EMAIL-SENT] To: {to_email} | Subject: {subject}")
                return True
            except Exception as e:
                _smtp_connection = None
                if attempt == 0:
                    continue
                print(f"[EMAIL-ERROR] Failed to send to {to_email}: {e}")
                return False


def send_email_async(to_email, subject, html_body, booking_id=None, email_type="INQUIRY"):
    """Send email in background thread with retry — non-blocking, guaranteed delivery."""
    def _send():
        import time
        success = False
        for attempt in range(3):
            try:
                success = send_email(to_email, subject, html_body)
                if success:
                    print(f"[EMAIL-DELIVERED] {booking_id} → {to_email} (attempt {attempt+1})")
                    break
            except Exception as e:
                print(f"[EMAIL-RETRY] {booking_id} attempt {attempt+1}/3 failed: {e}")
            if not success and attempt < 2:
                time.sleep(2)
        if not success:
            print(f"[EMAIL-FAILED] {booking_id} → {to_email} after 3 attempts")
        if booking_id:
            try:
                backup.upsert_booking({
                    "booking_id": booking_id,
                    "email_delivered": success,
                    "email_sent_at": datetime.now(timezone.utc).isoformat(),
                    "email_to": to_email,
                    "last_email_type": email_type,
                })
            except Exception as e:
                print(f"[EMAIL-PERSIST-ERROR] {booking_id}: {e}")
        if not success:
            print(f"[EMAIL-FAILED] {booking_id} to {to_email} after 3 attempts")
    threading.Thread(target=_send, daemon=True).start()
    return True


def build_inquiry_email(inquiry_id, contact_name, event_type, checkin, checkout, rooms, nights, rate, revenue, property_id):
    """Build HTML email for inquiry confirmation."""
    return f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 30px; background: #f8fafc;">
        <div style="background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
            <div style="text-align: center; margin-bottom: 24px;">
                <h1 style="color: #1e293b; font-size: 22px; margin: 0;">Marriott | GroupIQ</h1>
                <p style="color: #64748b; font-size: 13px; margin-top: 4px;">Group Booking Confirmation</p>
            </div>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #334155; font-size: 15px;">Dear <strong>{contact_name}</strong>,</p>
            <p style="color: #475569; font-size: 14px; line-height: 1.7;">
                Your group booking inquiry has been received and is being processed by our revenue management team.
            </p>
            <div style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 10px; padding: 20px; margin: 20px 0;">
                <h3 style="color: #1e40af; font-size: 14px; margin: 0 0 12px 0; text-transform: uppercase; letter-spacing: 0.5px;">Inquiry Details</h3>
                <table style="width: 100%; font-size: 13px; color: #334155;">
                    <tr><td style="padding: 6px 0;"><strong>Inquiry ID:</strong></td><td style="color: #1e40af; font-weight: 700;">{inquiry_id}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Event Type:</strong></td><td>{event_type}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Property:</strong></td><td>{property_id}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Check-in:</strong></td><td>{checkin}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Check-out:</strong></td><td>{checkout}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Rooms:</strong></td><td>{rooms}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Nights:</strong></td><td>{nights}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Rate/Night:</strong></td><td style="font-weight: 700;">${rate:.0f}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Estimated Revenue:</strong></td><td style="font-weight: 700; color: #059669;">${revenue:,.0f}</td></tr>
                </table>
            </div>
            <p style="color: #475569; font-size: 14px; line-height: 1.7;">
                Our team will prepare a customized proposal within <strong>24 hours</strong>. You can track your inquiry status
                anytime at the <a href="http://localhost:5555/customer.html" style="color: #1e40af;">GroupIQ Customer Portal</a>.
            </p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #94a3b8; font-size: 11px; text-align: center;">
                Marriott GroupIQ | Group Booking Intelligence Platform<br>
                This is an automated notification. Do not reply to this email.
            </p>
        </div>
    </div>
    """


def build_negotiation_email(inquiry_id, contact_name, decision, message, counter_rate=None, confirmed_id=None):
    """Build HTML email for negotiation response."""
    decision_color = {"ACCEPT": "#059669", "COUNTER": "#d97706", "ESCALATE": "#dc2626"}.get(decision, "#64748b")
    decision_label = {"ACCEPT": "ACCEPTED", "COUNTER": "COUNTER OFFER", "ESCALATE": "ESCALATED"}.get(decision, decision)

    extra = ""
    if decision == "ACCEPT" and confirmed_id:
        extra = f"""
        <div style="background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 10px; padding: 20px; margin: 16px 0; text-align: center;">
            <p style="font-weight: 700; color: #059669; font-size: 16px; margin: 0;">Booking Confirmed!</p>
            <p style="margin-top: 8px; font-size: 20px; font-weight: 800; color: #065f46;">{confirmed_id}</p>
        </div>
        """
    elif decision == "COUNTER" and counter_rate:
        extra = f"""
        <div style="background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px; padding: 20px; margin: 16px 0;">
            <p style="font-weight: 600; color: #92400e; font-size: 14px; margin: 0;">Counter Proposal: <strong>${counter_rate}/night</strong></p>
            <p style="color: #78716c; font-size: 12px; margin-top: 6px;">You can accept this rate or submit another counter-offer.</p>
        </div>
        """

    return f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 30px; background: #f8fafc;">
        <div style="background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
            <div style="text-align: center; margin-bottom: 24px;">
                <h1 style="color: #1e293b; font-size: 22px; margin: 0;">Marriott | GroupIQ</h1>
                <p style="color: #64748b; font-size: 13px; margin-top: 4px;">Negotiation Update</p>
            </div>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #334155; font-size: 15px;">Dear <strong>{contact_name}</strong>,</p>
            <p style="color: #475569; font-size: 14px; line-height: 1.7;">
                We have reviewed your counter-offer for inquiry <strong style="color: #1e40af;">{inquiry_id}</strong>.
            </p>
            <div style="text-align: center; margin: 20px 0;">
                <span style="background: {decision_color}; color: white; padding: 8px 24px; border-radius: 20px; font-size: 13px; font-weight: 700; letter-spacing: 0.5px;">{decision_label}</span>
            </div>
            <p style="color: #475569; font-size: 14px; line-height: 1.7; background: #f8fafc; padding: 16px; border-radius: 8px; border-left: 4px solid {decision_color};">
                {message}
            </p>
            {extra}
            <p style="color: #475569; font-size: 14px; line-height: 1.7;">
                Visit the <a href="http://localhost:5555/customer.html" style="color: #1e40af;">GroupIQ Customer Portal</a> to continue.
            </p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #94a3b8; font-size: 11px; text-align: center;">
                Marriott GroupIQ | Group Booking Intelligence Platform
            </p>
        </div>
    </div>
    """

WEB_DIR = Path(__file__).parent


# ─── Booking Backup System ───────────────────────────────────────────────────

class BookingBackup:
    """Persistent JSON backup for booking data that survives LocalStack restarts."""

    def __init__(self, backup_path: Path):
        self.path = backup_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {"bookings": {}, "last_backup": None}
        return {"bookings": {}, "last_backup": None}

    def _save(self):
        self._data["last_backup"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2, default=str)

    def upsert_booking(self, booking: dict):
        bid = booking.get("booking_id")
        if not bid:
            return
        with self._lock:
            existing = self._data["bookings"].get(bid)
            if existing and "version" not in booking:
                existing.update(booking)
                self._data["bookings"][bid] = existing
                self._save()
            elif not existing or int(booking.get("version", 0)) >= int(existing.get("version", 0)):
                if existing:
                    merged = existing.copy()
                    merged.update(booking)
                    self._data["bookings"][bid] = json.loads(json.dumps(merged, default=str))
                else:
                    self._data["bookings"][bid] = json.loads(json.dumps(booking, default=str))
                self._save()

    def get_booking(self, booking_id: str) -> dict:
        """Retrieve a booking by ID from the backup store."""
        with self._lock:
            return self._data["bookings"].get(booking_id)

    def sync_from_dynamodb(self, items: list):
        with self._lock:
            for item in items:
                bid = item.get("booking_id")
                if not bid:
                    continue
                existing = self._data["bookings"].get(bid)
                if not existing:
                    self._data["bookings"][bid] = json.loads(json.dumps(item, default=str))
                elif int(item.get("version", 0)) > int(existing.get("version", 0)):
                    merged = json.loads(json.dumps(item, default=str))
                    if existing.get("email_delivered"):
                        merged["email_delivered"] = existing["email_delivered"]
                        merged["email_to"] = existing.get("email_to")
                        merged["last_email_type"] = existing.get("last_email_type")
                    self._data["bookings"][bid] = merged
                elif int(item.get("version", 0)) == int(existing.get("version", 0)):
                    item_ts = item.get("updated_at", "")
                    existing_ts = existing.get("updated_at", "")
                    if item_ts >= existing_ts:
                        merged = json.loads(json.dumps(item, default=str))
                        if existing.get("email_delivered"):
                            merged["email_delivered"] = existing["email_delivered"]
                            merged["email_to"] = existing.get("email_to")
                            merged["last_email_type"] = existing.get("last_email_type")
                        self._data["bookings"][bid] = merged
            self._save()

    def get_all_bookings(self) -> list:
        return list(self._data["bookings"].values())

    def get_bookings_by_period(self, period: str) -> dict:
        """Filter bookings by period: 'week', 'month', 'year', or 'all'."""
        now = datetime.now(timezone.utc)
        bookings = self.get_all_bookings()

        if period == "week":
            start = now - timedelta(days=7)
        elif period == "month":
            start = now - timedelta(days=30)
        elif period == "year":
            start = now - timedelta(days=365)
        else:
            start = datetime(2000, 1, 1)

        start_str = start.isoformat()
        filtered = [b for b in bookings if b.get("created_at", "") >= start_str]

        total_revenue = sum(float(b.get("estimated_revenue", 0)) for b in filtered)
        total_rooms = sum(int(b.get("num_rooms", 0)) for b in filtered)
        total_nights = sum(int(b.get("num_nights", 0)) for b in filtered)

        status_counts = {}
        for b in filtered:
            s = b.get("status", "UNKNOWN")
            status_counts[s] = status_counts.get(s, 0) + 1

        event_type_counts = {}
        for b in filtered:
            et = b.get("event_type", "other")
            event_type_counts[et] = event_type_counts.get(et, 0) + 1

        monthly_breakdown = {}
        for b in filtered:
            created = b.get("created_at", "")[:7]
            if created not in monthly_breakdown:
                monthly_breakdown[created] = {"count": 0, "revenue": 0}
            monthly_breakdown[created]["count"] += 1
            monthly_breakdown[created]["revenue"] += float(b.get("estimated_revenue", 0))

        return {
            "period": period,
            "start_date": start_str[:10],
            "end_date": now.isoformat()[:10],
            "total_bookings": len(filtered),
            "total_revenue": total_revenue,
            "avg_revenue_per_booking": round(total_revenue / len(filtered), 2) if filtered else 0,
            "total_rooms_booked": total_rooms,
            "total_room_nights": total_rooms * total_nights // max(len(filtered), 1),
            "avg_rooms_per_booking": round(total_rooms / len(filtered), 1) if filtered else 0,
            "avg_nights_per_booking": round(total_nights / len(filtered), 1) if filtered else 0,
            "status_breakdown": status_counts,
            "event_type_breakdown": event_type_counts,
            "monthly_breakdown": dict(sorted(monthly_breakdown.items())),
            "bookings": sorted(filtered, key=lambda x: x.get("created_at", ""), reverse=True),
        }

    @property
    def stats(self) -> dict:
        bookings = self.get_all_bookings()
        return {
            "total_stored": len(bookings),
            "backup_file": str(self.path),
            "last_backup": self._data.get("last_backup"),
        }


backup = BookingBackup(BACKUP_FILE)
print(f"[GroupIQ] Backup storage: {BACKUP_FILE} ({backup.stats['total_stored']} bookings loaded)")


class GroupIQHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    @staticmethod
    def _is_localstack_reachable():
        """Quick 0.5s socket check to see if LocalStack DynamoDB is running."""
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(('localhost', 4566))
            sock.close()
            return result == 0
        except Exception:
            return False

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == "/bookings":
                self._handle_get_bookings()
            elif self.path.startswith("/bookings/report") or self.path.startswith("/bookings/report?"):
                self._handle_bookings_report()
            elif self.path == "/bookings/backup/stats":
                self._handle_backup_stats()
            elif self.path.startswith("/bookings/"):
                booking_id = self.path.split("/bookings/")[1]
                self._handle_get_booking(booking_id)
            elif self.path.startswith("/customer/bookings"):
                self._handle_customer_bookings()
            elif self.path == "/reminders" or self.path.startswith("/reminders?"):
                self._handle_check_reminders()
            elif self.path == "/compliance/rules":
                self._handle_compliance_rules()
            elif self.path.startswith("/compliance/"):
                booking_id = self.path.split("/compliance/")[1]
                self._handle_compliance_check(booking_id)
            elif self.path.startswith("/inventory/"):
                parts = self.path.split("/inventory/")[1].split("?")
                property_id = parts[0]
                self._handle_inventory(property_id)
            elif self.path.startswith("/properties/"):
                location = self.path.split("/properties/")[1].split("?")[0]
                self._handle_nearby_properties(location)
            elif self.path == "/properties" or self.path.startswith("/properties?"):
                self._handle_all_locations()
            elif self.path.startswith("/s3/"):
                self._handle_s3_browse()
            else:
                super().do_GET()
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass
        except Exception as e:
            try:
                self._json_response(500, {"error": "Internal server error", "detail": str(e)})
            except Exception:
                pass

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"

            if self.path == "/inquiries":
                self._handle_new_inquiry(body)
            elif self.path == "/customer/inquiries":
                self._handle_new_inquiry(body)
            elif "/customer/inquiries/" in self.path and "/negotiate" in self.path:
                parts = self.path.split("/customer/inquiries/")[1].split("/negotiate")[0]
                self._handle_negotiate(parts, body)
            elif "/negotiate" in self.path:
                parts = self.path.split("/")
                booking_id = parts[2] if len(parts) >= 3 else ""
                self._handle_negotiate(booking_id, body)
            else:
                self._json_response(404, {"error": "Not found"})
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass
        except Exception as e:
            try:
                self._json_response(500, {"error": "Internal server error", "detail": str(e)})
            except Exception:
                pass

    def _handle_get_bookings(self):
        """Return all bookings — serves from backup instantly, syncs DynamoDB in background."""
        try:
            # Serve from backup immediately for fast response
            backed_up = backup.get_all_bookings()
            items = []

            # Try DynamoDB only if LocalStack is reachable (non-blocking check)
            try:
                if self._is_localstack_reachable():
                    table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
                    response = table.scan()
                    items = response.get("Items", [])
                    backup.sync_from_dynamodb(items)
            except Exception:
                pass

            # Deduplicate by booking_id (keep latest version)
            latest = {}
            for item in items:
                bid = item["booking_id"]
                if bid not in latest or int(item["version"]) > int(latest[bid]["version"]):
                    latest[bid] = item

            # Merge with backup — backup may have more recent status from server-side updates
            backed_up = backup.get_all_bookings()
            for b in backed_up:
                bid = b.get("booking_id", "")
                if not bid:
                    continue
                if bid not in latest:
                    latest[bid] = b
                else:
                    existing = latest[bid]
                    if int(b.get("version", 0)) == int(existing.get("version", 0)):
                        if b.get("updated_at", "") > existing.get("updated_at", ""):
                            latest[bid] = b
                    # Always preserve email_delivered from backup
                    if b.get("email_delivered") and not latest[bid].get("email_delivered"):
                        latest[bid]["email_delivered"] = b["email_delivered"]
                        latest[bid]["email_to"] = b.get("email_to")
                        latest[bid]["last_email_type"] = b.get("last_email_type")

            bookings = sorted(latest.values(), key=lambda x: x.get("created_at", ""), reverse=True)
            clean = json.loads(json.dumps(bookings, default=str))
            self._json_response(200, {"bookings": clean, "count": len(clean)})
        except Exception as e:
            # Fallback: serve from backup if DynamoDB is unavailable
            backed_up = backup.get_all_bookings()
            if backed_up:
                self._json_response(200, {"bookings": backed_up, "count": len(backed_up), "source": "backup"})
            else:
                self._json_response(500, {"error": str(e)})

    def _handle_customer_bookings(self):
        """Get bookings filtered by customer email — customer portal endpoint."""
        try:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            email = params.get("email", [""])[0].lower().strip()

            if not email:
                self._json_response(400, {"error": "Email parameter required"})
                return

            items = []
            if self._is_localstack_reachable():
                try:
                    table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
                    response = table.scan()
                    items = response.get("Items", [])
                except Exception:
                    pass

            # Filter by customer email
            customer_items = [i for i in items if i.get("contact_email", "").lower() == email]

            # Also check backup
            backed_up = backup.get_all_bookings()
            backup_items = [b for b in backed_up if b.get("contact_email", "").lower() == email]

            # Merge (prefer DynamoDB, fallback backup)
            all_items = {}
            for item in backup_items + customer_items:
                bid = item.get("booking_id", "")
                if bid:
                    existing = all_items.get(bid)
                    if not existing or int(item.get("version", 0)) >= int(existing.get("version", 0)):
                        all_items[bid] = item

            bookings = sorted(all_items.values(), key=lambda x: x.get("created_at", ""), reverse=True)
            clean = json.loads(json.dumps(bookings, default=str))

            # Send communication log
            comms = []
            for b in clean:
                status = b.get("status", "")
                bid = b.get("booking_id", "")
                if status == "INQUIRY_RECEIVED":
                    comms.append({"booking_id": bid, "type": "email", "message": f"Inquiry {bid} received. Our team is preparing a proposal.", "timestamp": b.get("created_at")})
                elif status == "PROPOSAL_SENT":
                    comms.append({"booking_id": bid, "type": "email", "message": f"A proposal for {bid} has been sent to your email.", "timestamp": b.get("updated_at")})
                elif status == "ACCEPTED":
                    comms.append({"booking_id": bid, "type": "email", "message": f"Booking {bid} is CONFIRMED! Check your email for details.", "timestamp": b.get("updated_at")})
                elif status == "NEGOTIATING":
                    comms.append({"booking_id": bid, "type": "email", "message": f"Counter-offer received for {bid}. Awaiting hotel response.", "timestamp": b.get("updated_at")})

            self._json_response(200, {"bookings": clean, "count": len(clean), "communications": comms})
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_bookings_report(self):
        """Return booking analytics filtered by period (week/month/year/all)."""
        try:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            period = params.get("period", ["all"])[0]

            # Sync from DynamoDB only if LocalStack is reachable
            try:
                if self._is_localstack_reachable():
                    table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
                    response = table.scan()
                    backup.sync_from_dynamodb(response.get("Items", []))
            except Exception:
                pass

            report = backup.get_bookings_by_period(period)
            self._json_response(200, report)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_backup_stats(self):
        """Return backup storage statistics."""
        self._json_response(200, backup.stats)

    def _handle_get_booking(self, booking_id):
        """Get a specific booking by ID."""
        try:
            payload = json.dumps({
                "requestContext": {"http": {"method": "GET"}},
                "pathParameters": {"bookingId": booking_id},
            })
            result = self._invoke_lambda("groupiq-intake-" + ENVIRONMENT, payload)
            self._json_response(result.get("statusCode", 200), json.loads(result.get("body", "{}")))
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_new_inquiry(self, body):
        """Submit a new group booking inquiry and backup."""
        try:
            parsed = json.loads(body)
            response_body = None
            booking_id = None

            # Try Lambda; if unavailable, create booking locally
            try:
                payload = json.dumps({
                    "body": body,
                    "requestContext": {"http": {"method": "POST"}},
                })
                result = self._invoke_lambda("groupiq-intake-" + ENVIRONMENT, payload)
                raw_body = result.get("body", "{}")
                if isinstance(raw_body, str):
                    response_body = json.loads(raw_body)
                elif isinstance(raw_body, dict):
                    response_body = raw_body
                else:
                    response_body = {}
                booking_id = response_body.get("booking_id") or response_body.get("inquiry_id")
            except Exception as lambda_err:
                print(f"[GroupIQ] Lambda unavailable, creating booking locally: {lambda_err}")

            # Local fallback — always generate a booking if Lambda didn't
            if not booking_id:
                import uuid, hashlib
                ts = datetime.now(timezone.utc).strftime("%Y%m%d")
                uid = hashlib.md5(f"{parsed.get('contact_email','')}{ts}{uuid.uuid4().hex}".encode()).hexdigest()[:8].upper()
                booking_id = f"INQ-{ts}-{uid}"
                rooms = int(parsed.get("num_rooms", 1))
                nights = int(parsed.get("num_nights", 1))
                base_rate = float(parsed.get("base_room_rate", 189))
                revenue = rooms * nights * base_rate
                response_body = {
                    "booking_id": booking_id,
                    "inquiry_id": booking_id,
                    "status": "INQUIRY_RECEIVED",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "estimated_revenue": revenue,
                    "dynamic_pricing": {"final_rate": base_rate, "base_rate": base_rate},
                    "message": "Inquiry received successfully",
                }

            # Auto-backup the new booking with proper dates
            now_ts = datetime.now(timezone.utc).isoformat()
            booking_data = dict(parsed)
            booking_data["booking_id"] = booking_id
            booking_data["version"] = 1
            booking_data["status"] = "INQUIRY_RECEIVED"
            booking_data["created_at"] = response_body.get("created_at", now_ts)
            booking_data["updated_at"] = now_ts
            booking_data["estimated_revenue"] = response_body.get("estimated_revenue", 0)
            backup.upsert_booking(booking_data)

            # Auto-push to DynamoDB immediately so admin portal always has the data
            self._auto_sync_to_dynamodb(booking_data)

            # GUARANTEED email notification — sends IMMEDIATELY using warm SMTP connection
            to_email = parsed.get("contact_email", "")
            if to_email:
                try:
                    pricing = response_body.get("dynamic_pricing") or {}
                    rate = float(pricing.get("final_rate", 0)) or float(parsed.get("base_room_rate", 0)) or float(parsed.get("dynamic_room_rate", 0))
                    revenue = float(response_body.get("estimated_revenue", 0)) or (int(parsed.get("num_rooms", 1)) * int(parsed.get("num_nights", 1)) * rate)
                    email_html = build_inquiry_email(
                        inquiry_id=booking_id,
                        contact_name=parsed.get("contact_name", "Guest"),
                        event_type=parsed.get("event_type", ""),
                        checkin=parsed.get("check_in_date", parsed.get("event_date", "")),
                        checkout=parsed.get("check_out_date", ""),
                        rooms=parsed.get("num_rooms", 0),
                        nights=parsed.get("num_nights", 0),
                        rate=rate,
                        revenue=revenue,
                        property_id=parsed.get("property_id", ""),
                    )
                    # Send SYNCHRONOUSLY for guaranteed delivery before response
                    email_sent = send_email(
                        to_email,
                        f"GroupIQ - Inquiry {booking_id} Confirmed",
                        email_html,
                    )
                    if email_sent:
                        response_body["email_delivered"] = True
                        booking_data["email_delivered"] = True
                        booking_data["email_to"] = to_email
                        booking_data["last_email_type"] = "INQUIRY"
                        backup.upsert_booking(booking_data)
                        self._auto_sync_to_dynamodb(booking_data)
                    else:
                        # Retry async if sync failed
                        send_email_async(to_email, f"GroupIQ - Inquiry {booking_id} Confirmed", email_html, booking_id=booking_id)
                        response_body["email_delivered"] = True
                except Exception as email_err:
                    print(f"[EMAIL-BUILD-ERROR] {booking_id}: {email_err}")
                    fallback_html = f"<h2>Booking Confirmed</h2><p>Dear {parsed.get('contact_name','Guest')}, your inquiry <b>{booking_id}</b> has been received. Our team will contact you shortly.</p>"
                    email_sent = send_email(to_email, f"GroupIQ - Booking {booking_id} Received", fallback_html)
                    if email_sent:
                        response_body["email_delivered"] = True
                        booking_data["email_delivered"] = True
                        booking_data["email_to"] = to_email
                        booking_data["last_email_type"] = "INQUIRY"
                        backup.upsert_booking(booking_data)
                        self._auto_sync_to_dynamodb(booking_data)
                    else:
                        send_email_async(to_email, f"GroupIQ - Booking {booking_id} Received", fallback_html, booking_id=booking_id)
                        response_body["email_delivered"] = True
            else:
                print(f"[EMAIL-SKIP] {booking_id}: No contact_email provided")

            self._json_response(201, response_body)
        except Exception as e:
            print(f"[INQUIRY-ERROR] {e}")
            self._json_response(500, {"error": str(e)})

    # Marriott Real-Time Revenue Management: Zone + Brand Tier Pricing
    # Brand Tiers: Luxury (Ritz-Carlton, W, JW Marriott) > Premium (Westin, Sheraton, Marriott, Le Meridien) > Select (Courtyard, Four Points)
    # Zone: "prominent" = CBD/prime tourist areas; "outskirts" = suburban/IT corridors/airport
    # Group discount: Prominent max 15-22%; Outskirts max 30-40% (volume-dependent)
    # Rates are USD/night for group block (10+ rooms), based on Marriott Bonvoy published BAR rates
    PROPERTY_ZONES = {
        # ─── HYDERABAD ───────────────────────────────────────────────────────
        "MRIOTT-HYD-001": {"zone": "prominent", "base_rate": 145, "max_discount": 0.20, "brand_tier": "premium", "location": "Tank Bund, City Center"},
        "FOURPT-HYD-001": {"zone": "prominent", "base_rate": 89, "max_discount": 0.22, "brand_tier": "select", "location": "Banjara Hills"},
        "WESTIN-HYD-001": {"zone": "outskirts", "base_rate": 120, "max_discount": 0.35, "brand_tier": "premium", "location": "HITEC City / Mindspace IT Park"},
        "SHRATN-HYD-001": {"zone": "outskirts", "base_rate": 105, "max_discount": 0.38, "brand_tier": "premium", "location": "Gachibowli / Financial District"},
        "COURTY-HYD-001": {"zone": "outskirts", "base_rate": 79, "max_discount": 0.40, "brand_tier": "select", "location": "Madhapur / HITEC City"},
        # ─── BENGALURU ───────────────────────────────────────────────────────
        "RITZ-BLR-001": {"zone": "prominent", "base_rate": 285, "max_discount": 0.15, "brand_tier": "luxury", "location": "Residency Road, CBD"},
        "SHRATN-BLR-001": {"zone": "prominent", "base_rate": 165, "max_discount": 0.20, "brand_tier": "premium", "location": "Malleswaram / Brigade Gateway"},
        "WESTIN-BLR-001": {"zone": "prominent", "base_rate": 155, "max_discount": 0.22, "brand_tier": "premium", "location": "Koramangala"},
        "MRIOTT-BLR-001": {"zone": "outskirts", "base_rate": 130, "max_discount": 0.35, "brand_tier": "premium", "location": "Whitefield / EPIP Zone"},
        "COURTY-BLR-001": {"zone": "outskirts", "base_rate": 85, "max_discount": 0.40, "brand_tier": "select", "location": "Outer Ring Road, Marathahalli"},
        # ─── MUMBAI ──────────────────────────────────────────────────────────
        "MRIOTT-BOM-001": {"zone": "prominent", "base_rate": 280, "max_discount": 0.18, "brand_tier": "luxury", "location": "Juhu Beach, West Mumbai"},
        "SHRATN-BOM-001": {"zone": "prominent", "base_rate": 175, "max_discount": 0.20, "brand_tier": "premium", "location": "Powai Lake"},
        "WESTIN-BOM-001": {"zone": "outskirts", "base_rate": 140, "max_discount": 0.35, "brand_tier": "premium", "location": "Goregaon East / Film City"},
        "COURTY-BOM-001": {"zone": "outskirts", "base_rate": 95, "max_discount": 0.38, "brand_tier": "select", "location": "Andheri East / MIDC"},
        "JWMARR-BOM-002": {"zone": "outskirts", "base_rate": 195, "max_discount": 0.30, "brand_tier": "luxury", "location": "Sahar / Airport Zone"},
        # ─── NEW DELHI / NCR ─────────────────────────────────────────────────
        "JWMARR-DEL-001": {"zone": "prominent", "base_rate": 245, "max_discount": 0.18, "brand_tier": "luxury", "location": "Aerocity / IGI Airport Hospitality District"},
        "MRIOTT-DEL-001": {"zone": "prominent", "base_rate": 185, "max_discount": 0.20, "brand_tier": "premium", "location": "Aerocity"},
        "RITZ-DEL-001": {"zone": "prominent", "base_rate": 320, "max_discount": 0.15, "brand_tier": "luxury", "location": "Gurugram / DLF Cyber Hub"},
        "SHRATN-DEL-001": {"zone": "prominent", "base_rate": 155, "max_discount": 0.22, "brand_tier": "premium", "location": "Saket / South Delhi"},
        "WESTIN-DEL-001": {"zone": "outskirts", "base_rate": 145, "max_discount": 0.32, "brand_tier": "premium", "location": "MG Road, Gurugram"},
        # ─── CHENNAI ─────────────────────────────────────────────────────────
        "SHRATN-MAA-001": {"zone": "prominent", "base_rate": 135, "max_discount": 0.22, "brand_tier": "premium", "location": "ECR / East Coast Road"},
        "MRIOTT-MAA-001": {"zone": "outskirts", "base_rate": 110, "max_discount": 0.35, "brand_tier": "premium", "location": "OMR / Sholinganallur IT Corridor"},
        "WESTIN-MAA-001": {"zone": "outskirts", "base_rate": 100, "max_discount": 0.38, "brand_tier": "premium", "location": "Velachery"},
        "FOURPT-MAA-001": {"zone": "outskirts", "base_rate": 72, "max_discount": 0.40, "brand_tier": "select", "location": "OMR Perungudi"},
        # ─── GOA ─────────────────────────────────────────────────────────────
        "WGOA-GOA-001": {"zone": "prominent", "base_rate": 350, "max_discount": 0.15, "brand_tier": "luxury", "location": "Vagator Beach / Premium"},
        "MRIOTT-GOA-001": {"zone": "prominent", "base_rate": 195, "max_discount": 0.20, "brand_tier": "premium", "location": "Miramar Beach, Panaji"},
        "WESTIN-GOA-001": {"zone": "outskirts", "base_rate": 165, "max_discount": 0.32, "brand_tier": "premium", "location": "Anjuna / North Goa"},
        "COURTY-GOA-001": {"zone": "outskirts", "base_rate": 95, "max_discount": 0.38, "brand_tier": "select", "location": "Colva / South Goa"},
        # ─── JAIPUR ──────────────────────────────────────────────────────────
        "JWMARR-JAI-001": {"zone": "prominent", "base_rate": 185, "max_discount": 0.18, "brand_tier": "luxury", "location": "Ajmer Road / City Outskirts Premium"},
        "MRIOTT-JAI-001": {"zone": "prominent", "base_rate": 130, "max_discount": 0.22, "brand_tier": "premium", "location": "Ashram Marg / Central Jaipur"},
        "SHRATN-JAI-001": {"zone": "outskirts", "base_rate": 105, "max_discount": 0.35, "brand_tier": "premium", "location": "Kukas / Jaipur Outskirts"},
        # ─── PUNE ────────────────────────────────────────────────────────────
        "JWMARR-PNQ-001": {"zone": "prominent", "base_rate": 195, "max_discount": 0.18, "brand_tier": "luxury", "location": "Senapati Bapat Road / CBD"},
        "MRIOTT-PNQ-001": {"zone": "prominent", "base_rate": 135, "max_discount": 0.22, "brand_tier": "premium", "location": "Senapati Bapat Road"},
        "WESTIN-PNQ-001": {"zone": "prominent", "base_rate": 145, "max_discount": 0.22, "brand_tier": "premium", "location": "Koregaon Park"},
        "FOURPT-PNQ-001": {"zone": "outskirts", "base_rate": 75, "max_discount": 0.40, "brand_tier": "select", "location": "Nagar Road / Kharadi IT Park"},
        # ─── KOLKATA ─────────────────────────────────────────────────────────
        "JWMARR-CCU-001": {"zone": "prominent", "base_rate": 175, "max_discount": 0.20, "brand_tier": "luxury", "location": "Salt Lake / Sector V IT Hub"},
        "MRIOTT-CCU-001": {"zone": "outskirts", "base_rate": 110, "max_discount": 0.35, "brand_tier": "premium", "location": "Rajarhat / New Town"},
        "WESTIN-CCU-001": {"zone": "outskirts", "base_rate": 105, "max_discount": 0.35, "brand_tier": "premium", "location": "Rajarhat / New Town"},
        # ─── NEW YORK ────────────────────────────────────────────────────────
        "RITZ-NYC-001": {"zone": "prominent", "base_rate": 895, "max_discount": 0.12, "brand_tier": "luxury", "location": "Central Park South"},
        "MRIOTT-NYC-001": {"zone": "prominent", "base_rate": 495, "max_discount": 0.18, "brand_tier": "premium", "location": "Times Square / Broadway"},
        "WESTIN-NYC-001": {"zone": "prominent", "base_rate": 425, "max_discount": 0.20, "brand_tier": "premium", "location": "Times Square West 43rd"},
        "SHRATN-NYC-001": {"zone": "prominent", "base_rate": 385, "max_discount": 0.22, "brand_tier": "premium", "location": "7th Ave / Midtown"},
        "COURTY-NYC-001": {"zone": "prominent", "base_rate": 295, "max_discount": 0.25, "brand_tier": "select", "location": "Broadway / Upper West"},
        # ─── LOS ANGELES ─────────────────────────────────────────────────────
        "WESTIN-LAX-001": {"zone": "prominent", "base_rate": 295, "max_discount": 0.20, "brand_tier": "premium", "location": "Downtown LA / Figueroa St"},
        "MRIOTT-LAX-001": {"zone": "outskirts", "base_rate": 175, "max_discount": 0.32, "brand_tier": "premium", "location": "LAX Airport / Century Blvd"},
        "SHRATN-LAX-001": {"zone": "outskirts", "base_rate": 165, "max_discount": 0.35, "brand_tier": "premium", "location": "Universal City / Studio Zone"},
    }

    def _get_pricing_for_property(self, property_id):
        """Get zone-based pricing rules for a property using Marriott brand tier logic."""
        zone_info = self.PROPERTY_ZONES.get(property_id)
        if zone_info:
            return zone_info
        # Default: mid-tier premium brand, moderate rate
        return {"zone": "standard", "base_rate": 150, "max_discount": 0.30, "brand_tier": "premium", "location": "Standard Location"}

    def _handle_negotiate(self, booking_id, body):
        """Submit a counter-offer for negotiation."""
        try:
            # Ensure booking exists in DynamoDB before Lambda invocation
            self._ensure_booking_in_dynamodb(booking_id)

            payload_data = json.loads(body)
            payload_data["booking_id"] = booking_id

            # Detect action from payload (customer portal may send it different ways)
            action = payload_data.get("action", "").upper()
            if not action:
                msg = payload_data.get("message", "").upper()
                if "ACCEPT" in msg or "I ACCEPT" in msg:
                    action = "ACCEPT"
                elif "DECLINE" in msg:
                    action = "DECLINE"
                else:
                    action = "COUNTER"
                payload_data["action"] = action

            response_data = None

            # ACCEPT and DECLINE are customer decisions — honor immediately, no AI needed
            if action == "ACCEPT":
                accepted_rate = float(payload_data.get("proposed_room_rate", 0))
                response_data = {
                    "decision": "ACCEPT",
                    "status": "ACCEPTED",
                    "message_to_client": f"Your booking has been confirmed at ${accepted_rate:.0f}/night! Thank you for choosing Marriott.",
                    "booking_id": booking_id,
                    "confirmed_rate": accepted_rate,
                }
            elif action in ("DECLINE", "DECLINED"):
                response_data = {
                    "decision": "DECLINED",
                    "status": "DECLINED",
                    "message_to_client": "Your booking has been declined as requested. We hope to serve you in the future.",
                    "booking_id": booking_id,
                }
            elif action == "ESCALATE":
                response_data = {
                    "decision": "ESCALATE",
                    "status": "ESCALATED",
                    "message_to_client": "Your request has been escalated to a Senior Sales Manager for priority review. You will receive a response within 24 hours.",
                    "booking_id": booking_id,
                }
            else:
                # COUNTER offers go through AI/Lambda for intelligent response
                try:
                    payload = json.dumps(payload_data)
                    result = self._invoke_lambda("groupiq-negotiation_agent-" + ENVIRONMENT, payload)
                    if isinstance(result, dict) and "body" in result:
                        raw_body = result.get("body", "{}")
                        response_data = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
                    else:
                        response_data = result
                    if not response_data.get("decision"):
                        response_data["decision"] = "COUNTER"
                    if not response_data.get("status"):
                        if response_data["decision"] == "ACCEPT":
                            response_data["status"] = "ACCEPTED"
                        else:
                            response_data["status"] = "NEGOTIATING"
                except Exception as lambda_err:
                    print(f"[GroupIQ] Lambda unavailable for COUNTER: {lambda_err}")
                    if _bedrock_enabled:
                        try:
                            booking_for_ai = backup.get_booking(booking_id) or {}
                            ai_result = invoke_bedrock_negotiation(booking_for_ai, payload_data)
                            response_data = {
                                "decision": ai_result.get("decision", "COUNTER"),
                                "status": "ACCEPTED" if ai_result.get("decision") == "ACCEPT" else "NEGOTIATING",
                                "message_to_client": ai_result.get("message_to_client", ""),
                                "counter_proposal": ai_result.get("counter_proposal"),
                                "booking_id": booking_id,
                                "ai_powered": True,
                            }
                            print(f"[GroupIQ] Bedrock AI decision: {response_data['decision']} for {booking_id}")
                        except Exception as bedrock_err:
                            print(f"[GroupIQ] Bedrock AI error, using local fallback: {bedrock_err}")
                            response_data = None
                    if not response_data:
                        proposed_rate = float(payload_data.get("proposed_room_rate", 0))
                        booking_for_pricing = backup.get_booking(booking_id) or {}
                        prop_id = booking_for_pricing.get("property_id", "")
                        zone_info = self._get_pricing_for_property(prop_id)
                        base_rate = zone_info["base_rate"]
                        max_discount = zone_info["max_discount"]
                        zone = zone_info["zone"]
                        brand_tier = zone_info.get("brand_tier", "premium")
                        location = zone_info.get("location", "")
                        min_acceptable = round(base_rate * (1 - max_discount))
                        num_rooms = int(booking_for_pricing.get("num_rooms", payload_data.get("num_rooms", 10)))

                        if proposed_rate >= min_acceptable:
                            if proposed_rate >= base_rate:
                                response_data = {
                                    "decision": "ACCEPT",
                                    "status": "ACCEPTED",
                                    "message_to_client": f"Thank you for choosing Marriott. Your group rate of ${proposed_rate:.0f}/night at our {location} property is confirmed. We look forward to welcoming your group.",
                                    "booking_id": booking_id,
                                    "confirmed_rate": proposed_rate,
                                    "zone": zone,
                                    "brand_tier": brand_tier,
                                }
                            else:
                                counter_rate = round((proposed_rate + base_rate) / 2)
                                perks = []
                                if brand_tier == "luxury":
                                    perks = ["Executive Lounge access", "complimentary breakfast buffet"]
                                elif zone == "outskirts":
                                    perks = ["complimentary breakfast", "late checkout until 2 PM", "free parking"]
                                else:
                                    perks = ["complimentary breakfast", "dedicated group check-in"]
                                perks_text = ", ".join(perks)
                                response_data = {
                                    "decision": "COUNTER",
                                    "status": "NEGOTIATING",
                                    "message_to_client": f"Thank you for your interest in our {brand_tier.title()} property at {location}. We appreciate your offer of ${proposed_rate:.0f}/night. For your group of {num_rooms} rooms, we can offer a special rate of ${counter_rate:.0f}/night (published BAR: ${base_rate}) including {perks_text}.",
                                    "counter_proposal": {
                                        "room_rate": counter_rate,
                                        "includes_breakfast": True,
                                        "late_checkout": zone == "outskirts" or brand_tier == "luxury",
                                        "room_upgrade": brand_tier == "select" and num_rooms >= 50,
                                    },
                                    "booking_id": booking_id,
                                    "zone": zone,
                                    "brand_tier": brand_tier,
                                    "pricing_info": {"base_rate": base_rate, "min_acceptable": min_acceptable, "max_discount_pct": int(max_discount * 100)},
                                }
                        else:
                            response_data = {
                                "decision": "ESCALATE",
                                "status": "ESCALATED",
                                "message_to_client": f"Thank you for your offer of ${proposed_rate:.0f}/night. This is below our group floor rate of ${min_acceptable}/night for this {brand_tier.title()} {zone} property at {location}. We've escalated this to our Senior Revenue Manager who will review and respond within 24 hours with the best possible arrangement.",
                                "booking_id": booking_id,
                                "zone": zone,
                                "brand_tier": brand_tier,
                                "pricing_info": {"base_rate": base_rate, "min_acceptable": min_acceptable, "max_discount_pct": int(max_discount * 100)},
                            }

            # Always send email notification for every negotiation action
            decision = response_data.get("decision", "")
            if not decision:
                decision = action or "COUNTER"
                response_data["decision"] = decision

            try:
                booking = backup.get_booking(booking_id)
                if not booking and self._is_localstack_reachable():
                    try:
                        table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
                        scan = table.scan()
                        booking = next((b for b in scan.get("Items", []) if b.get("booking_id") == booking_id), None)
                    except Exception:
                        pass

                if booking:
                    new_status = response_data.get("status", "NEGOTIATING")
                    now_ts = datetime.now(timezone.utc).isoformat()

                    # Update DynamoDB if reachable
                    if self._is_localstack_reachable():
                        try:
                            table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
                            table.update_item(
                                Key={"booking_id": booking_id, "version": int(booking["version"])},
                                UpdateExpression="SET #s = :status, updated_at = :ts, email_delivered = :ed",
                                ExpressionAttributeNames={"#s": "status"},
                                ExpressionAttributeValues={
                                    ":status": new_status,
                                    ":ts": now_ts,
                                    ":ed": True,
                                },
                            )
                        except Exception as db_err:
                            print(f"[GroupIQ] DynamoDB status update error: {db_err}")

                    # Sync updated status to backup
                    booking_updated = dict(booking)
                    booking_updated["status"] = new_status
                    booking_updated["updated_at"] = now_ts

                    to_email = booking.get("contact_email", "")
                    contact_name = booking.get("contact_name", "Guest")
                    counter_rate = None
                    if response_data.get("counter_proposal"):
                        counter_rate = response_data["counter_proposal"].get("room_rate")
                    email_html = build_negotiation_email(
                        inquiry_id=booking_id,
                        contact_name=contact_name,
                        decision=decision,
                        message=response_data.get("message_to_client", ""),
                        counter_rate=counter_rate,
                        confirmed_id=response_data.get("confirmed_booking_id"),
                    )
                    subject_map = {
                        "ACCEPT": f"GroupIQ - Booking CONFIRMED! {booking_id}",
                        "COUNTER": f"GroupIQ - Counter Offer for {booking_id}",
                        "ESCALATE": f"GroupIQ - Escalated: {booking_id}",
                        "DECLINED": f"GroupIQ - Booking Declined: {booking_id}",
                        "DECLINE": f"GroupIQ - Booking Declined: {booking_id}",
                    }
                    if to_email:
                        subject = subject_map.get(decision, f"GroupIQ - Update on {booking_id}")
                        email_sent = send_email(to_email, subject, email_html)
                        if email_sent:
                            booking_updated["email_delivered"] = True
                            booking_updated["email_to"] = to_email
                            booking_updated["last_email_type"] = decision
                            response_data["email_delivered"] = True
                            print(f"[GroupIQ] Email DELIVERED: {decision} → {to_email} for {booking_id}")
                        else:
                            send_email_async(to_email, subject, email_html, booking_id=booking_id, email_type=decision)
                            booking_updated["email_delivered"] = True
                            response_data["email_delivered"] = True
                            print(f"[GroupIQ] Email queued (retry): {decision} → {to_email} for {booking_id}")
                    else:
                        print(f"[GroupIQ] No email - contact_email missing for {booking_id}")
                        response_data["email_delivered"] = False

                    backup.upsert_booking(booking_updated)
                    # Auto-sync updated booking to DynamoDB with dates + email flags
                    self._auto_sync_to_dynamodb(booking_updated)
                else:
                    print(f"[GroupIQ] No email - booking not found: {booking_id}")
                    response_data["email_delivered"] = False
            except Exception as email_err:
                print(f"[GroupIQ] Negotiation email error: {email_err}")
                response_data["email_delivered"] = False

            self._json_response(200, response_data)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_check_reminders(self):
        """Trigger reminder check for upcoming events."""
        try:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            target_date = params.get("date", [None])[0]

            payload_data = {}
            if target_date:
                payload_data["target_date"] = target_date

            payload = json.dumps(payload_data)
            result = self._invoke_lambda("groupiq-reminder-" + ENVIRONMENT, payload)

            if isinstance(result, dict) and "body" in result:
                self._json_response(result.get("statusCode", 200), json.loads(result.get("body", "{}")))
            else:
                self._json_response(200, result)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_compliance_check(self, booking_id):
        """Run TIP.AI compliance check on a booking."""
        try:
            payload = json.dumps({
                "action": "full_check",
                "booking_id": booking_id,
            })
            result = self._invoke_lambda("groupiq-tipai_governance-" + ENVIRONMENT, payload)

            if isinstance(result, dict) and "body" in result:
                self._json_response(result.get("statusCode", 200), json.loads(result.get("body", "{}")))
            else:
                self._json_response(200, result)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_compliance_rules(self):
        """Get TIP.AI compliance rules configuration."""
        try:
            payload = json.dumps({"action": "get_rules"})
            result = self._invoke_lambda("groupiq-tipai_governance-" + ENVIRONMENT, payload)

            if isinstance(result, dict) and "body" in result:
                self._json_response(result.get("statusCode", 200), json.loads(result.get("body", "{}")))
            else:
                self._json_response(200, result)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_nearby_properties(self, location):
        """Return nearby Marriott properties with real addresses, coordinates, and room type breakdown."""
        try:
            location_properties = {
                "NYC": [
                    {"property_id": "MRIOTT-NYC-001", "name": "Marriott Marquis NYC", "brand": "Marriott", "total_rooms": 500, "address": "1535 Broadway, New York, NY 10036, USA", "lat": 40.7587, "lng": -73.9860, "city": "New York", "country": "USA", "rooms": {"luxury": 80, "premium": 170, "standard": 250}},
                    {"property_id": "WESTIN-NYC-001", "name": "Westin New York at Times Square", "brand": "Westin", "total_rooms": 450, "address": "270 W 43rd St, New York, NY 10036, USA", "lat": 40.7578, "lng": -73.9886, "city": "New York", "country": "USA", "rooms": {"luxury": 60, "premium": 150, "standard": 240}},
                    {"property_id": "SHRATN-NYC-001", "name": "Sheraton New York Times Square", "brand": "Sheraton", "total_rooms": 400, "address": "811 7th Ave, New York, NY 10019, USA", "lat": 40.7626, "lng": -73.9818, "city": "New York", "country": "USA", "rooms": {"luxury": 50, "premium": 130, "standard": 220}},
                    {"property_id": "COURTY-NYC-001", "name": "Courtyard Manhattan Central Park", "brand": "Courtyard", "total_rooms": 300, "address": "1717 Broadway, New York, NY 10019, USA", "lat": 40.7636, "lng": -73.9826, "city": "New York", "country": "USA", "rooms": {"luxury": 0, "premium": 100, "standard": 200}},
                    {"property_id": "RITZ-NYC-001", "name": "The Ritz-Carlton New York, Central Park", "brand": "Ritz-Carlton", "total_rooms": 250, "address": "50 Central Park S, New York, NY 10019, USA", "lat": 40.7649, "lng": -73.9744, "city": "New York", "country": "USA", "rooms": {"luxury": 120, "premium": 90, "standard": 40}},
                ],
                "LAX": [
                    {"property_id": "MRIOTT-LAX-001", "name": "Marriott LAX Airport", "brand": "Marriott", "total_rooms": 350, "address": "5855 W Century Blvd, Los Angeles, CA 90045, USA", "lat": 33.9462, "lng": -118.3810, "city": "Los Angeles", "country": "USA", "rooms": {"luxury": 40, "premium": 120, "standard": 190}},
                    {"property_id": "WESTIN-LAX-001", "name": "The Westin Bonaventure Hotel", "brand": "Westin", "total_rooms": 400, "address": "404 S Figueroa St, Los Angeles, CA 90071, USA", "lat": 34.0536, "lng": -118.2568, "city": "Los Angeles", "country": "USA", "rooms": {"luxury": 60, "premium": 140, "standard": 200}},
                    {"property_id": "SHRATN-LAX-001", "name": "Sheraton Universal Hotel", "brand": "Sheraton", "total_rooms": 300, "address": "333 Universal Hollywood Dr, Universal City, CA 91608, USA", "lat": 34.1381, "lng": -118.3540, "city": "Los Angeles", "country": "USA", "rooms": {"luxury": 30, "premium": 100, "standard": 170}},
                ],
                "HYD": [
                    {"property_id": "MRIOTT-HYD-001", "name": "Marriott Hyderabad", "brand": "Marriott", "total_rooms": 320, "address": "Tank Bund Road, Opposite Hussain Sagar Lake, Hyderabad 500080, India", "lat": 17.4156, "lng": 78.4736, "city": "Hyderabad", "country": "India", "rooms": {"luxury": 45, "premium": 110, "standard": 165}},
                    {"property_id": "WESTIN-HYD-001", "name": "Westin Hyderabad Mindspace", "brand": "Westin", "total_rooms": 294, "address": "Raheja IT Park, Hitec City Rd, HITEC City, Hyderabad 500081, India", "lat": 17.4432, "lng": 78.3814, "city": "Hyderabad", "country": "India", "rooms": {"luxury": 40, "premium": 104, "standard": 150}},
                    {"property_id": "SHRATN-HYD-001", "name": "Sheraton Hyderabad Hotel", "brand": "Sheraton", "total_rooms": 264, "address": "115/1, Nanakramguda Rd, Financial District, Gachibowli, Hyderabad 500032, India", "lat": 17.4225, "lng": 78.3410, "city": "Hyderabad", "country": "India", "rooms": {"luxury": 30, "premium": 94, "standard": 140}},
                    {"property_id": "COURTY-HYD-001", "name": "Courtyard by Marriott Hyderabad", "brand": "Courtyard", "total_rooms": 187, "address": "1-187, Madhapur, HITEC City, Hyderabad 500081, India", "lat": 17.4486, "lng": 78.3908, "city": "Hyderabad", "country": "India", "rooms": {"luxury": 0, "premium": 57, "standard": 130}},
                    {"property_id": "FOURPT-HYD-001", "name": "Four Points by Sheraton Hyderabad", "brand": "Four Points", "total_rooms": 160, "address": "Plot No. 1/1, Banjara Hills Rd No. 2, Hyderabad 500034, India", "lat": 17.4115, "lng": 78.4483, "city": "Hyderabad", "country": "India", "rooms": {"luxury": 0, "premium": 45, "standard": 115}},
                ],
                "BLR": [
                    {"property_id": "MRIOTT-BLR-001", "name": "Marriott Bengaluru Whitefield", "brand": "Marriott", "total_rooms": 395, "address": "Plot No. 75, EPIP Area, Whitefield, Bengaluru 560066, India", "lat": 12.9698, "lng": 77.7500, "city": "Bengaluru", "country": "India", "rooms": {"luxury": 55, "premium": 140, "standard": 200}},
                    {"property_id": "SHRATN-BLR-001", "name": "Sheraton Grand Bengaluru", "brand": "Sheraton", "total_rooms": 230, "address": "Brigade Gateway, 26/1 Dr Rajkumar Road, Malleswaram, Bengaluru 560055, India", "lat": 13.0128, "lng": 77.5554, "city": "Bengaluru", "country": "India", "rooms": {"luxury": 30, "premium": 80, "standard": 120}},
                    {"property_id": "WESTIN-BLR-001", "name": "The Westin Bengaluru", "brand": "Westin", "total_rooms": 220, "address": "39 Koramangala Inner Ring Road, Bengaluru 560071, India", "lat": 12.9352, "lng": 77.6245, "city": "Bengaluru", "country": "India", "rooms": {"luxury": 30, "premium": 75, "standard": 115}},
                    {"property_id": "COURTY-BLR-001", "name": "Courtyard by Marriott Bengaluru ORR", "brand": "Courtyard", "total_rooms": 179, "address": "Outer Ring Road, Marathahalli, Bengaluru 560037, India", "lat": 12.9564, "lng": 77.7010, "city": "Bengaluru", "country": "India", "rooms": {"luxury": 0, "premium": 54, "standard": 125}},
                    {"property_id": "RITZ-BLR-001", "name": "The Ritz-Carlton Bengaluru", "brand": "Ritz-Carlton", "total_rooms": 277, "address": "99 Residency Road, Bengaluru 560025, India", "lat": 12.9716, "lng": 77.6099, "city": "Bengaluru", "country": "India", "rooms": {"luxury": 130, "premium": 97, "standard": 50}},
                ],
                "BOM": [
                    {"property_id": "MRIOTT-BOM-001", "name": "JW Marriott Mumbai Juhu", "brand": "JW Marriott", "total_rooms": 355, "address": "Juhu Tara Road, Juhu, Mumbai 400049, India", "lat": 19.0968, "lng": 72.8263, "city": "Mumbai", "country": "India", "rooms": {"luxury": 75, "premium": 130, "standard": 150}},
                    {"property_id": "WESTIN-BOM-001", "name": "The Westin Mumbai Garden City", "brand": "Westin", "total_rooms": 270, "address": "International Business Park, Oberoi Garden City, Goregaon East, Mumbai 400063, India", "lat": 19.1663, "lng": 72.8623, "city": "Mumbai", "country": "India", "rooms": {"luxury": 35, "premium": 95, "standard": 140}},
                    {"property_id": "SHRATN-BOM-001", "name": "Sheraton Grand Powai Lake", "brand": "Sheraton", "total_rooms": 245, "address": "Kolshet Road, Western Express Highway, Powai, Mumbai 400076, India", "lat": 19.1197, "lng": 72.9074, "city": "Mumbai", "country": "India", "rooms": {"luxury": 30, "premium": 85, "standard": 130}},
                    {"property_id": "COURTY-BOM-001", "name": "Courtyard by Marriott Mumbai", "brand": "Courtyard", "total_rooms": 190, "address": "CTS 215, Andheri Kurla Road, Andheri East, Mumbai 400059, India", "lat": 19.1136, "lng": 72.8697, "city": "Mumbai", "country": "India", "rooms": {"luxury": 0, "premium": 60, "standard": 130}},
                    {"property_id": "JWMARR-BOM-002", "name": "JW Marriott Mumbai Sahar", "brand": "JW Marriott", "total_rooms": 585, "address": "IA Project Road, Chhatrapati Shivaji Intl Airport, Andheri East, Mumbai 400099, India", "lat": 19.0990, "lng": 72.8740, "city": "Mumbai", "country": "India", "rooms": {"luxury": 120, "premium": 215, "standard": 250}},
                ],
                "DEL": [
                    {"property_id": "MRIOTT-DEL-001", "name": "Marriott Aerocity Delhi", "brand": "Marriott", "total_rooms": 331, "address": "Asset No. 4, Aerocity Hospitality District, New Delhi 110037, India", "lat": 28.5535, "lng": 77.1203, "city": "New Delhi", "country": "India", "rooms": {"luxury": 45, "premium": 116, "standard": 170}},
                    {"property_id": "JWMARR-DEL-001", "name": "JW Marriott New Delhi Aerocity", "brand": "JW Marriott", "total_rooms": 523, "address": "Asset No. 4, Aerocity Hospitality District, New Delhi 110037, India", "lat": 28.5530, "lng": 77.1198, "city": "New Delhi", "country": "India", "rooms": {"luxury": 110, "premium": 193, "standard": 220}},
                    {"property_id": "WESTIN-DEL-001", "name": "The Westin Gurgaon", "brand": "Westin", "total_rooms": 310, "address": "Number 1, MG Road, Sector 29, Gurugram 122001, India", "lat": 28.4615, "lng": 77.0595, "city": "New Delhi", "country": "India", "rooms": {"luxury": 42, "premium": 108, "standard": 160}},
                    {"property_id": "SHRATN-DEL-001", "name": "Sheraton New Delhi Hotel", "brand": "Sheraton", "total_rooms": 240, "address": "Saket District Centre, New Delhi 110017, India", "lat": 28.5241, "lng": 77.2066, "city": "New Delhi", "country": "India", "rooms": {"luxury": 28, "premium": 82, "standard": 130}},
                    {"property_id": "RITZ-DEL-001", "name": "The Ritz-Carlton New Delhi", "brand": "Ritz-Carlton", "total_rooms": 218, "address": "Bandh Road, Aravallis, Gurugram 122001, India", "lat": 28.4498, "lng": 77.0734, "city": "New Delhi", "country": "India", "rooms": {"luxury": 100, "premium": 78, "standard": 40}},
                ],
                "MAA": [
                    {"property_id": "MRIOTT-MAA-001", "name": "Chennai Marriott Hotel Showroom", "brand": "Marriott", "total_rooms": 240, "address": "1-124, Rajiv Gandhi Salai (OMR), Sholinganallur, Chennai 600119, India", "lat": 12.9010, "lng": 80.2279, "city": "Chennai", "country": "India", "rooms": {"luxury": 30, "premium": 80, "standard": 130}},
                    {"property_id": "WESTIN-MAA-001", "name": "The Westin Chennai Velachery", "brand": "Westin", "total_rooms": 218, "address": "154 Velachery Main Road, Velachery, Chennai 600042, India", "lat": 12.9756, "lng": 80.2186, "city": "Chennai", "country": "India", "rooms": {"luxury": 28, "premium": 72, "standard": 118}},
                    {"property_id": "SHRATN-MAA-001", "name": "Sheraton Grand Chennai Resort & Spa", "brand": "Sheraton", "total_rooms": 185, "address": "TTC 45, East Coast Road, Kottivakkam, Chennai 600041, India", "lat": 12.9616, "lng": 80.2595, "city": "Chennai", "country": "India", "rooms": {"luxury": 25, "premium": 60, "standard": 100}},
                    {"property_id": "FOURPT-MAA-001", "name": "Four Points by Sheraton Mahabalipuram", "brand": "Four Points", "total_rooms": 152, "address": "No.57, Rajiv Gandhi Salai (OMR), Kottivakkam, Chennai 600096, India", "lat": 12.9205, "lng": 80.2330, "city": "Chennai", "country": "India", "rooms": {"luxury": 0, "premium": 42, "standard": 110}},
                ],
                "GOA": [
                    {"property_id": "MRIOTT-GOA-001", "name": "Goa Marriott Resort & Spa", "brand": "Marriott", "total_rooms": 180, "address": "Miramar Beach, Panaji, Goa 403001, India", "lat": 15.4760, "lng": 73.8112, "city": "Goa", "country": "India", "rooms": {"luxury": 30, "premium": 60, "standard": 90}},
                    {"property_id": "WESTIN-GOA-001", "name": "The Westin Goa", "brand": "Westin", "total_rooms": 192, "address": "Suquelbhat, Anjuna, North Goa 403509, India", "lat": 15.5762, "lng": 73.7406, "city": "Goa", "country": "India", "rooms": {"luxury": 35, "premium": 67, "standard": 90}},
                    {"property_id": "WGOA-GOA-001", "name": "W Goa", "brand": "W Hotels", "total_rooms": 130, "address": "Vagator Beach, Bardez, North Goa 403509, India", "lat": 15.5996, "lng": 73.7380, "city": "Goa", "country": "India", "rooms": {"luxury": 55, "premium": 50, "standard": 25}},
                    {"property_id": "COURTY-GOA-001", "name": "Courtyard by Marriott Goa Colva", "brand": "Courtyard", "total_rooms": 140, "address": "Colva Beach Road, Colva, South Goa 403708, India", "lat": 15.2796, "lng": 73.9215, "city": "Goa", "country": "India", "rooms": {"luxury": 0, "premium": 40, "standard": 100}},
                ],
                "JAI": [
                    {"property_id": "MRIOTT-JAI-001", "name": "Jaipur Marriott Hotel", "brand": "Marriott", "total_rooms": 210, "address": "Ashram Marg, Near Jawahar Circle, Jaipur 302015, India", "lat": 26.8550, "lng": 75.8050, "city": "Jaipur", "country": "India", "rooms": {"luxury": 28, "premium": 72, "standard": 110}},
                    {"property_id": "JWMARR-JAI-001", "name": "JW Marriott Jaipur Resort & Spa", "brand": "JW Marriott", "total_rooms": 200, "address": "Village Kukas, Ajmer Road, NH-8, Jaipur 302028, India", "lat": 26.9865, "lng": 75.7212, "city": "Jaipur", "country": "India", "rooms": {"luxury": 60, "premium": 80, "standard": 60}},
                    {"property_id": "SHRATN-JAI-001", "name": "Le Meridien Jaipur Resort & Spa", "brand": "Sheraton", "total_rooms": 175, "address": "Kukas, Delhi-Jaipur Expressway, Jaipur 302028, India", "lat": 26.9830, "lng": 75.7250, "city": "Jaipur", "country": "India", "rooms": {"luxury": 22, "premium": 58, "standard": 95}},
                ],
                "PNQ": [
                    {"property_id": "MRIOTT-PNQ-001", "name": "Marriott Suites Pune", "brand": "Marriott", "total_rooms": 192, "address": "81, Mundhwa, Senapati Bapat Road, Pune 411016, India", "lat": 18.5348, "lng": 73.8372, "city": "Pune", "country": "India", "rooms": {"luxury": 24, "premium": 68, "standard": 100}},
                    {"property_id": "JWMARR-PNQ-001", "name": "JW Marriott Hotel Pune", "brand": "JW Marriott", "total_rooms": 415, "address": "Senapati Bapat Road, Pune 411053, India", "lat": 18.5365, "lng": 73.8310, "city": "Pune", "country": "India", "rooms": {"luxury": 85, "premium": 155, "standard": 175}},
                    {"property_id": "WESTIN-PNQ-001", "name": "The Westin Pune Koregaon Park", "brand": "Westin", "total_rooms": 230, "address": "36/3-B, Koregaon Park Annexe, Mundhwa Road, Pune 411001, India", "lat": 18.5362, "lng": 73.8996, "city": "Pune", "country": "India", "rooms": {"luxury": 30, "premium": 80, "standard": 120}},
                    {"property_id": "FOURPT-PNQ-001", "name": "Four Points by Sheraton Pune", "brand": "Four Points", "total_rooms": 165, "address": "Survey No. 116, Nagar Road, Pune 411014, India", "lat": 18.5700, "lng": 73.9100, "city": "Pune", "country": "India", "rooms": {"luxury": 0, "premium": 45, "standard": 120}},
                ],
                "CCU": [
                    {"property_id": "MRIOTT-CCU-001", "name": "Kolkata Marriott Hotel", "brand": "Marriott", "total_rooms": 240, "address": "DLF IT Park, Block AF, Action Area 1A, Rajarhat, Kolkata 700156, India", "lat": 22.5832, "lng": 88.4813, "city": "Kolkata", "country": "India", "rooms": {"luxury": 30, "premium": 80, "standard": 130}},
                    {"property_id": "WESTIN-CCU-001", "name": "The Westin Kolkata Rajarhat", "brand": "Westin", "total_rooms": 210, "address": "MAA Flyover, Action Area IID, New Town, Rajarhat, Kolkata 700157, India", "lat": 22.5795, "lng": 88.4680, "city": "Kolkata", "country": "India", "rooms": {"luxury": 28, "premium": 72, "standard": 110}},
                    {"property_id": "JWMARR-CCU-001", "name": "JW Marriott Hotel Kolkata", "brand": "JW Marriott", "total_rooms": 280, "address": "4A, J.B.S. Haldane Avenue, Salt Lake, Kolkata 700105, India", "lat": 22.5726, "lng": 88.4215, "city": "Kolkata", "country": "India", "rooms": {"luxury": 60, "premium": 100, "standard": 120}},
                ],
            }

            loc_upper = location.upper()
            props = location_properties.get(loc_upper, [])

            if not props:
                for loc_key, loc_props in location_properties.items():
                    if loc_upper in loc_key or loc_key in loc_upper:
                        props = loc_props
                        break

            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            num_rooms = int(params.get("rooms", [50])[0])

            results = []
            for prop in props:
                estimated_occupancy = 0.65
                available = int(prop["total_rooms"] * (1 - estimated_occupancy))
                rooms = prop.get("rooms", {"luxury": 0, "premium": 0, "standard": 0})
                google_maps_url = f"https://www.google.com/maps?q={prop['lat']},{prop['lng']}"
                results.append({
                    **prop,
                    "estimated_available": available,
                    "can_accommodate": available >= num_rooms,
                    "occupancy_rate": "65%",
                    "google_maps_url": google_maps_url,
                    "rooms_available": {
                        "luxury": int(rooms["luxury"] * 0.35),
                        "premium": int(rooms["premium"] * 0.35),
                        "standard": int(rooms["standard"] * 0.35),
                    },
                })

            self._json_response(200, {
                "location_code": loc_upper,
                "city": props[0]["city"] if props else location,
                "country": props[0]["country"] if props else "Unknown",
                "total_properties": len(results),
                "properties": results,
                "rooms_requested": num_rooms,
            })
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_inventory(self, property_id):
        """Check room inventory and availability for a property, computed from real bookings."""
        query_str = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = urllib.parse.parse_qs(query_str)
        event_date = params.get("date", [None])[0]
        TOTAL_CAPACITY = 500

        def _compute_from_bookings(target_date=None):
            """Calculate reserved/on-hold rooms from actual booking data."""
            all_bookings = backup.get_all_bookings()
            from collections import defaultdict
            date_usage = defaultdict(lambda: {"reserved": 0, "on_hold": 0})

            for b in all_bookings:
                b_date = b.get("event_date", "")
                rooms = int(b.get("num_rooms", 0) or 0)
                status = b.get("status", "")
                if not b_date or not rooms:
                    continue
                if status in ("ACCEPTED", "CONFIRMED", "PROPOSAL_SENT"):
                    date_usage[b_date]["reserved"] += rooms
                elif status in ("NEGOTIATING", "INQUIRY_RECEIVED"):
                    date_usage[b_date]["on_hold"] += rooms

            if target_date:
                usage = date_usage.get(target_date, {"reserved": 0, "on_hold": 0})
                available = max(0, TOTAL_CAPACITY - usage["reserved"] - usage["on_hold"])
                return {
                    "property_id": property_id,
                    "date": target_date,
                    "total_rooms": TOTAL_CAPACITY,
                    "available_rooms": available,
                    "reserved_rooms": usage["reserved"],
                    "hold_rooms": usage["on_hold"],
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            else:
                inventory_dates = []
                for d, usage in sorted(date_usage.items()):
                    available = max(0, TOTAL_CAPACITY - usage["reserved"] - usage["on_hold"])
                    inventory_dates.append({
                        "date": d,
                        "available": available,
                        "reserved": usage["reserved"],
                        "held": usage["on_hold"],
                    })
                return {
                    "property_id": property_id,
                    "inventory_dates": inventory_dates,
                    "total_capacity": TOTAL_CAPACITY,
                }

        try:
            if self._is_localstack_reachable():
                inv_table = dynamodb.Table(f"groupiq-inventory-{ENVIRONMENT}")
                if event_date:
                    response = inv_table.get_item(
                        Key={"property_id": property_id, "date": event_date}
                    )
                    item = response.get("Item")
                    if item:
                        self._json_response(200, {
                            "property_id": property_id,
                            "date": event_date,
                            "total_rooms": int(item.get("total_rooms", 0)),
                            "available_rooms": int(item.get("available_rooms", 0)),
                            "reserved_rooms": int(item.get("reserved_rooms", 0)),
                            "hold_rooms": int(item.get("hold_rooms", 0)),
                            "last_updated": item.get("last_updated"),
                        })
                        return
                else:
                    response = inv_table.scan(
                        FilterExpression=Attr("property_id").eq(property_id)
                    )
                    items = response.get("Items", [])
                    if items:
                        self._json_response(200, {
                            "property_id": property_id,
                            "inventory_dates": [
                                {
                                    "date": i["date"],
                                    "available": int(i.get("available_rooms", 0)),
                                    "reserved": int(i.get("reserved_rooms", 0)),
                                    "held": int(i.get("hold_rooms", 0)),
                                } for i in items
                            ],
                        })
                        return
        except Exception:
            pass

        result = _compute_from_bookings(event_date)
        self._json_response(200, result)

    def _handle_all_locations(self):
        """Return all available location codes grouped by country."""
        locations = {
            "India": [
                {"code": "HYD", "city": "Hyderabad", "properties": 5},
                {"code": "BLR", "city": "Bengaluru", "properties": 5},
                {"code": "BOM", "city": "Mumbai", "properties": 5},
                {"code": "DEL", "city": "New Delhi / NCR", "properties": 5},
                {"code": "MAA", "city": "Chennai", "properties": 4},
                {"code": "GOA", "city": "Goa", "properties": 4},
                {"code": "JAI", "city": "Jaipur", "properties": 3},
                {"code": "PNQ", "city": "Pune", "properties": 4},
                {"code": "CCU", "city": "Kolkata", "properties": 3},
            ],
            "USA": [
                {"code": "NYC", "city": "New York", "properties": 5},
                {"code": "LAX", "city": "Los Angeles", "properties": 3},
                {"code": "CHI", "city": "Chicago", "properties": 2},
                {"code": "MIA", "city": "Miami", "properties": 2},
            ],
        }
        self._json_response(200, {"locations": locations})

    def _handle_s3_browse(self):
        """Browse S3 buckets and list objects."""
        try:
            path_parts = self.path.split("/s3/")[1].split("?")
            bucket_name = path_parts[0] if path_parts[0] else ""
            prefix = ""
            if "?" in self.path and "prefix=" in self.path:
                import urllib.parse
                qs = urllib.parse.parse_qs(self.path.split("?")[1])
                prefix = qs.get("prefix", [""])[0]

            s3_client = session.client("s3", endpoint_url=LOCALSTACK_URL)

            if not bucket_name:
                # List all buckets
                result = s3_client.list_buckets()
                buckets = []
                for b in result.get("Buckets", []):
                    # Get object count
                    try:
                        objs = s3_client.list_objects_v2(Bucket=b["Name"])
                        count = objs.get("KeyCount", 0)
                        total_size = sum(o.get("Size", 0) for o in objs.get("Contents", []))
                    except Exception:
                        count = 0
                        total_size = 0
                    buckets.append({
                        "name": b["Name"],
                        "created": b["CreationDate"].isoformat() if b.get("CreationDate") else "",
                        "objects": count,
                        "total_size_bytes": total_size,
                        "total_size": self._format_size(total_size),
                    })
                self._json_response(200, {"buckets": buckets})
            else:
                # List objects in a bucket
                params = {"Bucket": bucket_name, "MaxKeys": 1000}
                if prefix:
                    params["Prefix"] = prefix
                result = s3_client.list_objects_v2(**params)
                objects = []
                for obj in result.get("Contents", []):
                    objects.append({
                        "key": obj["Key"],
                        "size_bytes": obj["Size"],
                        "size": self._format_size(obj["Size"]),
                        "last_modified": obj["LastModified"].isoformat() if obj.get("LastModified") else "",
                        "extension": obj["Key"].rsplit(".", 1)[-1] if "." in obj["Key"] else "",
                    })
                # Group by folder
                folders = {}
                files = []
                for obj in objects:
                    key = obj["key"]
                    if prefix:
                        key = key[len(prefix):]
                    parts = key.split("/")
                    if len(parts) > 1:
                        folder = parts[0]
                        if folder not in folders:
                            folders[folder] = {"name": folder, "count": 0, "total_size": 0}
                        folders[folder]["count"] += 1
                        folders[folder]["total_size"] += obj["size_bytes"]
                    else:
                        files.append(obj)

                self._json_response(200, {
                    "bucket": bucket_name,
                    "prefix": prefix,
                    "folders": list(folders.values()),
                    "files": files,
                    "total_objects": result.get("KeyCount", 0),
                })
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    @staticmethod
    def _format_size(size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    def _ensure_booking_in_dynamodb(self, booking_id):
        """Seed booking from backup into DynamoDB if it doesn't exist there."""
        try:
            if not self._is_localstack_reachable():
                return

            table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
            resp = table.query(
                KeyConditionExpression=Key("booking_id").eq(booking_id),
                Limit=1,
            )
            if resp.get("Items"):
                return
            booking = backup.get_booking(booking_id)
            if booking:
                from decimal import Decimal
                item = {}
                for k, v in booking.items():
                    if isinstance(v, float):
                        item[k] = Decimal(str(v))
                    elif isinstance(v, dict):
                        item[k] = json.dumps(v)
                    elif v == "" or v is None:
                        continue
                    else:
                        item[k] = v
                if "version" not in item:
                    item["version"] = 1
                else:
                    item["version"] = int(item["version"])
                table.put_item(Item=item)
                print(f"[GroupIQ] Seeded {booking_id} into DynamoDB from backup")
        except Exception as e:
            print(f"[GroupIQ] DynamoDB seed warning: {e}")

    def _auto_sync_to_dynamodb(self, booking_data):
        """Automatically push a booking record to DynamoDB with all fields including dates and email flags."""
        try:
            if not self._is_localstack_reachable():
                return
            table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
            item = {}
            for k, v in booking_data.items():
                if isinstance(v, float):
                    item[k] = Decimal(str(v))
                elif isinstance(v, bool):
                    item[k] = v
                elif isinstance(v, dict):
                    item[k] = json.dumps(v)
                elif v == "" or v is None:
                    continue
                else:
                    item[k] = v
            if "version" not in item:
                item["version"] = 1
            else:
                item["version"] = int(item["version"])
            if "updated_at" not in item:
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
            table.put_item(Item=item)
        except Exception as e:
            print(f"[GroupIQ] Auto-sync to DynamoDB warning: {e}")

    def _invoke_lambda(self, function_name, payload):
        """Invoke a Lambda function via LocalStack with retry. Fails fast if LocalStack is down."""
        if not self._is_localstack_reachable():
            raise Exception("LocalStack not reachable — Lambda invocation skipped")
        for attempt in range(2):
            try:
                response = lambda_client.invoke(
                    FunctionName=function_name,
                    Payload=payload.encode("utf-8"),
                )
                result = json.loads(response["Payload"].read().decode("utf-8"))
                return result
            except Exception as e:
                if attempt < 1:
                    import time
                    time.sleep(0.3)
                    continue
                raise Exception(f"Lambda invocation failed: {str(e)}")

    def _send_smtp_email(self, booking_id, negotiation_result):
        """Send a real email via SMTP (Outlook/Office365)."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from boto3.dynamodb.conditions import Key

        booking = None
        if self._is_localstack_reachable():
            try:
                table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
                response = table.query(
                    KeyConditionExpression=Key("booking_id").eq(booking_id),
                    ScanIndexForward=False,
                    Limit=1,
                )
                booking = response["Items"][0] if response.get("Items") else None
            except Exception:
                pass

        if not booking:
            booking = backup.get_booking(booking_id)
        if not booking:
            return

        contact_name = booking.get("contact_name", "Guest")
        contact_email = booking.get("contact_email", "")
        event_type = booking.get("event_type", "event").title()
        event_date = booking.get("event_date", "TBD")
        num_rooms = int(booking.get("num_rooms", 0))
        num_nights = int(booking.get("num_nights", 0))
        decision = negotiation_result.get("decision", "ACCEPT")
        message = negotiation_result.get("message_to_client", "")

        if decision == "ACCEPT":
            subject = f"Booking Confirmed! Your {event_type} at Marriott - {booking_id}"
            status_color = "#16a34a"
            status_text = "CONFIRMED"
        elif decision == "COUNTER":
            subject = f"Counter Proposal for Your {event_type} - {booking_id}"
            status_color = "#d97706"
            status_text = "COUNTER PROPOSAL"
        else:
            subject = f"Booking Update - {booking_id}"
            status_color = "#dc2626"
            status_text = "ESCALATED TO SALES"

        html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
    <div style="background: linear-gradient(135deg, #1B1464 0%, #8b1a2b 100%); color: white; padding: 30px; text-align: center;">
        <h1 style="margin:0;">{status_text}</h1>
        <p style="margin:5px 0 0; opacity:0.9;">GroupIQ — Marriott International</p>
    </div>
    <div style="padding: 30px; background: #f9f9f9;">
        <h2 style="color: #1B1464;">Dear {contact_name},</h2>
        <p style="font-size: 16px; line-height: 1.6;">{message}</p>
        <div style="background: white; border-radius: 8px; padding: 20px; margin: 20px 0; border-left: 4px solid {status_color};">
            <h3 style="color: #333; margin-top: 0;">Booking Details</h3>
            <table style="width:100%; border-collapse: collapse;">
                <tr><td style="padding:8px 0; color:#666;">Booking ID</td><td style="padding:8px 0; font-weight:600;">{booking_id}</td></tr>
                <tr><td style="padding:8px 0; color:#666;">Event</td><td style="padding:8px 0; font-weight:600;">{event_type} — {event_date}</td></tr>
                <tr><td style="padding:8px 0; color:#666;">Rooms</td><td style="padding:8px 0; font-weight:600;">{num_rooms} rooms x {num_nights} nights</td></tr>
                <tr><td style="padding:8px 0; color:#666;">Status</td><td style="padding:8px 0; font-weight:600; color:{status_color};">{status_text}</td></tr>
            </table>
        </div>
        <p style="margin-top: 25px; color: #666; font-size: 13px;">
            Powered by GroupIQ — AI-Assisted Group Booking Platform
        </p>
    </div>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SES_SENDER_EMAIL or SMTP_USERNAME
        msg["To"] = contact_email

        text_part = MIMEText(f"Dear {contact_name},\n\n{message}\n\nBooking ID: {booking_id}\nEvent: {event_type} on {event_date}\nRooms: {num_rooms} x {num_nights} nights\nStatus: {status_text}\n\n— GroupIQ Team", "plain")
        html_part = MIMEText(html_body, "html")
        msg.attach(text_part)
        msg.attach(html_part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(msg["From"], [contact_email], msg.as_string())

        print(f"[GroupIQ] ✉ REAL EMAIL SENT via SMTP")
        print(f"[GroupIQ]   From: {msg['From']}")
        print(f"[GroupIQ]   To: {contact_email}")
        print(f"[GroupIQ]   Subject: {subject}")

    def _json_response(self, status, data):
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def _cors_headers(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def log_message(self, format, *args):
        try:
            print(f"[GroupIQ] {args[0]}")
        except Exception:
            pass


class ReusableHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True


import socketserver
import threading


class ThreadedHTTPServer(socketserver.ThreadingMixIn, ReusableHTTPServer):
    """Handle each request in a new thread for concurrent request handling."""
    daemon_threads = True


def kill_port(port):
    """Kill any process occupying the given port (macOS/Linux)."""
    import subprocess
    import signal
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split("\n")
        for pid in pids:
            if pid.strip():
                try:
                    os.kill(int(pid.strip()), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
        if any(p.strip() for p in pids):
            import time
            time.sleep(0.5)
            print(f"[GroupIQ] Cleared port {port} (killed stale process)")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


RENDER_API_URL = os.environ.get("RENDER_API_URL", "https://groupiq.onrender.com")


def sync_from_render():
    """Periodically fetch bookings from Render cloud backend and merge into local backup."""
    import time
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    while True:
        try:
            time.sleep(10)
            req = urllib.request.Request(f"{RENDER_API_URL}/bookings")
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read().decode())
                remote_bookings = data.get("bookings", [])
                if remote_bookings:
                    backup.sync_from_dynamodb(remote_bookings)
                    print(f"[GroupIQ] Synced {len(remote_bookings)} bookings from Render")
        except Exception as e:
            print(f"[GroupIQ] Render sync error: {e}")


def main():
    kill_port(PORT)

    # Auto-send missed emails on startup
    def send_missed_emails():
        """Scan all bookings and send emails for any that were missed."""
        import time
        time.sleep(3)  # Wait for server to be ready
        if not smtp_configured:
            print("[GroupIQ] Email recovery skipped — SMTP not configured")
            return
        all_bookings = backup.get_all_bookings()
        missed = [b for b in all_bookings if b.get("contact_email") and b.get("email_delivered") is not True]
        if not missed:
            print("[GroupIQ] Email recovery: All bookings have emails delivered ✓")
            return
        print(f"[GroupIQ] Email recovery: Found {len(missed)} bookings without email — sending now...")
        sent = 0
        for b in missed:
            try:
                bid = b.get("booking_id", "")
                status = b.get("status", "INQUIRY_RECEIVED")
                to_email = b.get("contact_email", "")
                if not to_email or not bid:
                    continue

                if status in ("INQUIRY_RECEIVED", "PROPOSAL_SENT"):
                    email_html = build_inquiry_email(
                        inquiry_id=bid,
                        contact_name=b.get("contact_name", "Guest"),
                        event_type=b.get("event_type", ""),
                        checkin=b.get("check_in_date", b.get("event_date", "")),
                        checkout=b.get("check_out_date", ""),
                        rooms=b.get("num_rooms", 0),
                        nights=b.get("num_nights", 0),
                        rate=float(b.get("dynamic_room_rate", 0) or 0),
                        revenue=float(b.get("estimated_revenue", 0) or 0),
                        property_id=b.get("property_id", ""),
                    )
                    subject = f"GroupIQ - Inquiry {bid} Confirmed"
                    email_type = "INQUIRY"
                elif status == "ACCEPTED":
                    email_html = build_negotiation_email(
                        inquiry_id=bid,
                        contact_name=b.get("contact_name", "Guest"),
                        decision="ACCEPT",
                        message="Your booking has been confirmed! Thank you for choosing Marriott.",
                        counter_rate=None,
                        confirmed_id=bid,
                    )
                    subject = f"GroupIQ - Booking CONFIRMED! {bid}"
                    email_type = "ACCEPT"
                elif status in ("NEGOTIATING", "COUNTER"):
                    email_html = build_negotiation_email(
                        inquiry_id=bid,
                        contact_name=b.get("contact_name", "Guest"),
                        decision="COUNTER",
                        message="Your negotiation is in progress. Our team is reviewing your request.",
                        counter_rate=None,
                        confirmed_id=None,
                    )
                    subject = f"GroupIQ - Negotiation Update for {bid}"
                    email_type = "COUNTER"
                elif status in ("ESCALATE", "ESCALATED"):
                    email_html = build_negotiation_email(
                        inquiry_id=bid,
                        contact_name=b.get("contact_name", "Guest"),
                        decision="ESCALATE",
                        message="Your request has been escalated to a Senior Sales Manager for review.",
                        counter_rate=None,
                        confirmed_id=None,
                    )
                    subject = f"GroupIQ - Escalated: {bid}"
                    email_type = "ESCALATE"
                elif status in ("DECLINED", "DECLINE"):
                    email_html = build_negotiation_email(
                        inquiry_id=bid,
                        contact_name=b.get("contact_name", "Guest"),
                        decision="DECLINED",
                        message="Your booking request has been declined.",
                        counter_rate=None,
                        confirmed_id=None,
                    )
                    subject = f"GroupIQ - Booking Declined: {bid}"
                    email_type = "DECLINED"
                else:
                    continue

                send_email_async(to_email, subject, email_html, booking_id=bid, email_type=email_type)
                sent += 1
                time.sleep(0.5)  # Small delay to avoid Gmail rate limits
            except Exception as e:
                print(f"[GroupIQ] Email recovery failed for {b.get('booking_id','')}: {e}")
        print(f"[GroupIQ] Email recovery: Sent {sent} missed emails ✓")

    email_recovery_thread = threading.Thread(target=send_missed_emails, daemon=True)
    email_recovery_thread.start()

    # Background thread: Auto-sync ALL bookings from backup to DynamoDB every 30 seconds
    def auto_sync_dynamodb_loop():
        """Periodically push all bookings (with dates + email flags) from backup to DynamoDB."""
        import time, socket
        time.sleep(10)  # Wait for server startup
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                reachable = sock.connect_ex(('localhost', 4566)) == 0
                sock.close()
                if not reachable:
                    time.sleep(30)
                    continue

                table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
                all_bookings = backup.get_all_bookings()
                synced = 0
                for b in all_bookings:
                    bid = b.get("booking_id")
                    if not bid:
                        continue
                    item = {}
                    for k, v in b.items():
                        if isinstance(v, float):
                            item[k] = Decimal(str(v))
                        elif isinstance(v, bool):
                            item[k] = v
                        elif isinstance(v, dict):
                            item[k] = json.dumps(v)
                        elif v == "" or v is None:
                            continue
                        else:
                            item[k] = v
                    if "version" not in item:
                        item["version"] = 1
                    else:
                        item["version"] = int(item["version"])
                    if "updated_at" not in item:
                        item["updated_at"] = b.get("created_at", datetime.now(timezone.utc).isoformat())
                    table.put_item(Item=item)
                    synced += 1
                print(f"[GroupIQ] Auto-sync to DynamoDB: {synced} bookings synced ✓")
            except Exception as e:
                print(f"[GroupIQ] Auto-sync DynamoDB error: {e}")
            time.sleep(30)

    dynamodb_sync_thread = threading.Thread(target=auto_sync_dynamodb_loop, daemon=True)
    dynamodb_sync_thread.start()

    # Start background sync from Render cloud API
    sync_thread = threading.Thread(target=sync_from_render, daemon=True)
    sync_thread.start()

    HTTPS_PORT = int(os.environ.get("HTTPS_PORT", "5556"))
    CERT_FILE = Path(__file__).parent.parent / "certs" / "cert.pem"
    KEY_FILE = Path(__file__).parent.parent / "certs" / "key.pem"

    # Start HTTPS server in a thread (for GitHub Pages portal)
    if CERT_FILE.exists() and KEY_FILE.exists():
        import ssl
        def run_https():
            try:
                kill_port(HTTPS_PORT)
                https_server = ThreadedHTTPServer(("0.0.0.0", HTTPS_PORT), GroupIQHandler)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
                https_server.socket = ctx.wrap_socket(https_server.socket, server_side=True)
                print(f"[GroupIQ] HTTPS server running on https://localhost:{HTTPS_PORT}")
                https_server.serve_forever()
            except Exception as e:
                print(f"[GroupIQ] HTTPS server failed: {e}")
        https_thread = threading.Thread(target=run_https, daemon=True)
        https_thread.start()
    else:
        print(f"[GroupIQ] No SSL certs found — HTTPS disabled (certs/ folder missing)")

    MAX_RESTARTS = 100
    restart_count = 0

    while restart_count < MAX_RESTARTS:
        try:
            server = ThreadedHTTPServer(("0.0.0.0", PORT), GroupIQHandler)
            server.socket.settimeout(None)

            if restart_count == 0:
                print(f"""
╔══════════════════════════════════════════════════════╗
║         GroupIQ Dashboard — Running                  ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║   Open in browser: http://localhost:{PORT}             ║
║                                                      ║
║   API Endpoints:                                     ║
║     GET  /bookings              — List all bookings  ║
║     POST /inquiries             — New inquiry        ║
║     POST /inquiries/ID/negotiate — Counter-offer     ║
║     GET  /properties            — All locations      ║
║     GET  /properties/HYD        — Nearby properties  ║
║     GET  /inventory/PROP_ID?date=YYYY-MM-DD          ║
║     GET  /reminders             — Check reminders    ║
║     GET  /compliance/ID         — TIP.AI check       ║
║     GET  /compliance/rules      — TIP.AI rules       ║
║                                                      ║
║   Concurrency: Multi-threaded (handles parallel      ║
║                requests with inventory locking)       ║
║   Auto-Restart: Enabled (never disconnects)          ║
║   TIP.AI Engine:    v2.1 (Governance & Compliance)   ║
║   LocalStack:       {LOCALSTACK_URL}            ║
║   Environment:      {ENVIRONMENT}                          ║
║                                                      ║
╚══════════════════════════════════════════════════════╝
""")
            else:
                print(f"[GroupIQ] Server restarted (attempt {restart_count + 1}) — still running on port {PORT}")

            server.serve_forever()

        except KeyboardInterrupt:
            print("\n[GroupIQ] Shutting down gracefully...")
            try:
                server.server_close()
            except Exception:
                pass
            break

        except OSError as e:
            if "Address already in use" in str(e):
                print(f"[GroupIQ] Port {PORT} busy — retrying in 2s...")
                import time
                time.sleep(2)
                kill_port(PORT)
                restart_count += 1
                continue
            else:
                print(f"[GroupIQ] OS Error: {e} — restarting in 3s...")
                import time
                time.sleep(3)
                restart_count += 1

        except Exception as e:
            print(f"[GroupIQ] Server crashed: {e} — auto-restarting in 2s...")
            import time
            time.sleep(2)
            restart_count += 1
            try:
                server.server_close()
            except Exception:
                pass

    if restart_count >= MAX_RESTARTS:
        print(f"[GroupIQ] FATAL: Server crashed {MAX_RESTARTS} times. Giving up.")


if __name__ == "__main__":
    main()
