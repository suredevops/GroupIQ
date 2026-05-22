# GroupIQ — AI-Powered Group Booking Platform for Marriott

An end-to-end serverless AI agent that automates group booking negotiations for Marriott International. It accepts group inquiries, calculates dynamic pricing based on 12+ market factors (including IPL cricket, Indian festivals, Marriott BAR strategy, and customer loyalty), generates customized proposals with AI, negotiates counter-offers autonomously with multi-round support, manages room inventory with concurrency control, and sends real email notifications — all without manual intervention.

**Live Demo:** Admin Portal `http://localhost:5555` | Customer Portal `http://localhost:5555/customer.html`

---

## Architecture Diagram

```
                                    ┌─────────────────────────────────────────────────────────┐
                                    │              AWS Cloud (us-east-1)                       │
                                    │                                                         │
 ┌──────────┐    HTTPS POST         │  ┌──────────────┐     ┌────────────────────────────┐   │
 │  Event   │───────────────────────┼─▶│ API Gateway  │────▶│  AWS Step Functions        │   │
 │  Planner │                       │  │  (HTTP API)  │     │  (Booking Workflow)         │   │
 └──────────┘                       │  └──────────────┘     └────────────┬───────────────┘   │
       ▲                            │                                    │                    │
       │                            │         ┌──────────────────────────┼──────────┐        │
       │  Email (SES/SMTP)          │         │                          │          │        │
       │                            │         ▼                          ▼          ▼        │
       │                            │  ┌─────────────┐  ┌──────────────────┐ ┌───────────┐  │
       │                            │  │   Lambda:   │  │    Lambda:       │ │  Lambda:  │  │
       │                            │  │   Intake    │  │ Proposal Gen     │ │Negotiation│  │
       │                            │  │ + Pricing   │  │  (Bedrock AI)    │ │  Agent    │  │
       │                            │  │   Engine    │  │                  │ │+ Inventory│  │
       │                            │  └──────┬──────┘  └────────┬─────────┘ └─────┬─────┘  │
       │                            │         │                  │                 │        │
       │                            │         ▼                  ▼                 ▼        │
       │                            │  ┌─────────────────────────────────────────────────┐  │
       │                            │  │              Amazon DynamoDB                     │  │
       │                            │  │  Bookings / Negotiations / Pricing / Inventory   │  │
       │                            │  └─────────────────────────────────────────────────┘  │
       │                            │                                                       │
       │                            │  ┌──────────────┐  ┌───────────┐  ┌───────────────┐  │
       └────────────────────────────┼──│ Amazon SES   │  │ Amazon S3 │  │  Amazon SNS   │  │
                                    │  │ (Emails)     │  │(Proposals)│  │ (Escalation)  │  │
                                    │  └──────────────┘  └───────────┘  └───────┬───────┘  │
                                    │                                            │          │
                                    └────────────────────────────────────────────┼──────────┘
                                                                                 │
                                                                                 ▼
                                                                          ┌─────────────┐
                                                                          │Sales Manager│
                                                                          │  (Human)    │
                                                                          └─────────────┘
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Customer Portal** | Separate customer-facing UI with email-based login, inquiry submission, multi-round negotiation, and communications log |
| **Admin Portal** | Full admin dashboard with all bookings, real-time auto-refresh (15s), compliance, inventory, and reports |
| **Dynamic Pricing Engine** | 12-factor rate calculation (seasonality, occupancy, competitor, lead time, holidays, IPL/cricket, festivals, Marriott BAR strategy, loyalty concession, group size, stay length, local demand) |
| **Customer Intelligence** | Marriott Bonvoy loyalty tier detection, repeat guest concessions, lifetime value scoring, corporate account benefits |
| **Multi-Round Negotiation** | Full negotiation lifecycle: ACCEPT ($265+) / COUNTER ($180-$265) / ESCALATE (<$180) / DECLINE with visual timeline |
| **Inquiry → Booking ID Flow** | INQ- prefix for inquiries, GRP- prefix generated only on acceptance — clear lifecycle tracking |
| **Concurrency Control** | Atomic room inventory with DynamoDB conditional writes — prevents overbooking under simultaneous load |
| **AI Negotiation** | Autonomous ACCEPT/COUNTER/ESCALATE decisions using Claude 3 Sonnet via Amazon Bedrock |
| **AI Proposal Generation** | Customized multi-tier proposals (Good/Better/Best) with pricing rationale |
| **Email Notifications** | Real email delivery via Gmail SMTP for booking confirmations, counter-offers, escalations, and declines |
| **Demand-Based Pricing** | IPL cricket matches, Indian festivals (Diwali, Holi), wedding season, corporate events drive rate adjustments |
| **Marriott Strategy** | BAR (Best Available Rate) logic with premium/value positioning based on demand score |
| **Room Inventory Tracking** | Real-time availability with soft holds during negotiation and hard reservations on acceptance |
| **Priority Queue** | Large bookings (100+ rooms) are queued for sequential processing |
| **TIP.AI Governance** | Enterprise compliance checks on all bookings (risk scoring, policy validation) |
| **Property Search** | Nearby Marriott properties with Google Maps, room type breakdowns, and availability |
| **Persistent Backup** | Booking data survives LocalStack restarts via local JSON backup |
| **Booking Reports** | Weekly, monthly, yearly analytics with revenue breakdowns |
| **Auto-Restart Server** | Crash-proof server that never disconnects — auto-recovers from any failure |

---

## AWS Services Used

| # | Service | Purpose | Why This Service |
|---|---------|---------|-----------------|
| 1 | **Amazon API Gateway (HTTP API)** | REST endpoint for inquiries & negotiation | Low-latency, pay-per-request, native Lambda integration |
| 2 | **AWS Lambda** (Python 3.12) | Serverless compute for each pipeline stage | Zero idle cost, auto-scales to demand, 15-min max execution |
| 3 | **AWS Step Functions** | Orchestrates the multi-step booking workflow | Visual workflow, built-in retry/error handling, wait states |
| 4 | **Amazon Bedrock** (Claude 3 Sonnet) | AI proposal generation and negotiation logic | Managed LLM, no infra to maintain, enterprise guardrails |
| 5 | **Amazon DynamoDB** | Bookings, negotiations, pricing, inventory, queue | Single-digit ms latency, conditional writes for concurrency |
| 6 | **Amazon S3** | Stores generated proposal documents (JSON) | Durable, versioned, lifecycle policies for cost management |
| 7 | **Amazon SES** | Sends proposal/confirmation emails | High deliverability, tracking, cost-effective bulk email |
| 8 | **Amazon SNS** | Alerts sales team when escalation is needed | Fan-out notifications, email/SMS/webhook |
| 9 | **Amazon CloudWatch** | Logging, metrics, scheduled triggers | Unified observability, daily reminder cron |
| 10 | **AWS IAM** | Least-privilege access control per Lambda | Security best practice, fine-grained resource permissions |
| 11 | **AWS X-Ray** | Distributed tracing across the pipeline | End-to-end latency visibility, bottleneck detection |
| 12 | **Terraform** | Infrastructure as Code | Reproducible, auditable, multi-environment deployments |

---

## Project Structure

```
GroupIQ/
├── lambdas/
│   ├── common/                      # Shared modules (all Lambdas)
│   │   ├── utils.py                # DynamoDB, S3, SES clients, booking status, inventory manager
│   │   ├── pricing_engine.py       # Dynamic pricing calculation (12 factors)
│   │   ├── calendar_data.py        # Seasonality, holidays, IPL, festivals, lead time tiers
│   │   ├── market_data.py          # Occupancy, competitor rates, Marriott BAR strategy
│   │   ├── customer_intelligence.py # Loyalty tiers, repeat guest, lifetime value, corporate
│   │   └── requirements.txt
│   ├── intake/                      # Validates inquiries, runs pricing engine, holds inventory
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── proposal_generator/          # Bedrock AI proposal creation with pricing breakdown
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── negotiation_agent/           # AI counter-offer evaluation + inventory reservation
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── notification/                # SES email + SNS escalation
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── reminder/                    # Checks upcoming events, sends reminders
│   │   ├── handler.py
│   │   └── requirements.txt
│   └── tipai_governance/            # TIP.AI compliance & risk scoring
│       ├── handler.py
│       └── requirements.txt
├── web/
│   ├── index.html                   # Admin Portal UI (all bookings, analytics, compliance)
│   ├── customer.html                # Customer Portal (login, inquiries, negotiate, communications)
│   └── server.py                    # Local API server (multi-threaded, SMTP email, auto-restart)
├── terraform/                       # Infrastructure as Code (AWS deployment)
│   ├── main.tf                     # Provider and backend config
│   ├── variables.tf                # Input variables
│   ├── outputs.tf                  # Stack outputs
│   ├── dynamodb.tf                 # DynamoDB tables
│   ├── s3.tf                       # S3 buckets
│   ├── iam.tf                      # IAM roles and policies
│   ├── lambda.tf                   # Lambda functions + CloudWatch Events
│   ├── api_gateway.tf              # HTTP API + routes + logging
│   └── step_functions.tf           # State machine + logging
├── step_functions/
│   └── workflow.asl.json            # State machine definition (ASL)
├── scripts/
│   ├── setup.sh                    # One-command setup after cloning (venv + deps + tests)
│   ├── start_web.sh                # Start web server with Gmail SMTP config
│   ├── build.sh                    # Package Lambdas for deployment
│   ├── deploy_local.sh             # Deploy to LocalStack (local development)
│   ├── seed_data.sh                # Load pricing rules into DynamoDB
│   └── test_local.sh              # Run local integration tests
├── data/
│   └── bookings_backup.json        # Persistent booking backup (auto-generated)
├── config/
│   └── sample_data.json            # Sample pricing rules & inquiry
├── tests/
│   ├── conftest.py                 # Test fixtures
│   ├── test_lambdas.py            # Lambda integration tests
│   └── test_utils.py              # Unit tests for utilities
├── .gitignore                       # Excludes .venv/, __pycache__/, .env
├── docker-compose.yml              # LocalStack container configuration
└── README.md
```

---

## Dynamic Pricing Engine

The pricing engine calculates optimal room rates using **12 market factors**:

```
Base Rate: $299/night

