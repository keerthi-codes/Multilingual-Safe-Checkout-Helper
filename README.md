# Multilingual Safe-Checkout Helper

An AI-powered multilingual shopping assistant that helps users compare products, understand checkout terms across languages (English, Spanish, French), establish spending limits, and execute purchases through PayPal Sandbox.

## Core Security & Architecture Principles

- **Deterministic Rule Enforcement**: All spending constraints, price caps, and transaction rules are strictly enforced by deterministic backend logic.
- **Accurate Money Representation**: Prices and limits are represented with `Decimal` (never binary floating-point) to prevent floating-point inaccuracy.
- **Strict Separation of AI Recommendations vs. Policy**: The AI assistant is an advisory service. It cannot authorize purchases, invent prices, or bypass policy checks.
- **Cryptographic One-Time Approval Secret**: Proposals require an unguessable one-time secret (`approval_secret`) for explicit user confirmation. Replays and unauthenticated requests are blocked.
- **Authoritative Data Integrity**: The client and LLM cannot supply authoritative prices, categories, or totals. All evaluations resolve against backend catalogue records.

---

## Prerequisites

- **Python 3.10+** (Python 3.13 supported)
- **Git**
- **Windows PowerShell** (or compatible shell)

---

## Setup Instructions

### 1. Clone & Enter Project Directory
```powershell
cd "Multilingual Safe-Checkout Helper"
```

### 2. Create and Activate Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```powershell
pip install -r requirements.txt
```

## Configuration Options

Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```

### 1. AI Provider Configuration
- **Mock Mode (Default)**: Runs completely offline without external network calls:
  ```env
  AI_PROVIDER=mock
  ```
- **Gemini Live Mode (Optional)**:
  ```env
  AI_PROVIDER=gemini
  GEMINI_API_KEY="your-gemini-api-key"
  GEMINI_MODEL="gemini-3.6-flash"
  ```

### 2. PayPal Sandbox Payment Configuration
- **Mock Payment Mode (Default)**: Runs completely offline without PayPal credentials or network requests:
  ```env
  PAYPAL_MODE=mock
  ```
- **PayPal Sandbox Mode**: Connects to the official PayPal Sandbox REST API:
  ```env
  PAYPAL_MODE=sandbox
  PAYPAL_CLIENT_ID="your-paypal-sandbox-client-id"
  PAYPAL_CLIENT_SECRET="your-paypal-sandbox-client-secret"
  PAYPAL_SANDBOX_BASE_URL="https://api-m.sandbox.paypal.com"
  ```
- **Demo Currency Bridge**:
  ```env
  PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD=0.012
  ```
  *(Authoritative catalogue prices and mandates remain strictly in INR. Converted USD amounts are for PayPal Sandbox checkout demonstration only.)*

---

## Running Tests

Run the complete test suite (70 unit, integration, and security tests) using `pytest`:
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

All 70 tests run deterministically and offline without requiring external API keys or live PayPal credentials.

---

## Running the Application

Start the FastAPI application using Uvicorn:
```powershell
.\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Application URLs & Verification

Once running, access the services:
- **Web Frontend**: `http://127.0.0.1:8000/` (Browse catalogue, setup mandates, chat with AI, and complete PayPal Sandbox checkout)
- **Health Check**: `http://127.0.0.1:8000/health`
- **Interactive API Docs (Swagger UI)**: `http://127.0.0.1:8000/docs`

---

## API Endpoints

### 1. Health Endpoint
- `GET /health`: Operational health check.

### 2. Catalogue Endpoints
- `GET /api/v1/products`: List products with optional `category`, `available`, and `search` filters.
- `GET /api/v1/products/categories`: Return unique sorted category names.
- `GET /api/v1/products/{product_id}`: Retrieve a single product by ID (HTTP 404 if not found).

### 3. Spending Mandate & Policy Endpoints
- `POST /api/v1/mandates`: Create a spending mandate with maximum amount, currency (`INR`), permitted categories, max quantity, and expiry.
- `GET /api/v1/mandates/{mandate_id}`: Retrieve a spending mandate and its latest lifecycle status.
- `POST /api/v1/mandates/{mandate_id}/evaluate`: Deterministically evaluate a proposed purchase against mandate rules.
- `POST /api/v1/mandates/evaluate`: Alternative evaluation endpoint with `mandate_id` in request body.
- `GET /api/v1/mandates/proposals/{proposal_id}`: Retrieve evaluated purchase proposal.
- `POST /api/v1/mandates/proposals/{proposal_id}/approve`: Explicitly user-approve proposal using the cryptographic `approval_secret`. Transitions proposal to `USER_APPROVED` and reserves mandate (`RESERVED`).
- `POST /api/v1/mandates/{mandate_id}/revoke`: Revoke an active mandate.

### 4. AI Assistant Endpoints
- `POST /api/v1/ai/chat`: Multilingual shopping assistant query supporting English (`en`), Spanish (`es`), and French (`fr`).

