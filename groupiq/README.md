# GroupIQ — AI Group Booking Negotiation Agent

An end-to-end serverless AI agent that automates group booking negotiations for hospitality (Marriott). It accepts group inquiries, generates customized proposals with dynamic pricing, and negotiates counter-offers autonomously within pre-set revenue bounds — eliminating days of manual back-and-forth.

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
       │  Email (SES)               │         │                          │          │        │
       │                            │         ▼                          ▼          ▼        │
       │                            │  ┌─────────────┐  ┌──────────────────┐ ┌───────────┐  │
       │                            │  │   Lambda:   │  │    Lambda:       │ │  Lambda:  │  │
       │                            │  │   Intake    │  │ Proposal Gen     │ │Negotiation│  │
       │                            │  │             │  │  (Bedrock AI)    │ │  Agent    │  │
       │                            │  └──────┬──────┘  └────────┬─────────┘ └─────┬─────┘  │
       │                            │         │                  │                 │        │
       │                            │         ▼                  ▼                 ▼        │
       │                            │  ┌─────────────────────────────────────────────────┐  │
       │                            │  │              Amazon DynamoDB                     │  │
       │                            │  │  (Bookings / Negotiations / Pricing Rules)       │  │
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

## AWS Services Used

| # | Service | Purpose | Why This Service |
|---|---------|---------|-----------------|
| 1 | **Amazon API Gateway (HTTP API)** | REST endpoint for inquiries & negotiation | Low-latency, pay-per-request, native Lambda integration |
| 2 | **AWS Lambda** (Python 3.12) | Serverless compute for each pipeline stage | Zero idle cost, auto-scales to demand, 15-min max execution |
| 3 | **AWS Step Functions** | Orchestrates the multi-step booking workflow | Visual workflow, built-in retry/error handling, wait states for async |
| 4 | **Amazon Bedrock** (Claude 3 Sonnet) | AI proposal generation and negotiation logic | Managed LLM, no infra to maintain, enterprise-grade guardrails |
| 5 | **Amazon DynamoDB** | Stores bookings, negotiation history, pricing rules | Single-digit ms latency, serverless, auto-scaling |
| 6 | **Amazon S3** | Stores generated proposal documents (JSON/PDF) | Durable, versioned, lifecycle policies for cost management |
| 7 | **Amazon SES** | Sends proposal emails to event planners | High deliverability, tracking, cost-effective bulk email |
| 8 | **Amazon SNS** | Alerts sales team when escalation is needed | Fan-out notifications, email/SMS/webhook |
| 9 | **Amazon CloudWatch** | Logging, metrics, dashboards, alarms | Unified observability, custom metrics for RevPAR tracking |
| 10 | **AWS IAM** | Least-privilege access control per Lambda | Security best practice, fine-grained resource permissions |
| 11 | **AWS X-Ray** | Distributed tracing across the pipeline | End-to-end latency visibility, bottleneck detection |
| 12 | **Terraform** | Infrastructure as Code | Reproducible, auditable, multi-environment deployments |

---

## Project Structure

```
groupiq/
├── terraform/                    # Infrastructure as Code
│   ├── main.tf                  # Provider and backend config
│   ├── variables.tf             # Input variables
│   ├── outputs.tf               # Stack outputs
│   ├── dynamodb.tf              # DynamoDB tables
│   ├── s3.tf                    # S3 buckets
│   ├── iam.tf                   # IAM roles and policies
│   ├── lambda.tf                # Lambda functions + SNS
│   ├── api_gateway.tf           # HTTP API + routes
│   ├── step_functions.tf        # State machine
│   └── terraform.tfvars.example # Example variable values
├── lambdas/
│   ├── common/                  # Shared layer (utils, models)
│   │   ├── utils.py
│   │   └── requirements.txt
│   ├── intake/                  # Validates & stores inquiries
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── proposal_generator/      # Bedrock AI proposal creation
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── negotiation_agent/       # AI counter-offer evaluation
│   │   ├── handler.py
│   │   └── requirements.txt
│   └── notification/            # SES email + SNS escalation
│       ├── handler.py
│       └── requirements.txt
├── step_functions/
│   └── workflow.asl.json        # State machine definition (ASL)
├── config/
│   └── sample_data.json         # Sample pricing rules & inquiry
├── scripts/
│   ├── build.sh                 # Package Lambdas for deployment
│   └── seed_data.sh             # Load pricing rules into DynamoDB
└── README.md
```

---

## Workflow — How It Works