Event Date: September 15 (weekday, peak conference season)
├── Seasonality (September):         +12%
├── Weekday business demand:         +5%
├── Holiday/Event (IPL, Festival):   +18%
├── Lead time (4 months out):         0%
├── Simulated occupancy (65%):        0%
├── Competitor benchmark ($313):     +2%
├── Group size (75 rooms):           -5%
├── Length of stay (3 nights):       -4%
├── Local Demand (City events):     +30%
├── Marriott BAR Strategy:          +3%
├── Loyalty Concession (Silver):    -3%
└── Rate Cap (peak):                 cap

Combined Multiplier: ×1.66
Final Rate: $399/night (capped at peak rate)
```

| Factor | Range | Source File |
|--------|-------|-------------|
| Seasonality | -15% to +30% | `calendar_data.py` |
| Day of Week | -5% to +15% | `calendar_data.py` |
| Holidays/Events | 0% to +50% | `calendar_data.py` |
| Lead Time | -15% to +30% | `calendar_data.py` |
| Occupancy | -8% to +15% | `market_data.py` |
| Competitor Rates | -5% to +5% | `market_data.py` |
| Group Size | -12% to 0% | `market_data.py` |
| Length of Stay | -10% to 0% | `market_data.py` |
| Local Demand (IPL/City) | 0% to +50% | `calendar_data.py` |
| Marriott BAR Strategy | -3% to +8% | `market_data.py` |
| Loyalty Concession | -20% to 0% | `customer_intelligence.py` |
| Rate Cap (floor/peak) | Guardrails | `pricing_engine.py` |

---

## Concurrency Control

Prevents overbooking when multiple requests hit the same property/date simultaneously:

```
Request A (150 rooms) ──┐
                        ├──▶ DynamoDB Conditional Write ──▶ Only one succeeds
