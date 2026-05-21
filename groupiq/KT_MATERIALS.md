# GroupIQ — KT Guide (Simple Version)

---

## WHAT IS THIS PROJECT?

**One-liner**: An AI bot that handles group hotel bookings automatically for Marriott.

**The Problem**: When someone wants to book 50+ hotel rooms for a conference or wedding, it takes 3-5 days of emails back and forth with the hotel sales team to agree on pricing.

**The Solution**: GroupIQ does this in minutes:
1. Customer submits a booking request
2. AI generates a pricing proposal (Good/Better/Best options)
3. Customer can negotiate the price
4. AI decides: Accept the deal, Counter-offer, or Escalate to a human

---

## HOW DOES IT WORK? (Simple Flow)

```
Customer sends request
        ↓
[INTAKE] Checks if request is valid (min 10 rooms, all fields filled)
        ↓
[PROPOSAL] AI creates 3 pricing tiers and emails them
        ↓
Customer replies with counter-offer
        ↓
[NEGOTIATE] AI checks: Is this within our discount limit?
        ↓
   ┌────┼────┐
   ↓    ↓    ↓
ACCEPT  COUNTER  ESCALATE
(done)  (meet    (human
         halfway) takes over)
```

---

## THE 4 MAIN FUNCTIONS (Lambdas)

### 1. INTAKE — The Front Door
- **File**: `lambdas/intake/handler.py`
- **What it does**: Receives booking requests, checks they're valid, saves to database
- **Key rules**: Must have 7 fields, minimum 10 rooms
- **Outputs**: Booking ID like `GRP-20260518-A3F7B2C1`

### 2. PROPOSAL GENERATOR — The AI Writer
- **File**: `lambdas/proposal_generator/handler.py`
- **What it does**: Asks AI (Claude) to create a custom pricing proposal
- **Output**: Good/Better/Best tiers with rates, F&B packages, total price
- **Stored in**: S3 bucket as JSON file

### 3. NEGOTIATION AGENT — The AI Negotiator
- **File**: `lambdas/negotiation_agent/handler.py`
- **What it does**: Customer says "I want $265/night", AI decides if that's OK
- **The rules**:
  - Base rate: $299/night
  - Maximum discount: 15% (floor = $254/night)
  - If proposed rate ≥ $254 → ACCEPT
  - If proposed rate is close but below → COUNTER (offer a middle ground + perks)
  - If proposed rate is way too low → ESCALATE (send to human)
  - Maximum 5 rounds of back-and-forth

### 4. NOTIFICATION — The Messenger
- **File**: `lambdas/notification/handler.py`
- **What it does**: Sends branded HTML emails with the proposal, or escalation alerts via SNS

---

## THE DATABASE (3 Tables)

| Table | Stores What | Keys |
|-------|------------|------|
| **bookings** | All booking requests + their current status | booking_id + version |
| **negotiations** | Every round of back-and-forth negotiation | booking_id + turn_number |
| **pricing-rules** | Hotel rates, discounts, F&B prices | property_id + rule_type |

---

## HOW TO RUN IT LOCALLY (5 Steps)

```bash
# Step 1: Start fake AWS (Docker must be running)
cd /Users/bgudi536/project/babu/groupiq
docker-compose up -d

# Step 2: Activate Python environment
source .venv/bin/activate

# Step 3: Deploy everything locally
bash scripts/deploy_local.sh

# Step 4: Start the web server (keep this running)
python3 web/server.py

# Step 5: Open NEW terminal and test
curl -s -X POST http://localhost:5555/inquiries \
  -H 'Content-Type: application/json' \
  -d '{"contact_name":"Sarah Johnson","contact_email":"sarah@test.com","event_type":"conference","event_date":"2026-09-15","num_rooms":75,"num_nights":3,"property_id":"MRIOTT-NYC-001"}' | python3 -m json.tool
```

---

## DEMO COMMANDS (Copy-Paste Ready)

### Create a Booking
```bash
curl -s -X POST http://localhost:5555/inquiries \
  -H 'Content-Type: application/json' \
  -d '{"contact_name":"Krishna","contact_email":"krishna@test.com","event_type":"wedding","event_date":"2026-12-20","num_rooms":100,"num_nights":4,"property_id":"MRIOTT-NYC-001"}' | python3 -m json.tool
```

### Negotiate — AI Accepts (11% discount, within limit)
```bash
curl -s -X POST http://localhost:5555/inquiries/BOOKING_ID_HERE/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"proposed_rate":265,"message":"Can we get a better rate?"}' | python3 -m json.tool
```

### Negotiate — AI Counters (20% discount, too much)
```bash
curl -s -X POST http://localhost:5555/inquiries/BOOKING_ID_HERE/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"proposed_rate":240,"message":"Budget is tight"}' | python3 -m json.tool
```

### Negotiate — AI Escalates (40% discount, way too much)
```bash
curl -s -X POST http://localhost:5555/inquiries/BOOKING_ID_HERE/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"proposed_rate":180,"message":"Maximum $180 per night"}' | python3 -m json.tool
```

### See All Bookings
```bash
curl -s http://localhost:5555/bookings | python3 -m json.tool
```

