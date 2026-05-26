"""
GroupIQ Production API Server — Deployed on Render.
Handles bookings from public GitHub Pages portal without LocalStack dependency.
Uses file-based JSON storage for persistence.
"""
import json
import http.server
import urllib.parse
import os
import sys
import threading
import smtplib
import socketserver
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime, timedelta, timezone

PORT = int(os.environ.get("PORT", "5555"))
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "")

DATA_DIR = Path(__file__).parent.parent / "data"
BOOKINGS_FILE = DATA_DIR / "bookings_prod.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

smtp_configured = bool(SMTP_USERNAME and SMTP_PASSWORD)
if smtp_configured:
    print(f"[GroupIQ-Prod] SMTP configured: {SMTP_USERNAME}")
else:
    print(f"[GroupIQ-Prod] SMTP not configured — emails logged only")

_lock = threading.Lock()


def load_bookings():
    if BOOKINGS_FILE.exists():
        try:
            with open(BOOKINGS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_bookings(data):
    with open(BOOKINGS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


def gen_id():
    ts = datetime.now().strftime("%Y%m%d")
    suffix = ''.join(random.choices(string.hexdigits[:16], k=8)).upper()
    return f"INQ-{ts}-{suffix}"


def send_email(to_email, subject, html_body):
    sender = SES_SENDER_EMAIL or SMTP_USERNAME
    if not smtp_configured:
        print(f"[EMAIL-LOG] To: {to_email} | Subject: {subject} (not sent)")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"GroupIQ Marriott <{sender}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(sender, to_email, msg.as_string())
        print(f"[EMAIL-SENT] To: {to_email} | Subject: {subject}")
        return True
    except Exception as e:
        print(f"[EMAIL-ERROR] {e}")
        return False


def calculate_pricing(num_rooms, num_nights, event_type):
    """Simple dynamic pricing without Lambda."""
    base_rates = {
        "corporate": 280, "conference": 320, "wedding": 450,
        "social": 350, "sports": 300, "ipl_cricket": 500,
        "government": 250, "education": 220, "other": 300
    }
    base = base_rates.get(event_type, 300)
    if num_rooms >= 50:
        base *= 0.85
    elif num_rooms >= 30:
        base *= 0.90
    elif num_rooms >= 20:
        base *= 0.93
    if num_nights >= 5:
        base *= 0.92
    elif num_nights >= 3:
        base *= 0.95
    final_rate = round(base, 2)
    revenue = round(final_rate * num_rooms * num_nights, 2)
    return {"final_rate": final_rate, "base_rate": base_rates.get(event_type, 300), "discount_applied": True}, revenue


def build_inquiry_email(inquiry_id, contact_name, event_type, checkin, checkout, rooms, nights, rate, revenue, property_id):
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
                Your group booking inquiry has been received and is being processed.
            </p>
            <div style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 10px; padding: 20px; margin: 20px 0;">
                <h3 style="color: #1e40af; font-size: 14px; margin: 0 0 12px 0;">Inquiry Details</h3>
                <table style="width: 100%; font-size: 13px; color: #334155;">
                    <tr><td style="padding: 6px 0;"><strong>Inquiry ID:</strong></td><td style="color: #1e40af; font-weight: 700;">{inquiry_id}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Event Type:</strong></td><td>{event_type}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Property:</strong></td><td>{property_id}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Check-in:</strong></td><td>{checkin}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Check-out:</strong></td><td>{checkout}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Rooms:</strong></td><td>{rooms}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Nights:</strong></td><td>{nights}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Rate/Night:</strong></td><td style="font-weight: 700;">${rate:.0f}</td></tr>
                    <tr><td style="padding: 6px 0;"><strong>Revenue:</strong></td><td style="font-weight: 700; color: #059669;">${revenue:,.0f}</td></tr>
                </table>
            </div>
            <p style="color: #475569; font-size: 14px;">Our team will review within <strong>24 hours</strong>.</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #94a3b8; font-size: 11px; text-align: center;">Marriott GroupIQ | Group Booking Intelligence Platform</p>
        </div>
    </div>
    """


def build_negotiation_email(inquiry_id, contact_name, decision, message, counter_rate=None):
    decision_color = {"ACCEPT": "#059669", "COUNTER": "#d97706", "ESCALATE": "#dc2626", "DECLINED": "#dc2626"}.get(decision, "#64748b")
    decision_label = {"ACCEPT": "ACCEPTED", "COUNTER": "COUNTER OFFER", "ESCALATE": "ESCALATED", "DECLINED": "DECLINED"}.get(decision, decision)
    extra = ""
    if decision == "COUNTER" and counter_rate:
        extra = f'<div style="background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px; padding: 20px; margin: 16px 0;"><p style="font-weight: 600; color: #92400e;">Counter Proposal: <strong>${counter_rate}/night</strong></p></div>'
    return f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 30px; background: #f8fafc;">
        <div style="background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
            <div style="text-align: center; margin-bottom: 24px;">
                <h1 style="color: #1e293b; font-size: 22px; margin: 0;">Marriott | GroupIQ</h1>
                <p style="color: #64748b; font-size: 13px; margin-top: 4px;">Negotiation Update</p>
            </div>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #334155; font-size: 15px;">Dear <strong>{contact_name}</strong>,</p>
            <p style="color: #475569; font-size: 14px;">Update for inquiry <strong style="color: #1e40af;">{inquiry_id}</strong>:</p>
            <div style="text-align: center; margin: 20px 0;">
                <span style="background: {decision_color}; color: white; padding: 8px 24px; border-radius: 20px; font-size: 13px; font-weight: 700;">{decision_label}</span>
            </div>
            <p style="color: #475569; font-size: 14px; background: #f8fafc; padding: 16px; border-radius: 8px; border-left: 4px solid {decision_color};">{message}</p>
            {extra}
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #94a3b8; font-size: 11px; text-align: center;">Marriott GroupIQ | Group Booking Intelligence Platform</p>
        </div>
    </div>
    """


class ProdHandler(http.server.BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == "/health":
                self._json_response(200, {"status": "ok", "bookings": len(load_bookings())})
            elif self.path == "/bookings":
                self._handle_get_bookings()
            elif self.path == "/properties" or self.path.startswith("/properties?"):
                self._handle_all_locations()
            elif self.path.startswith("/properties/"):
                location = self.path.split("/properties/")[1].split("?")[0]
                self._handle_nearby_properties(location)
            else:
                self._json_response(404, {"error": "Not found"})
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"

            if self.path == "/customer/inquiries" or self.path == "/inquiries":
                self._handle_new_inquiry(body)
            elif "/customer/inquiries/" in self.path and "/negotiate" in self.path:
                bid = self.path.split("/customer/inquiries/")[1].split("/negotiate")[0]
                self._handle_negotiate(bid, body)
            elif "/negotiate" in self.path:
                parts = self.path.split("/")
                bid = parts[2] if len(parts) >= 3 else ""
                self._handle_negotiate(bid, body)
            else:
                self._json_response(404, {"error": "Not found"})
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_get_bookings(self):
        with _lock:
            bookings = load_bookings()
        items = sorted(bookings.values(), key=lambda x: x.get("created_at", ""), reverse=True)
        self._json_response(200, {"bookings": items, "count": len(items)})

    def _handle_new_inquiry(self, body):
        parsed = json.loads(body)
        inquiry_id = gen_id()
        num_rooms = int(parsed.get("num_rooms", 10))
        num_nights = int(parsed.get("num_nights", 1))
        event_type = parsed.get("event_type", "other")
        pricing, revenue = calculate_pricing(num_rooms, num_nights, event_type)

        booking = {
            "booking_id": inquiry_id,
            "inquiry_id": inquiry_id,
            "contact_name": parsed.get("contact_name", ""),
            "contact_email": parsed.get("contact_email", ""),
            "company_name": parsed.get("company_name", ""),
            "event_type": event_type,
            "check_in_date": parsed.get("check_in_date", parsed.get("event_date", "")),
            "check_out_date": parsed.get("check_out_date", ""),
            "num_rooms": num_rooms,
            "num_nights": num_nights,
            "property_id": parsed.get("property_id", ""),
            "status": "INQUIRY_RECEIVED",
            "version": 1,
            "estimated_revenue": revenue,
            "dynamic_pricing": pricing,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "fnb_required": parsed.get("fnb_required", False),
            "meeting_space_required": parsed.get("meeting_space_required", False),
            "special_requests": parsed.get("special_requests", ""),
            "budget_indication": parsed.get("budget_indication", ""),
        }

        with _lock:
            bookings = load_bookings()
            bookings[inquiry_id] = booking
            save_bookings(bookings)

        # Send email
        to_email = parsed.get("contact_email", "")
        email_sent = False
        if to_email:
            html = build_inquiry_email(
                inquiry_id, parsed.get("contact_name", "Guest"), event_type,
                booking["check_in_date"], booking["check_out_date"],
                num_rooms, num_nights, pricing["final_rate"], revenue,
                parsed.get("property_id", "")
            )
            email_sent = send_email(to_email, f"GroupIQ - Inquiry {inquiry_id} Confirmed", html)

        self._json_response(201, {
            "booking_id": inquiry_id,
            "inquiry_id": inquiry_id,
            "status": "INQUIRY_RECEIVED",
            "dynamic_pricing": pricing,
            "estimated_revenue": revenue,
            "created_at": booking["created_at"],
            "email_delivered": email_sent,
        })

    def _handle_negotiate(self, booking_id, body):
        parsed = json.loads(body)
        customer_rate = float(parsed.get("proposed_rate", parsed.get("counter_rate", 0)))
        action = parsed.get("action", "counter").upper()

        with _lock:
            bookings = load_bookings()
            booking = bookings.get(booking_id)

        if not booking:
            self._json_response(404, {"error": f"Booking {booking_id} not found"})
            return

        current_rate = float(booking.get("dynamic_pricing", {}).get("final_rate", 300))
        contact_name = booking.get("contact_name", "Guest")
        to_email = booking.get("contact_email", "")

        if action == "ACCEPT":
            decision = "ACCEPT"
            message = "Your booking has been confirmed at the agreed rate. Welcome to Marriott!"
            new_status = "ACCEPTED"
        elif action == "DECLINE":
            decision = "DECLINED"
            message = "We regret that we are unable to accommodate your request at this time."
            new_status = "DECLINED"
        else:
            # Negotiation logic
            diff_pct = (current_rate - customer_rate) / current_rate * 100 if current_rate > 0 else 0
            if diff_pct <= 5:
                decision = "ACCEPT"
                message = f"We are pleased to accept your rate of ${customer_rate:.0f}/night."
                new_status = "ACCEPTED"
            elif diff_pct <= 15:
                counter = round(current_rate * 0.93, 0)
                decision = "COUNTER"
                message = f"We appreciate your offer. We can offer ${counter:.0f}/night as our best rate."
                new_status = "NEGOTIATING"
            elif diff_pct <= 25:
                counter = round(current_rate * 0.88, 0)
                decision = "COUNTER"
                message = f"Thank you for your interest. Our counter-offer is ${counter:.0f}/night including complimentary breakfast."
                new_status = "NEGOTIATING"
            else:
                decision = "ESCALATE"
                message = "Your request has been escalated to our senior revenue manager for special consideration."
                new_status = "NEGOTIATING"

        now_ts = datetime.now(timezone.utc).isoformat()
        with _lock:
            bookings = load_bookings()
            if booking_id in bookings:
                bookings[booking_id]["status"] = new_status
                bookings[booking_id]["updated_at"] = now_ts
                bookings[booking_id]["version"] = int(bookings[booking_id].get("version", 1)) + 1
                save_bookings(bookings)

        counter_rate = None
        if decision == "COUNTER":
            counter_rate = round(current_rate * 0.93 if diff_pct <= 15 else current_rate * 0.88, 0)

        email_sent = False
        if to_email:
            html = build_negotiation_email(booking_id, contact_name, decision, message, counter_rate)
            subject_map = {
                "ACCEPT": f"GroupIQ - Booking CONFIRMED! {booking_id}",
                "COUNTER": f"GroupIQ - Counter Offer for {booking_id}",
                "ESCALATE": f"GroupIQ - Escalated: {booking_id}",
                "DECLINED": f"GroupIQ - Booking Declined: {booking_id}",
            }
            email_sent = send_email(to_email, subject_map.get(decision, f"GroupIQ - Update {booking_id}"), html)

        response = {
            "decision": decision,
            "status": new_status,
            "message_to_client": message,
            "email_delivered": email_sent,
        }
        if counter_rate:
            response["counter_proposal"] = {"room_rate": counter_rate}

        self._json_response(200, response)

    def _handle_all_locations(self):
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

    def _handle_nearby_properties(self, location):
        # Same property data as local server
        location_properties = {
            "HYD": [
                {"property_id": "MRIOTT-HYD-001", "name": "Marriott Hyderabad", "brand": "Marriott", "total_rooms": 320, "city": "Hyderabad", "country": "India"},
                {"property_id": "WESTIN-HYD-001", "name": "Westin Hyderabad Mindspace", "brand": "Westin", "total_rooms": 294, "city": "Hyderabad", "country": "India"},
                {"property_id": "SHRATN-HYD-001", "name": "Sheraton Hyderabad Hotel", "brand": "Sheraton", "total_rooms": 264, "city": "Hyderabad", "country": "India"},
                {"property_id": "COURTY-HYD-001", "name": "Courtyard by Marriott Hyderabad", "brand": "Courtyard", "total_rooms": 187, "city": "Hyderabad", "country": "India"},
                {"property_id": "FOURPT-HYD-001", "name": "Four Points by Sheraton Hyderabad", "brand": "Four Points", "total_rooms": 160, "city": "Hyderabad", "country": "India"},
            ],
            "BLR": [
                {"property_id": "MRIOTT-BLR-001", "name": "Marriott Bengaluru Whitefield", "brand": "Marriott", "total_rooms": 395, "city": "Bengaluru", "country": "India"},
                {"property_id": "SHRATN-BLR-001", "name": "Sheraton Grand Bengaluru", "brand": "Sheraton", "total_rooms": 230, "city": "Bengaluru", "country": "India"},
                {"property_id": "WESTIN-BLR-001", "name": "The Westin Bengaluru", "brand": "Westin", "total_rooms": 220, "city": "Bengaluru", "country": "India"},
                {"property_id": "COURTY-BLR-001", "name": "Courtyard by Marriott Bengaluru ORR", "brand": "Courtyard", "total_rooms": 179, "city": "Bengaluru", "country": "India"},
                {"property_id": "RITZ-BLR-001", "name": "The Ritz-Carlton Bengaluru", "brand": "Ritz-Carlton", "total_rooms": 277, "city": "Bengaluru", "country": "India"},
            ],
            "BOM": [
                {"property_id": "MRIOTT-BOM-001", "name": "JW Marriott Mumbai Juhu", "brand": "JW Marriott", "total_rooms": 355, "city": "Mumbai", "country": "India"},
                {"property_id": "WESTIN-BOM-001", "name": "The Westin Mumbai Garden City", "brand": "Westin", "total_rooms": 270, "city": "Mumbai", "country": "India"},
                {"property_id": "SHRATN-BOM-001", "name": "Sheraton Grand Powai Lake", "brand": "Sheraton", "total_rooms": 245, "city": "Mumbai", "country": "India"},
            ],
            "DEL": [
                {"property_id": "MRIOTT-DEL-001", "name": "Marriott Aerocity Delhi", "brand": "Marriott", "total_rooms": 331, "city": "New Delhi", "country": "India"},
                {"property_id": "JWMARR-DEL-001", "name": "JW Marriott New Delhi Aerocity", "brand": "JW Marriott", "total_rooms": 523, "city": "New Delhi", "country": "India"},
                {"property_id": "WESTIN-DEL-001", "name": "The Westin Gurgaon", "brand": "Westin", "total_rooms": 310, "city": "New Delhi", "country": "India"},
            ],
            "NYC": [
                {"property_id": "MRIOTT-NYC-001", "name": "Marriott Marquis NYC", "brand": "Marriott", "total_rooms": 500, "city": "New York", "country": "USA"},
                {"property_id": "WESTIN-NYC-001", "name": "Westin New York at Times Square", "brand": "Westin", "total_rooms": 450, "city": "New York", "country": "USA"},
                {"property_id": "SHRATN-NYC-001", "name": "Sheraton New York Times Square", "brand": "Sheraton", "total_rooms": 400, "city": "New York", "country": "USA"},
            ],
            "LAX": [
                {"property_id": "MRIOTT-LAX-001", "name": "Marriott LAX Airport", "brand": "Marriott", "total_rooms": 350, "city": "Los Angeles", "country": "USA"},
                {"property_id": "WESTIN-LAX-001", "name": "The Westin Bonaventure Hotel", "brand": "Westin", "total_rooms": 400, "city": "Los Angeles", "country": "USA"},
            ],
            "MAA": [
                {"property_id": "MRIOTT-MAA-001", "name": "Chennai Marriott Hotel", "brand": "Marriott", "total_rooms": 240, "city": "Chennai", "country": "India"},
                {"property_id": "WESTIN-MAA-001", "name": "The Westin Chennai Velachery", "brand": "Westin", "total_rooms": 218, "city": "Chennai", "country": "India"},
            ],
            "GOA": [
                {"property_id": "MRIOTT-GOA-001", "name": "Goa Marriott Resort & Spa", "brand": "Marriott", "total_rooms": 180, "city": "Goa", "country": "India"},
                {"property_id": "WESTIN-GOA-001", "name": "The Westin Goa", "brand": "Westin", "total_rooms": 192, "city": "Goa", "country": "India"},
            ],
            "JAI": [
                {"property_id": "MRIOTT-JAI-001", "name": "Jaipur Marriott Hotel", "brand": "Marriott", "total_rooms": 210, "city": "Jaipur", "country": "India"},
                {"property_id": "JWMARR-JAI-001", "name": "JW Marriott Jaipur Resort & Spa", "brand": "JW Marriott", "total_rooms": 200, "city": "Jaipur", "country": "India"},
            ],
            "PNQ": [
                {"property_id": "MRIOTT-PNQ-001", "name": "Marriott Suites Pune", "brand": "Marriott", "total_rooms": 192, "city": "Pune", "country": "India"},
                {"property_id": "JWMARR-PNQ-001", "name": "JW Marriott Hotel Pune", "brand": "JW Marriott", "total_rooms": 415, "city": "Pune", "country": "India"},
            ],
            "CCU": [
                {"property_id": "MRIOTT-CCU-001", "name": "Kolkata Marriott Hotel", "brand": "Marriott", "total_rooms": 240, "city": "Kolkata", "country": "India"},
                {"property_id": "JWMARR-CCU-001", "name": "JW Marriott Hotel Kolkata", "brand": "JW Marriott", "total_rooms": 280, "city": "Kolkata", "country": "India"},
            ],
        }
        loc = location.upper()
        props = location_properties.get(loc, [])
        results = []
        for p in props:
            avail = int(p["total_rooms"] * 0.35)
            results.append({**p, "estimated_available": avail, "can_accommodate": True, "occupancy_rate": "65%"})
        city = props[0]["city"] if props else location
        country = props[0]["country"] if props else "Unknown"
        self._json_response(200, {"location_code": loc, "city": city, "country": country, "total_properties": len(results), "properties": results})

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

    def log_message(self, fmt, *args):
        print(f"[GroupIQ-Prod] {args[0]}" if args else "")


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    server = ThreadedServer(("0.0.0.0", PORT), ProdHandler)
    print(f"[GroupIQ-Prod] Production API running on port {PORT}")
    print(f"[GroupIQ-Prod] Bookings file: {BOOKINGS_FILE}")
    print(f"[GroupIQ-Prod] SMTP: {'Enabled' if smtp_configured else 'Disabled'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[GroupIQ-Prod] Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