Request B (200 rooms) ──┘    (available_rooms >= requested)

Booking Flow:
1. Inquiry received → Rooms placed on HOLD (soft lock)
2. During negotiation → Holds prevent other bookings from over-allocating
3. On ACCEPT → Holds converted to RESERVATIONS (hard lock)
4. On REJECT/EXPIRE → Rooms released back to inventory
5. Conflict → HTTP 409 with clear error message
```

---

## Workflow — How It Works

### 1. Inquiry Intake + Dynamic Pricing
- Event planner submits a group booking request via `POST /inquiries`
- Lambda validates fields, runs **dynamic pricing engine** (8 factors)
- Checks room inventory — rejects if insufficient capacity
- Places rooms on hold, stores booking in DynamoDB
- TIP.AI governance compliance check
- Triggers Step Functions workflow

### 2. AI Proposal Generation
- Step Functions invokes the Proposal Generator Lambda
- Lambda calls **Amazon Bedrock (Claude 3 Sonnet)** with booking context + pricing breakdown
- AI generates a tiered proposal (Good/Better/Best) with room blocks, F&B options
- Includes pricing rationale explaining WHY the rate was calculated
- Proposal stored in S3, summary written to DynamoDB

### 3. Proposal Delivery
- Notification Lambda sends a branded HTML email via **SMTP/SES**
- Includes pricing tiers, F&B packages, terms, and 7-day expiry

### 4. AI Negotiation (Counter-Offers)
- Client responds with counter-offer via `POST /inquiries/{id}/negotiate`
- Negotiation Agent Lambda evaluates against thresholds:
  - **ACCEPT** (≥ $265/night) → atomically reserves rooms, generates GRP- booking ID
  - **COUNTER** ($180-$265) → AI generates counter-proposal with perks (breakfast + late checkout)
  - **ESCALATE** (< $180) → SNS alert to sales manager for personalized offer
  - **DECLINE** → Customer explicitly rejects, status updated system-wide
- If inventory insufficient at acceptance → auto-escalates
- Optimistic locking prevents duplicate acceptance (HTTP 409)
- Max 10 negotiation rounds before auto-escalation
- Full negotiation timeline tracked with visual history

### 5. Resolution
- Accepted → INQ- converts to GRP- booking ID, email confirmation + rooms permanently reserved
- Escalated → human sales manager picks up with full context
- Declined → status updated, customer can re-inquire anytime
- Expired → rooms released back to inventory

---

## Local Development Setup

### Prerequisites
- Docker Desktop (for LocalStack)
- Python 3.12+
- AWS CLI v2

### Quick Start (One Command)

```bash
# 1. Clone the repo
git clone https://github.com/suredevops/GroupIQ.git
cd GroupIQ