### 5. PayPal Sandbox Payment Endpoints
- `POST /api/v1/payments/orders/create`: Create a PayPal order for an approved proposal. Converts authoritative INR total to USD via backend Decimal calculation and returns approval URL and currency breakdown.
- `POST /api/v1/payments/orders/{order_id}/capture`: Reconcile and capture PayPal order. Verifies provider state and capture amount; finalizes mandate as `CONSUMED` and proposal as `PAID`.
- `POST /api/v1/payments/orders/{order_id}/cancel`: Cancel checkout and safely release mandate reservation back to `ACTIVE`.
- `GET /api/v1/payments/orders/{order_id}`: Retrieve payment order record and verified capture status.

---

## Mandate & Payment Lifecycle

```
[Proposal Evaluation] 
       │
       ▼
 [ALLOW_PENDING_APPROVAL] + One-Time Secret
       │
       ▼ (POST /proposals/{id}/approve with secret)
 [Proposal: USER_APPROVED] ────► [Mandate: RESERVED (HELD)]
       │
       ▼ (POST /payments/orders/create)
 [PayPal Order: CREATED] (USD amount derived server-side)
       │
       ├───► (POST /payments/orders/{id}/cancel)
       │       │
       │       ▼
       │     [Order: CANCELLED] ──► [Mandate: ACTIVE (Released)]
       │
       ▼ (POST /payments/orders/{id}/capture)
 [Order: COMPLETED (Verified)]
       │
       ├──► [Proposal: PAID]
       └──► [Mandate: CONSUMED]
```

---

## Security & Concurrency Limitations of Prototype

- **In-Memory Storage**: Mandates, proposals, secrets, and payment records are stored in memory and reset upon server restart. Cross-process concurrency is not database-backed.
- **Webhook Signature Verification**: Webhooks are not implemented in this prototype; payment status is verified synchronously via backend reconciliation against the Orders v2 REST API.
- **Prototype Bearer Secret**: The `approval_secret` guarantees one-time authorization for a specific proposal, but full user authentication (OAuth/JWT) is left for production systems.

---

## Project Structure

```text
multilingual-safe-checkout-helper/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app, static mounts, and route definitions
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py               # Environment settings (AI, PayPal, exchange rate)
│   ├── catalogue/
│   │   ├── __init__.py             # Catalogue package exports
│   │   ├── models.py               # Pydantic Product entity with Decimal money validation
│   │   ├── repository.py           # In-memory repository with 10 seeded demo products
│   │   ├── service.py              # Filtering, query, and search business logic
│   │   └── router.py               # Read-only REST API endpoints
│   ├── policy/
│   │   ├── __init__.py             # Policy package exports
│   │   ├── models.py               # Mandate (ACTIVE, RESERVED, CONSUMED, REVOKED) and proposal models
│   │   ├── repository.py           # In-memory mandate and proposal store
│   │   ├── service.py              # Deterministic evaluation, reservation, and consumption
│   │   └── router.py               # Mandate & proposal API endpoints
│   ├── ai/
│   │   ├── __init__.py             # AI package exports
│   │   ├── models.py               # Chat requests, responses, and proposal intents
│   │   ├── prompts.py              # Multilingual system prompts and injection guardrails
│   │   ├── tools.py                # Read-only catalogue inspection tool manager
│   │   ├── service.py              # AIService with Mock and Gemini providers
│   │   └── router.py               # POST /api/v1/ai/chat endpoint
│   └── payment/
│       ├── __init__.py             # Payment package exports
│       ├── models.py               # Payment schemas, currency breakdown, order records
│       ├── currency.py             # Server-side Decimal INR-to-USD conversion
│       ├── repository.py           # In-memory payment order repository
│       ├── service.py              # BasePaymentProvider, Mock & PayPalSandbox providers, PaymentService
│       └── router.py               # Payment order create, capture, cancel, status endpoints
├── tests/
│   ├── test_health.py              # Health check endpoint test (1 test)
│   ├── test_catalogue.py           # Catalogue unit & integration tests (15 tests)
│   ├── test_policy.py              # Policy engine & approval security tests (23 tests)
│   ├── test_ai.py                  # AI assistant, tools, and multilingual tests (14 tests)
│   └── test_payment.py             # PayPal Sandbox, Mock, conversion & lifecycle tests (14 tests)
├── frontend/
│   ├── index.html                  # Catalogue browser, AI chat, and mandate display
│   ├── css/
│   │   └── style.css               # Custom styling
│   └── js/
│       └── main.js                 # Safe DOM rendering, AI chat, approval, and PayPal Sandbox checkout
├── data/
│   └── .gitkeep                    # SQLite data directory placeholder
├── .env.example                    # Template for configuration
├── .gitignore                      # Git ignore rules (.venv, .env, caches)
├── pytest.ini                      # Pytest configuration with pythonpath set
├── requirements.txt                # Pinned core dependencies
└── README.md                       # Project documentation
```