### Validation Error (too few rooms)
```bash
curl -s -X POST http://localhost:5555/inquiries \
  -H 'Content-Type: application/json' \
  -d '{"contact_name":"Test","contact_email":"t@t.com","event_type":"wedding","event_date":"2026-12-01","num_rooms":5,"num_nights":2,"property_id":"MRIOTT-NYC-001"}' | python3 -m json.tool
```

### Run All Tests
```bash
cd /Users/bgudi536/project/babu/groupiq
source .venv/bin/activate
pytest tests/ -v
```

---

## FILE STRUCTURE (What's Where)

```
groupiq/
├── lambdas/                    ← THE CODE
│   ├── common/utils.py        ← Shared helpers (ID generator, response builder)
│   ├── intake/handler.py      ← Validates & saves bookings
│   ├── proposal_generator/    ← AI creates proposals
│   ├── negotiation_agent/     ← AI handles counter-offers
│   └── notification/          ← Sends emails
│
├── terraform/                  ← INFRASTRUCTURE (AWS setup)
│   ├── dynamodb.tf            ← 3 database tables
│   ├── lambda.tf              ← 4 Lambda functions
│   ├── s3.tf                  ← Proposal storage bucket
│   ├── api_gateway.tf         ← API endpoints
│   ├── step_functions.tf      ← Workflow orchestration
│   └── iam.tf                 ← Permissions (who can do what)
│
├── step_functions/
│   └── workflow.asl.json       ← The full workflow definition
│
├── tests/                      ← TESTS
│   ├── conftest.py            ← Test setup (fake AWS)
│   ├── test_lambdas.py        ← Lambda handler tests (9 tests)
│   └── test_utils.py          ← Utility function tests (7 tests)
│
├── scripts/                    ← AUTOMATION
│   ├── build.sh               ← Package Lambdas into .zip
│   ├── deploy_local.sh        ← Deploy to LocalStack
│   ├── seed_data.sh           ← Load pricing data
│   └── test_local.sh          ← Quick validation tests
│
├── web/
│   └── server.py              ← Local dev server (port 5555)
│
├── config/
│   └── sample_data.json       ← Sample pricing rules
│
└── docker-compose.yml          ← LocalStack container setup
```

---

## PRICING RULES (How Discounts Work)

```
Base Room Rate:     $299/night
Floor Rate:         $254/night (15% max discount - AI never goes below this)
Peak Rate:          $399/night (busy season)

Breakfast:          $45/person
Lunch:              $65/person
Dinner:             $95/person

Max Room Discount:  15%
Max F&B Discount:   10%
Auto-Escalate:      If deal > $100K, human reviews
```

### The Negotiation Math:
- Customer proposes $265 → Discount = (299-265)/299 = 11.4% → ACCEPT (under 15%)
- Customer proposes $240 → Discount = 19.7% → COUNTER (over 15%, but close)
- Customer proposes $180 → Discount = 39.8% → ESCALATE (way over limit)

---

## BOOKING STATUS LIFECYCLE

```
INQUIRY_RECEIVED  →  Customer submitted the request
PROPOSAL_GENERATED  →  AI created the pricing proposal
PROPOSAL_SENT  →  Email sent to customer
NEGOTIATING  →  Back-and-forth counter-offers happening
ACCEPTED  →  Deal closed!
ESCALATED  →  Human sales manager took over
EXPIRED  →  Customer didn't respond in 7 days
DECLINED  →  Customer said no
```

---

## KT PRESENTATION ORDER (How to Present)

| # | Topic | Time | What to Show |
|---|-------|------|-------------|
| 1 | What is GroupIQ? | 3 min | Explain the problem + solution |
| 2 | Architecture | 5 min | Draw the flow diagram on whiteboard |
| 3 | Live Demo - Setup | 2 min | `docker-compose up` + `deploy_local.sh` |
| 4 | Live Demo - Create Booking | 3 min | curl to create, show response |
| 5 | Live Demo - Validation | 2 min | Show error handling |
| 6 | Live Demo - Negotiate (all 3) | 5 min | ACCEPT, COUNTER, ESCALATE |
| 7 | Run Tests | 2 min | `pytest tests/ -v` |
| 8 | Code Walkthrough | 10 min | Walk through 4 handler files |
| 9 | Infrastructure | 5 min | Terraform files overview |
| 10 | Design Decisions | 5 min | Why Bedrock, DynamoDB, Step Functions |
| 11 | Q&A | 5-10 min | Answer questions |

**Total: ~45-50 minutes**

---

## COMMON ISSUES & FIXES

| Problem | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: boto3` | venv not activated | `source .venv/bin/activate` |
| `no such file or directory: API_ID` | Using `<API_ID>` literally | Use `localhost:5555` instead |
| `Bedrock InternalFailure` | Bedrock is paid in LocalStack | Already fixed with mock fallback |
| `Failed to connect to localhost:5555` | Web server not running | Start `python3 web/server.py` first |
| Tests not found | Wrong directory | Run from `groupiq/` folder |

---

## KEY TAKEAWAYS FOR THE TEAM

1. **Everything is serverless** — No servers to manage, scales automatically
2. **AI has guardrails** — Never goes below floor rate, max 5 rounds, auto-escalate
3. **Fully tested offline** — moto mocks AWS, no real AWS needed for testing
4. **Local development is easy** — Docker + LocalStack = entire AWS on your laptop
5. **Versioned bookings** — Every change creates a new version (full audit trail)
6. **Pricing is configurable** — Change rates in DynamoDB, no code changes needed