# 2. Run setup (creates venv, installs all dependencies, runs tests)
bash scripts/setup.sh

# 3. Start LocalStack
docker-compose up -d

# 4. Deploy all resources
bash scripts/deploy_local.sh

# 5. Start the web server
bash scripts/start_web.sh

# 6. Open portals
open http://localhost:5555              # Admin Portal
open http://localhost:5555/customer.html  # Customer Portal
```

### Enable Real Email Notifications (Gmail)

```bash
# Generate App Password: https://myaccount.google.com/apppasswords
SMTP_PASSWORD='your-16-char-app-password' bash scripts/start_web.sh
```

### Portals

| Portal | URL | Purpose |
|--------|-----|---------|
| **Admin Portal** | `http://localhost:5555` | View ALL bookings, analytics, compliance, inventory |
| **Customer Portal** | `http://localhost:5555/customer.html` | Customer login, submit inquiries, negotiate, track status |

### API Endpoints (Local)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/bookings` | List all bookings (admin) |
| `GET` | `/bookings/{id}` | Get booking details |
| `POST` | `/inquiries` | Submit new group booking inquiry |
| `POST` | `/inquiries/{id}/negotiate` | Submit counter-offer |
| `GET` | `/customer/bookings?email=X` | Get customer's bookings only |
| `POST` | `/customer/inquiries` | Customer portal inquiry submission |
| `POST` | `/customer/inquiries/{id}/negotiate` | Customer counter-offer |
| `GET` | `/inventory/{property_id}?date=YYYY-MM-DD` | Check room availability |
| `GET` | `/properties` | List all supported locations |
| `GET` | `/properties/{code}` | Nearby Marriott properties with maps |
| `GET` | `/bookings/report?period=week\|month\|year\|all` | Booking analytics |
| `GET` | `/reminders` | Check upcoming event reminders |
| `GET` | `/compliance/{id}` | TIP.AI compliance check |
| `GET` | `/compliance/rules` | View compliance rules |