### 1. Inquiry Intake
- Event planner submits a group booking request via `POST /inquiries`
- Lambda validates fields, enriches with property pricing data
- Stores in DynamoDB, triggers Step Functions workflow

### 2. AI Proposal Generation
- Step Functions invokes the Proposal Generator Lambda
- Lambda calls **Amazon Bedrock (Claude 3 Sonnet)** with booking context + pricing rules
- AI generates a tiered proposal (Good/Better/Best) with room blocks, F&B options, meeting space
- Proposal stored in S3, summary written to DynamoDB

### 3. Proposal Delivery
- Notification Lambda sends a branded HTML email via **Amazon SES**
- Includes pricing tiers, F&B packages, terms, and 7-day expiry

### 4. AI Negotiation (Counter-Offers)
- Client responds with counter-offer via `POST /inquiries/{id}/negotiate`
- Negotiation Agent Lambda evaluates against bounds:
  - **ACCEPT** — within max discount threshold
  - **COUNTER** — room to compromise, AI generates counter-proposal
  - **ESCALATE** — exceeds bounds, SNS alert to sales manager
- Max 5 negotiation rounds before auto-escalation

### 5. Resolution
- Accepted → triggers downstream PMS integration
- Escalated → human sales manager picks up with full context
- Expired → TTL cleanup

---

## Deployment

### Prerequisites
- AWS CLI configured with appropriate credentials
- Terraform >= 1.5.0
- Python 3.12
- Amazon Bedrock Claude model access enabled in your AWS account

### Steps

```bash
# 1. Package Lambda functions
cd groupiq
./scripts/build.sh

# 2. Configure variables
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# 3. Initialize and deploy
terraform init
terraform plan
terraform apply

# 4. Seed pricing rules
cd ..
./scripts/seed_data.sh
```

### Test the API

```bash
# Submit a group booking inquiry
curl -X POST https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/inquiries \
  -H "Content-Type: application/json" \
  -d '{
    "contact_name": "Sarah Johnson",
    "contact_email": "sarah@techcorp.com",
    "company_name": "TechCorp Inc.",
    "event_type": "conference",
    "event_date": "2026-09-15",
    "num_rooms": 75,
    "num_nights": 3,
    "property_id": "MRIOTT-NYC-001",
    "fnb_required": true,
    "meeting_space_required": true,
    "special_requests": "Keynote ballroom for 500, 4 breakout rooms",
    "budget_indication": "$150,000 - $200,000"
  }'

# Submit a counter-offer
curl -X POST https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/inquiries/GRP-20260917-A1B2C3D4/negotiate \
  -H "Content-Type: application/json" \
  -d '{
    "counter_offer": {
      "requested_price": 160000,
      "requested_room_rate": 240,
      "additional_requests": "Include AV package at no charge",
      "message": "Our budget is firm at $160K. Can you include AV?"
    }
  }'
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Bedrock over SageMaker | Managed, no model hosting infra, instant scaling |
| Step Functions over SQS chains | Visual workflow, built-in wait/retry, easier debugging |
| DynamoDB over RDS | Serverless, pay-per-request matches bursty booking patterns |
| Python 3.12 for Lambdas | Fastest cold start, rich Bedrock SDK support |
| Tiered pricing (Good/Better/Best) | Anchoring effect — clients often pick the middle tier |
| Max 5 negotiation rounds | Prevents infinite loops, forces resolution |
| SNS for escalation | Decoupled alerting, supports email + SMS + Slack webhook |

---

## Revenue Impact

- **Speed**: Inquiry → Proposal in < 60 seconds (vs 2-3 days manual)
- **RevPAR Protection**: AI never goes below floor rate; optimizes ancillary revenue
- **Conversion**: Faster proposals = higher win rate on competitive group RFPs
- **Scale**: Handles unlimited concurrent negotiations with zero additional headcount
- **EBITDA**: Estimated 15-20% increase in group booking conversion at maintained ADR

---

## Cost Estimate (Monthly, 500 inquiries/month)

| Service | Est. Cost |
|---------|-----------|
| Lambda (4 functions × ~500 invocations) | ~$5 |
| Bedrock (Claude Sonnet, ~2000 calls) | ~$60 |
| DynamoDB (on-demand) | ~$10 |
| Step Functions (500 executions) | ~$2 |
| S3 (proposals storage) | ~$1 |
| SES (500 emails) | ~$0.50 |
| API Gateway (1000 requests) | ~$1 |
| **Total** | **~$80/month** |

Compared to a single group sales manager's salary (~$8K/month), this is 99% cost reduction for the automation layer.
