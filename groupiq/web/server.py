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

import boto3

LOCALSTACK_URL = os.environ.get("LOCALSTACK_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "local")
PORT = int(os.environ.get("PORT", "5555"))

# AWS clients pointing at LocalStack
session = boto3.Session(
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name=REGION,
)
lambda_client = session.client("lambda", endpoint_url=LOCALSTACK_URL)
dynamodb = session.resource("dynamodb", endpoint_url=LOCALSTACK_URL)

WEB_DIR = Path(__file__).parent


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
        """Scan all bookings from DynamoDB."""
        try:
            table = dynamodb.Table(f"groupiq-bookings-{ENVIRONMENT}")
            response = table.scan()
            items = response.get("Items", [])

            # Deduplicate by booking_id (keep latest version)
            latest = {}
            for item in items:
                bid = item["booking_id"]
                if bid not in latest or int(item["version"]) > int(latest[bid]["version"]):
                    latest[bid] = item

            bookings = sorted(latest.values(), key=lambda x: x.get("created_at", ""), reverse=True)
            # Convert Decimal to float for JSON
            clean = json.loads(json.dumps(bookings, default=str))
            self._json_response(200, {"bookings": clean, "count": len(clean)})
        except Exception as e:
            self._json_response(500, {"error": str(e)})

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
        """Submit a new group booking inquiry."""
        try:
            payload = json.dumps({
                "body": body,
                "requestContext": {"http": {"method": "POST"}},
            })
            result = self._invoke_lambda("groupiq-intake-" + ENVIRONMENT, payload)
            self._json_response(result.get("statusCode", 201), json.loads(result.get("body", "{}")))
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
                self._json_response(result.get("statusCode", 200), json.loads(result.get("body", "{}")))
            else:
                self._json_response(200, result)
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

    def _invoke_lambda(self, function_name, payload):
        """Invoke a Lambda function via LocalStack."""
        response = lambda_client.invoke(
            FunctionName=function_name,
            Payload=payload.encode("utf-8"),
        )
        result = json.loads(response["Payload"].read().decode("utf-8"))
        return result

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


def main():
    server = http.server.HTTPServer(("0.0.0.0", PORT), GroupIQHandler)
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