### Test the Full Negotiation Flow

```bash
# Step 1: Create an inquiry (gets INQ- ID)
curl -s -X POST http://localhost:5555/customer/inquiries \
  -H 'Content-Type: application/json' \
  -d '{"contact_name":"Test User","contact_email":"test@demo.com","event_type":"corporate","event_date":"2026-11-10","check_in_date":"2026-11-10","check_out_date":"2026-11-13","num_rooms":25,"num_nights":3,"property_id":"MRIOTT-HYD-001"}' | python3 -m json.tool

# Step 2: Negotiate at $250 (COUNTER response)
curl -s -X POST http://localhost:5555/customer/inquiries/INQ-XXXXXXXX/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"proposed_room_rate": 250, "message": "Can you offer a better rate?"}' | python3 -m json.tool

# Step 3: Negotiate at $170 (ESCALATE response)
curl -s -X POST http://localhost:5555/customer/inquiries/INQ-XXXXXXXX/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"proposed_room_rate": 170, "message": "Please escalate to manager"}' | python3 -m json.tool

# Step 4: Accept at $265 (ACCEPT → GRP- booking ID created)
curl -s -X POST http://localhost:5555/customer/inquiries/INQ-XXXXXXXX/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"proposed_room_rate": 265, "message": "I accept"}' | python3 -m json.tool
```

---

## AI Model

| Component | Model | Service |
|-----------|-------|---------|
| Proposal Generation | Anthropic Claude 3 Sonnet | Amazon Bedrock |
| Negotiation Agent | Anthropic Claude 3 Sonnet | Amazon Bedrock |
| Model ID | `anthropic.claude-3-sonnet-20240229-v1:0` | — |

The AI is used for:
- Generating multi-tier proposals with personalized recommendations
- Evaluating counter-offers against revenue bounds
- Creating transparent pricing rationale for customers

---

## DynamoDB Tables

