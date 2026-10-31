# Multilingual Safe-Checkout Helper

An AI-powered multilingual shopping assistant that helps users understand checkout terms, compare products across languages (English, Spanish, French), enforce deterministic spending mandates, and execute PayPal Sandbox/Mock checkouts with durable SQLite persistence.

> **Hackathon & Evaluation Notice:** This application operates in **Mock Mode** by default (`AI_PROVIDER=mock`, `PAYPAL_MODE=mock`), running 100% offline with zero external network dependencies or API keys required. All 125 automated tests run offline. The INR-to-USD currency conversion is an authoritative, server-side demonstration bridge. This project is a hackathon prototype and is **not production-ready**.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Proposed Solution](#2-proposed-solution)
3. [Key Features](#3-key-features)
4. [Supported Languages](#4-supported-languages)
5. [Technology Stack](#5-technology-stack)
6. [System Architecture & Component Responsibilities](#6-system-architecture--component-responsibilities)
7. [How the AI Assistant Works](#7-how-the-ai-assistant-works)
8. [How Deterministic Spending Mandates Work](#8-how-deterministic-spending-mandates-work)
9. [PayPal Payment Lifecycle](#9-paypal-payment-lifecycle)
10. [INR Catalogue Pricing & USD PayPal Demo Conversion](#10-inr-catalogue-pricing--usd-paypal-demo-conversion)
11. [Security Principles & Limitations](#11-security-principles--limitations)
12. [Project Structure](#12-project-structure)
13. [Installation & Configuration](#13-installation--configuration)
14. [Running the Application](#14-running-the-application)
15. [Running Automated Tests](#15-running-automated-tests)
16. [API Endpoint Reference](#16-api-endpoint-reference)
17. [Demonstration Instructions](#17-demonstration-instructions)
18. [Current Limitations & Future Improvements](#18-current-limitations--future-improvements)

---

## 1. Problem Statement

Cross-border e-commerce presents significant challenges for non-English speakers and budget-conscious consumers:
- **Language Barriers:** International online stores typically present checkout policies, terms of service, warranty rules, and return disclosures exclusively in English, leading to accidental subscriptions or buyer remorse.
- **Runaway Spending & Uncontrolled AI:** Emerging autonomous AI shopping agents often have direct payment authority, creating severe financial risks if the agent hallucinates prices or authorizes purchases without human oversight.
- **Currency Discrepancies:** Shoppers frequently see domestic prices (e.g. INR) but are charged in foreign payment currencies (e.g. USD) with opaque exchange margins.

---

## 2. Proposed Solution

The **Multilingual Safe-Checkout Helper** introduces a secure **advisory AI assistant** paired with a **hard deterministic policy engine**:
- **Bilingual & Multilingual Advisory:** Users converse in their native language (English, Spanish, or French) to search and understand catalogue products.
- **Zero-Authority AI Architecture:** The AI assistant is strictly advisory. It cannot alter database state, change product prices, approve proposals, or initiate payments.
- **Deterministic Spending Mandates:** Users set explicit spending limits, allowed categories, and expiry times before shopping. Every purchase proposal is deterministically evaluated against backend rules.
- **Explicit Human Approval:** Purchases require a one-time cryptographic secret entered by the human buyer.
- **Durable PayPal Lifecycle:** Purchases are bridged to PayPal Sandbox/Mock checkouts with SQLite transaction guarantees, webhook verification, and automated expiry reconciliation.

---

## 3. Key Features

- **Multilingual Assistant:** Natural language dialogue in English, Spanish, and French.
- **Deterministic Spending Mandates:** Enforce monetary caps, category whitelists, quantity limits, and strict UTC expirations.
- **Accurate Financial Arithmetic:** Built entirely on Python `Decimal` with half-up rounding; no binary floating-point inaccuracies.
- **One-Time Cryptographic Approval Secret:** Unguessable 32-byte secret generated per proposal; only the SHA-256 hash is persisted; held strictly in browser memory and zeroed upon use.
- **Durable SQLite Persistence:** Mandates, proposals, payment orders, and webhooks persist in SQLite with WAL mode and `BEGIN IMMEDIATE` transactions.
- **Safe PayPal Bridge:** Server-side INR-to-USD conversion, verified payment capture, idempotency headers, and cancellation rollback.
- **The Critical Reservation Rule:** Background worker verifies external provider state before releasing expired mandate reservations.
- **Offline Mock Mode Default:** Fully functional without API keys or internet access.

---

## 4. Supported Languages

| Language | Code | Dialogue Capabilities | Catalogue Explanations |
| :--- | :--- | :--- | :--- |
| **English** | `en` | Full conversational query & proposal generation | Product specs, warranties, checkout policies |
| **Spanish** | `es` | Consultas de productos en español nativo | Explicaciones claras de precios y categorías |
| **French** | `fr` | Assistance d'achat en français natif | Résumés des spécifications et des conditions |

---

## 5. Technology Stack

- **Backend Framework:** FastAPI 0.115+ (Python 3.11 / 3.12 / 3.13)
- **ASGI Server:** Uvicorn 0.30+
- **Data Validation & Schemas:** Pydantic v2
- **Persistence Engine:** SQLite3 with WAL mode, foreign keys, and `BEGIN IMMEDIATE` transactions
- **HTTP Client:** HTTPX 0.27+ (async requests for Gemini & PayPal REST APIs)
- **AI Providers:** Offline Deterministic Mock Assistant (default) or Google Gemini API (`gemini-3.6-flash`)
- **Payment Providers:** Offline Mock Payment Provider (default) or PayPal Sandbox REST API
- **Frontend:** Vanilla JavaScript (ES6+), HTML5, custom CSS (zero heavyweight frontend frameworks, zero third-party JS CDNs)
- **Test Framework:** Pytest 8.3+ (125 automated unit, integration, persistence, webhook, and audit hardening tests)

---

## 6. System Architecture & Component Responsibilities

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Vanilla JS Frontend (SPA)                       │
│  - Catalogue Browser  - Spending Mandates  - AI Chat  - PayPal Bridge │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │ JSON REST API / HTTPS
┌────────────────────────────────────▼───────────────────────────────────┐
│                          FastAPI Web Application                       │
│                                                                        │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────────────────┐ │
│  │ Catalogue API  │  │ AI Assistant   │  │ Policy Engine             │ │
│  │ (/products)    │  │ (/ai/chat)     │  │ (/mandates)               │ │
│  └───────┬────────┘  └───────┬────────┘  └─────────────┬─────────────┘ │
│          │                   │ (Read-only tools)       │               │
│          ▼                   ▼                         ▼               │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                   Authoritative Catalogue & Policy                │ │
│  └───────────────────────────────────┬───────────────────────────────┘ │
│                                      │                                 │
│                                      ▼                                 │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                      PayPal Payment Service                       │ │
│  │    - Order Creation   - Capture Verification   - Cancellation     │ │
│  │    - Webhook Signature & Replay Deduplication                     │ │
│  └───────────────────────────────────┬───────────────────────────────┘ │
│                                      │                                 │
│                                      ▼                                 │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │              Background Expiry Reconciliation Worker              │ │
│  └───────────────────────────────────┬───────────────────────────────┘ │
└──────────────────────────────────────┼─────────────────────────────────┘
                                       │ WAL mode & BEGIN IMMEDIATE
┌──────────────────────────────────────▼─────────────────────────────────┐
│                     Durable SQLite Database Engine                     │
│   spending_mandates │ purchase_proposals │ payment_orders │ webhooks   │
└────────────────────────────────────────────────────────────────────────┘
```

For complete architectural details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 7. How the AI Assistant Works

1. **User Prompt:** The shopper sends a natural-language query in English, Spanish, or French.
2. **Tool Execution:** The assistant queries the catalogue using read-only Python inspection tools (`search_catalogue_products`, `get_catalogue_product_details`, `list_catalogue_categories`).
3. **Intent Generation:** When recommending a product, the assistant generates a structured `proposal_intent` containing **only** `product_id` and `quantity`.
4. **Hard Security Boundary:** The assistant **cannot** set prices, specify currencies, apply discounts, or authorize funds. The policy engine intercepts the intent and resolves all data against authoritative catalogue records.

---

## 8. How Deterministic Spending Mandates Work

1. **Creation:** The user establishes a mandate specifying:
   - `max_amount` (e.g. ₹5,000.00 INR)
   - `permitted_categories` (e.g. `["Electronics", "Home & Kitchen"]`)
   - `max_quantity` (e.g. `2`)
   - `expires_in_seconds` (e.g. `3600`)
   - `requires_user_approval` (strictly `True`)
2. **Proposal Evaluation:** The policy engine checks:
   - Does `total_amount <= mandate.max_amount`?
   - Is `product.category` in `mandate.permitted_categories`?
   - Is `quantity <= mandate.max_quantity`?
   - Is `current_utc_time < mandate.expires_at`?
   - Is stock available?
3. **Approval Secret Generation:** If allowed, the engine creates a proposal in `PENDING_APPROVAL` status and returns an unguessable 32-byte hexadecimal secret.
4. **Explicit Human Approval:** The user reviews the details and submits the secret. Upon verification, the proposal transitions to `USER_APPROVED` and the mandate is locked to `RESERVED`.

---

## 9. PayPal Payment Lifecycle

```
[Proposal: USER_APPROVED] (Mandate: RESERVED)
       │
       ▼ POST /api/v1/payments/orders/create
 [PayPal Order: CREATED] (USD amount derived server-side via Decimal conversion)
       │
       ├───► POST /api/v1/payments/orders/{id}/cancel
       │       ▼
       │     [Order: CANCELLED] ──► [Mandate: ACTIVE (Reservation Released)]
       │
       ├───► Webhook: CHECKOUT.ORDER.APPROVED ──► [Order: BUYER_APPROVED] (Mandate stays RESERVED)
       ├───► Webhook: PAYMENT.CAPTURE.PENDING ──► [Order: CAPTURE_PENDING] (Mandate stays RESERVED)
       │
       ▼ POST /api/v1/payments/orders/{id}/capture OR Webhook: PAYMENT.CAPTURE.COMPLETED
 [Order: COMPLETED (Verified via PayPal / Mock)]
       │
       ├──► [Proposal: PAID]
       └──► [Mandate: CONSUMED (Permanently locked)]
```

### The Critical Reservation Rule & Reconciliation Worker
A mandate's expiry timestamp alone is **never** sufficient evidence to release a payment reservation. The background `ExpiryReconciliationWorker`:
1. Identifies expired reservations.
2. Queries the payment provider before taking action.
3. If capture is confirmed, finalizes the mandate to `CONSUMED`.
4. If capture is pending, uncertain, or the provider is unreachable, **retains the reservation**.
5. Releases the reservation only upon definitive safe terminal outcome (`VOIDED` or `EXPIRED`).

---

## 10. INR Catalogue Pricing & USD PayPal Demo Conversion

- **Catalogue Currency:** All 10 catalogue products are priced authoritatively in Indian Rupees (`INR`).
- **PayPal Sandbox Constraint:** PayPal Sandbox accounts do not support domestic INR payment processing.
- **Demo Currency Bridge:** To enable realistic PayPal checkout testing, the backend provides a deterministic server-side Decimal conversion to `USD` at a configurable rate:
  ```env
  PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD=0.012
  ```
  *(Example: ₹2,999.00 INR * 0.012 = $35.988 -> $35.99 USD rounded half-up).*
- **Authoritative Integrity:** The frontend cannot supply or override payment amounts or currencies. The USD amount is calculated on the server and displayed in a transparent currency breakdown card.

---

## 11. Security Principles & Limitations

### Core Security Guarantees
- **No Float Math:** All monetary operations use `Decimal` with two-decimal quantization.
- **Hashed Secrets:** Only SHA-256 hashes of approval secrets are persisted.
- **Memory-Only Client Secrets:** JavaScript closure isolation; zero persistent browser storage.
- **ACID State Transitions:** SQLite `BEGIN IMMEDIATE` transactions prevent race conditions.
- **Asymmetric Webhook Verification:** Official PayPal signature verification prevents forged notifications.

### Known Limitations (Not Production-Ready)
- **Fixed Demo FX Rate:** The INR-to-USD conversion rate is fixed; production requires live multi-currency FX feeds.
- **SQLite Concurrency:** SQLite is single-writer. High-volume enterprise concurrency requires PostgreSQL.
- **Single Background Worker:** The reconciliation worker runs in-process; production clustering requires Celery/Redis.
- **Live PayPal Sandbox Verification Pending:** Verified thoroughly under Mock Mode (125 tests); live Sandbox credentials required for external PayPal network calls.

For complete security analysis, see [docs/SECURITY.md](docs/SECURITY.md).

---

## 12. Project Structure

```text
multilingual-safe-checkout-helper/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI application & lifespan management
│   ├── core/
│   │   ├── config.py               # Environment configuration settings
│   │   └── database.py             # SQLite DatabaseManager, WAL, transactions
│   ├── catalogue/
│   │   ├── models.py               # Product entity with Decimal validation
│   │   ├── repository.py           # In-code seeded catalogue (10 products)
│   │   ├── service.py              # Filtering, query, and search logic
│   │   └── router.py               # Read-only catalogue REST endpoints
│   ├── policy/
│   │   ├── models.py               # Mandate and proposal Pydantic models
│   │   ├── repository.py           # SQLite repository for mandates & proposals
│   │   ├── service.py              # Pure policy evaluation & approval engine
│   │   └── router.py               # Spending mandate REST endpoints
│   ├── ai/
│   │   ├── models.py               # Chat requests, responses, proposal intents
│   │   ├── prompts.py              # Multilingual system prompts & guardrails
│   │   ├── tools.py                # Read-only catalogue inspection tool manager
│   │   ├── service.py              # AIService (Mock & Gemini providers)
│   │   └── router.py               # Multilingual AI chat endpoint
│   └── payment/
│       ├── models.py               # Payment schemas, currency breakdown, records
│       ├── currency.py             # Server-side Decimal INR-to-USD conversion
│       ├── repository.py           # SQLite repository for orders & webhooks
│       ├── service.py              # Mock & PayPalSandbox payment providers
│       ├── router.py               # Payment order & webhook endpoints
│       └── worker.py               # ExpiryReconciliationWorker background task
├── frontend/
│   ├── index.html                  # Catalogue browser, AI chat, mandate UI
│   ├── css/
│   │   └── style.css               # Vanilla CSS styling
│   └── js/
│       └── main.js                 # Safe DOM rendering, closure secrets, checkout
├── docs/
│   ├── DEMO_SCRIPT.md              # 3-minute hackathon demo script
│   ├── ARCHITECTURE.md             # Detailed architecture & Mermaid diagrams
│   └── SECURITY.md                 # Security analysis, threat model & limitations
├── tests/
│   ├── conftest.py                 # Isolated temporary SQLite database fixture
│   ├── test_health.py              # Health check endpoint test (1 test)
│   ├── test_catalogue.py           # Catalogue unit & integration tests (15 tests)
│   ├── test_policy.py              # Policy engine & approval security tests (25 tests)
│   ├── test_ai.py                  # AI assistant & multilingual tests (15 tests)
│   ├── test_payment.py             # PayPal Sandbox, Mock, conversion tests (13 tests)
│   ├── test_sqlite_persistence.py  # SQLite persistence & concurrency tests (12 tests)
│   ├── test_webhook_and_reconciliation.py # Webhook & expiry worker tests (23 tests)
│   ├── test_frontend_integration.py# Static asset & E2E HTTP integration tests (3 tests)
│   └── test_audit_hardening.py     # 18 end-to-end audit scenario tests (18 tests)
├── data/
│   └── .gitkeep                    # SQLite storage directory placeholder
├── .env.example                    # Template for environment configuration
├── .gitignore                      # Git exclusion rules
├── pytest.ini                      # Pytest settings with pythonpath configured
├── requirements.txt                # Pinned core dependencies
└── README.md                       # Complete project documentation
```

---

## 13. Installation & Configuration

### Prerequisites
- Python 3.11, 3.12, or 3.13 installed on Windows.
- PowerShell or command prompt.
- Git.

### Setup Instructions

1. **Clone the repository:**
   ```powershell
   git clone <repository-url>
   cd "Multilingual Safe-Checkout Helper"
   ```

2. **Create and activate a Python virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Install dependencies:**
   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   ```powershell
   Copy-Item .env.example .env
   ```

### Default Configuration (Mock Mode — Zero Credentials Required)
The application is pre-configured to run out-of-the-box in offline Mock Mode:
```env
PROJECT_NAME="Multilingual Safe-Checkout Helper"
ENVIRONMENT="development"
DEBUG=True
HOST="127.0.0.1"
PORT=8000
SQLITE_DB_PATH="data/checkout_helper.db"
AI_PROVIDER=mock
PAYPAL_MODE=mock
PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD=0.012
EXPIRY_RECONCILIATION_ENABLED=true
RECONCILIATION_INTERVAL_SECONDS=60
```

### Optional Live Sandbox Modes
- **Gemini Live AI:** Set `AI_PROVIDER=gemini`, `GEMINI_API_KEY="your-key"`, `GEMINI_MODEL="gemini-3.6-flash"`.
- **PayPal Sandbox:** Set `PAYPAL_MODE=sandbox`, `PAYPAL_CLIENT_ID="your-id"`, `PAYPAL_CLIENT_SECRET="your-secret"`, `PAYPAL_WEBHOOK_ID="your-webhook-id"`.

---

## 14. Running the Application

Start the FastAPI application using Uvicorn:
```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Application URLs
- **Web Frontend:** `http://127.0.0.1:8000/`
- **Health Check:** `http://127.0.0.1:8000/health`
- **Interactive OpenAPI Documentation (Swagger UI):** `http://127.0.0.1:8000/docs`

---

## 15. Running Automated Tests

Execute the complete test suite across all 125 tests:
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

All 125 tests run deterministically and offline using an isolated temporary SQLite database fixture (`tests/conftest.py`) without modifying real application data or requiring external API credentials.

---

## 16. API Endpoint Reference

### Health
- `GET /health` — Service health and operational status.

### Catalogue
- `GET /api/v1/products` — List and filter products (`category`, `available`, `search`).
- `GET /api/v1/products/categories` — List unique sorted categories.
- `GET /api/v1/products/{product_id}` — Get single product by ID.

### Spending Mandates & Policy
- `POST /api/v1/mandates` — Create a spending mandate with budget and category caps.
- `GET /api/v1/mandates/{mandate_id}` — Get mandate details and lifecycle state.
- `POST /api/v1/mandates/{mandate_id}/evaluate` — Evaluate a purchase proposal against a mandate.
- `POST /api/v1/mandates/evaluate` — Alternative evaluation endpoint with mandate ID in body.
- `GET /api/v1/mandates/proposals/{proposal_id}` — Retrieve evaluated proposal.
- `POST /api/v1/mandates/proposals/{proposal_id}/approve` — Approve proposal with cryptographic secret.
- `POST /api/v1/mandates/{mandate_id}/revoke` — Revoke active mandate.

### Multilingual AI Assistant
- `POST /api/v1/ai/chat` — Multilingual chat query (English, Spanish, French) with read-only catalogue inspection.

### PayPal Payments & Webhooks
- `POST /api/v1/payments/orders/create` — Create PayPal payment order for approved proposal.
- `POST /api/v1/payments/orders/{order_id}/capture` — Verify and capture payment order; finalize mandate.
- `POST /api/v1/payments/orders/{order_id}/cancel` — Cancel checkout and release mandate reservation.
- `GET /api/v1/payments/orders/{order_id}` — Retrieve payment order record and capture status.
- `POST /api/v1/payments/webhooks` — Ingest and verify official PayPal Sandbox webhook events.

---

## 17. Demonstration Instructions

To conduct a three-minute live demonstration:
1. Ensure the application is running at `http://127.0.0.1:8000/`.
2. Follow the exact timestamped narration and screen actions in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md):
   - **0:00 – 0:30:** Explain cross-border checkout risks and AI boundaries.
   - **0:30 – 1:00:** Create a ₹3,000 mandate for "Electronics".
   - **1:00 – 1:35:** Ask in Spanish: *"Quiero auriculares inalámbricos de buena calidad"*.
   - **1:35 – 2:00:** Review policy evaluation and click **"Approve Proposal"**.
   - **2:00 – 2:35:** Click **"Pay with PayPal (Mock Sandbox)"** and simulate capture.
   - **2:35 – 3:00:** Verify mandate marked `CONSUMED` and proposal marked `PAID`.

---

## 18. Current Limitations & Future Improvements

| Current Prototype Limitation | Planned Production Improvement |
| :--- | :--- |
| Fixed demonstration conversion rate (0.0120 USD/INR). | Integration with real-time foreign exchange API feeds with slippage bands. |
| SQLite single-writer lock serialization. | Migration to PostgreSQL with row-level locking (`SELECT ... FOR UPDATE`). |
| In-process `asyncio` background reconciliation worker. | Distributed task queue (Celery + Redis) with distributed lock coordination. |
| Static 10-item in-code product catalogue. | Dynamic product catalog backed by inventory database and search indexing. |
| Local Mock Mode testing only. | End-to-end verification against live PayPal Sandbox with registered merchant accounts. |

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.