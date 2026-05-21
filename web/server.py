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
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

LOCALSTACK_URL = os.environ.get("LOCALSTACK_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "local")
PORT = int(os.environ.get("PORT", "5555"))

# SMTP configuration for sending real emails (Outlook/Office365)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.office365.com")
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

# SMTP email setup
smtp_configured = bool(SMTP_USERNAME and SMTP_PASSWORD)
if smtp_configured:
    print(f"[GroupIQ] SMTP email configured: {SMTP_USERNAME} via {SMTP_HOST}:{SMTP_PORT}")
else:
    print(f"[GroupIQ] SMTP not configured — emails will be logged to console only")
    print(f"[GroupIQ] To enable real emails, set: SMTP_USERNAME, SMTP_PASSWORD, SES_SENDER_EMAIL")

WEB_DIR = Path(__file__).parent


# ─── Booking Backup System ───────────────────────────────────────────────────

class BookingBackup:
    """Persistent JSON backup for booking data that survives LocalStack restarts."""

    def __init__(self, backup_path: Path):
        self.path = backup_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
        existing = self._data["bookings"].get(bid)
        if not existing or int(booking.get("version", 0)) >= int(existing.get("version", 0)):
            self._data["bookings"][bid] = json.loads(json.dumps(booking, default=str))
            self._save()

    def sync_from_dynamodb(self, items: list):
        for item in items:
            self.upsert_booking(item)

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

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/bookings":
            self._handle_get_bookings()
        elif self.path.startswith("/bookings/report") or self.path.startswith("/bookings/report?"):
            self._handle_bookings_report()
        elif self.path == "/bookings/backup/stats":
            self._handle_backup_stats()
        elif self.path.startswith("/bookings/"):
            booking_id = self.path.split("/bookings/")[1]
            self._handle_get_booking(booking_id)
        elif self.path == "/reminders" or self.path.startswith("/reminders?"):
            self._handle_check_reminders()
        elif self.path == "/compliance/rules":
            self._handle_compliance_rules()
        elif self.path.startswith("/compliance/"):
            booking_id = self.path.split("/compliance/")[1]
            self._handle_compliance_check(booking_id)
        elif self.path.startswith("/properties/"):
            location = self.path.split("/properties/")[1].split("?")[0]
            self._handle_nearby_properties(location)
        elif self.path == "/properties" or self.path.startswith("/properties?"):
            self._handle_all_locations()
        else:
            super().do_GET()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"

        if self.path == "/inquiries":
            self._handle_new_inquiry(body)
        elif "/negotiate" in self.path:
            parts = self.path.split("/")
            booking_id = parts[2] if len(parts) >= 3 else ""
            self._handle_negotiate(booking_id, body)
        else:
            self._json_response(404, {"error": "Not found"})

    def _handle_get_bookings(self):
        """Scan all bookings from DynamoDB and sync to backup."""
        try:
            table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
            response = table.scan()
            items = response.get("Items", [])

            # Sync all items to permanent backup
            backup.sync_from_dynamodb(items)

            # Deduplicate by booking_id (keep latest version)
            latest = {}
            for item in items:
                bid = item["booking_id"]
                if bid not in latest or int(item["version"]) > int(latest[bid]["version"]):
                    latest[bid] = item

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

    def _handle_bookings_report(self):
        """Return booking analytics filtered by period (week/month/year/all)."""
        try:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            period = params.get("period", ["all"])[0]

            # First sync latest from DynamoDB
            try:
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
            payload = json.dumps({
                "body": body,
                "requestContext": {"http": {"method": "POST"}},
            })
            result = self._invoke_lambda("groupiq-intake-" + ENVIRONMENT, payload)
            response_body = json.loads(result.get("body", "{}"))

            # Auto-backup the new booking
            if response_body.get("booking_id"):
                booking_data = json.loads(body)
                booking_data["booking_id"] = response_body["booking_id"]
                booking_data["version"] = 1
                booking_data["status"] = "INQUIRY_RECEIVED"
                booking_data["created_at"] = response_body.get("created_at", datetime.now(timezone.utc).isoformat())
                booking_data["estimated_revenue"] = response_body.get("estimated_revenue", 0)
                backup.upsert_booking(booking_data)

            self._json_response(result.get("statusCode", 201), response_body)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _handle_negotiate(self, booking_id, body):
        """Submit a counter-offer for negotiation."""
        try:
            payload_data = json.loads(body)
            payload_data["booking_id"] = booking_id
            payload = json.dumps(payload_data)
            result = self._invoke_lambda("groupiq-negotiation_agent-" + ENVIRONMENT, payload)

            if isinstance(result, dict) and "body" in result:
                response_data = json.loads(result.get("body", "{}"))
            else:
                response_data = result

            # Send real email via SMTP if configured
            if smtp_configured and response_data.get("email_sent"):
                try:
                    self._send_smtp_email(booking_id, response_data)
                    response_data["real_email_delivered"] = True
                except Exception as email_err:
                    response_data["real_email_delivered"] = False
                    response_data["email_error"] = str(email_err)
                    print(f"[GroupIQ] SMTP email error: {email_err}")

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

    def _invoke_lambda(self, function_name, payload):
        """Invoke a Lambda function via LocalStack."""
        response = lambda_client.invoke(
            FunctionName=function_name,
            Payload=payload.encode("utf-8"),
        )
        result = json.loads(response["Payload"].read().decode("utf-8"))
        return result

    def _send_smtp_email(self, booking_id, negotiation_result):
        """Send a real email via SMTP (Outlook/Office365)."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from boto3.dynamodb.conditions import Key

        table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
        response = table.query(
            KeyConditionExpression=Key("booking_id").eq(booking_id),
            ScanIndexForward=False,
            Limit=1,
        )
        booking = response["Items"][0] if response.get("Items") else {}
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        print(f"[GroupIQ] {args[0]}")


class ReusableHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True


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


def main():
    kill_port(PORT)
    server = ReusableHTTPServer(("0.0.0.0", PORT), GroupIQHandler)
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
║     GET  /reminders             — Check reminders    ║
║     GET  /compliance/ID         — TIP.AI check       ║
║     GET  /compliance/rules      — TIP.AI rules       ║
║                                                      ║
║   TIP.AI Engine:    v2.1 (Governance & Compliance)   ║
║   LocalStack:       {LOCALSTACK_URL}            ║
║   Environment:      {ENVIRONMENT}                          ║
║                                                      ║
╚══════════════════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