| Table | Partition Key | Sort Key | Purpose |
|-------|--------------|----------|---------|
| `groupiq-bookings` | `booking_id` | `version` | All booking records |
| `groupiq-negotiations` | `booking_id` | `turn_number` | Negotiation history |
| `groupiq-pricing-rules` | `property_id` | `rule_type` | Base rates, discounts, bounds |
| `groupiq-inventory` | `property_id` | `date` | Room availability (atomic counters) |
| `groupiq-booking-queue` | `booking_id` | — | Priority queue for large bookings |

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Dynamic pricing over fixed rates | Maximizes RevPAR, responds to market conditions automatically |
| 12-factor pricing model | Captures demand signals (IPL, festivals, loyalty) that 8-factor model missed |
| INQ- → GRP- ID lifecycle | Clear distinction between inquiry and confirmed booking for tracking |
| Separate customer & admin portals | Customers see only their data; admins see everything — role-based access |
| DynamoDB conditional writes for inventory | Prevents overbooking under concurrent load without distributed locks |
| Bedrock over SageMaker | Managed, no model hosting infra, instant scaling |
| Step Functions over SQS chains | Visual workflow, built-in wait/retry, easier debugging |
| DynamoDB over RDS | Serverless, pay-per-request matches bursty booking patterns |
| Multi-threaded server with auto-restart | Never disconnects during demos/production — crash-proof |
| Mock Bedrock fallback | Allows full local testing without AWS credentials |
| Persistent JSON backup | Survives LocalStack restarts, enables offline analytics |
| Tiered pricing (Good/Better/Best) | Anchoring effect — clients often pick the middle tier |
| Max 10 negotiation rounds | Prevents infinite loops, forces resolution |
| Floor/Peak rate guardrails | Protects revenue floor, prevents price gouging |
| Email-based customer login | Simple demo auth without password complexity — sessionStorage based |
| Auto-refresh admin (15s) | Real-time visibility of customer actions without manual refresh |
| Gmail SMTP for local | Real email delivery without AWS SES setup for demos |

---

## Revenue Impact

- **Speed**: Inquiry → Proposal in < 60 seconds (vs 2-3 days manual)
- **RevPAR Protection**: Dynamic pricing ensures optimal rate; never below floor
- **Conversion**: Faster proposals + transparent pricing = higher win rate
- **Scale**: Handles unlimited concurrent negotiations with zero additional headcount
- **Overbooking Prevention**: Atomic inventory management eliminates double-booking risk
- **EBITDA**: Estimated 15-20% increase in group booking conversion at maintained ADR

---

## Cost Estimate (Monthly, 500 inquiries/month)

| Service | Est. Cost |
|---------|-----------|
| Lambda (6 functions × ~500 invocations) | ~$8 |
| Bedrock (Claude Sonnet, ~2000 calls) | ~$60 |
| DynamoDB (5 tables, on-demand) | ~$15 |
| Step Functions (500 executions) | ~$2 |
| S3 (proposals storage) | ~$1 |
| SES (500 emails) | ~$0.50 |
| API Gateway (2000 requests) | ~$2 |
| CloudWatch (logs + events) | ~$5 |
| **Total** | **~$94/month** |

Compared to a single group sales manager's salary (~$8K/month), this is **99% cost reduction** for the automation layer.

---

## Supported Properties

| Country | Cities | Properties |
|---------|--------|-----------|
| India | Hyderabad, Bengaluru, Mumbai, Delhi/NCR, Chennai, Goa, Jaipur, Pune, Kolkata | 38 |
| USA | New York, Los Angeles, Chicago, Miami | 12 |

Each property includes: precise address, GPS coordinates, Google Maps link, room type breakdown (Luxury/Premium/Standard).

---

## Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Frontend (Admin) | HTML5, CSS3, JavaScript — real-time dashboard with auto-refresh |
| Frontend (Customer) | HTML5, CSS3, JavaScript — email login, negotiation timeline |
| Backend (Local) | Python HTTP Server (multi-threaded, SMTP email, auto-restart) |
| Backend (AWS) | API Gateway + Lambda + Step Functions |
| Database | Amazon DynamoDB (5 tables, conditional writes) |
| AI/ML | Amazon Bedrock (Claude 3 Sonnet) |
| Email | Gmail SMTP (local) / Amazon SES (production) |
| Infrastructure | Terraform, Docker, LocalStack |
| Pricing | Custom engine (Python) — 12 market factors |
| Customer Intelligence | Marriott Bonvoy loyalty, CRM, lifetime value scoring |
| Monitoring | Amazon CloudWatch, AWS X-Ray |
| Testing | pytest + moto (AWS mocking) — 18 automated tests |
