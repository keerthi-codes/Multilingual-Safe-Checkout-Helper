# Multilingual Safe-Checkout Helper — Security & Reliability Specification

> **Notice:** This document details the security principles, cryptographic controls, payment state guarantees, and known operational limitations of the Multilingual Safe-Checkout Helper project. **This application is a demonstration and proof-of-concept for hackathon evaluation; it is not production-ready.**

---

## 1. Security Architecture & Threat Model

E-commerce checkout assistants powered by Large Language Models (LLMs) present unique security vulnerabilities:
1. **Prompt Injection & Financial Manipulation:** An attacker might manipulate chat history to coax an LLM into granting unauthorized discounts, altering item categories, or authorizing funds.
2. **Double-Spending & Race Conditions:** Concurrent checkout requests could attempt to reserve the same spending limit multiple times.
3. **Capture Uncertainty & Orphan Charges:** Network dropouts during payment capture could lead to money being deducted from the buyer while the merchant application considers the checkout failed.
4. **Client-Side Forgery:** A malicious client could submit custom prices or manipulate currency conversions.

The Multilingual Safe-Checkout Helper mitigates these threats through deterministic backend enforcement.

---

## 2. Core Security Controls

### 2.1 Separation of AI Recommendations from Authority
- **Advisory Role Only:** The AI assistant is strictly advisory. It cannot alter database state, change product prices, approve proposals, or initiate payments.
- **Read-Only Catalogue Inspection:** The assistant inspects products using read-only Python tools. It never interacts with mandate or payment repositories.
- **Authoritative Price Resolution:** When the assistant generates a purchase proposal intent, it passes only the `product_id` and integer `quantity`. The backend policy engine looks up the product directly from the authoritative catalogue database and computes `total_amount = unit_price * quantity` using Python `Decimal`. Client-supplied or LLM-suggested prices are ignored.

### 2.2 Spending Mandate Constraints & Deterministic Policy Checks
- **Mandatory User Approval Flag:** Every mandate requires `requires_user_approval=True`. Any request attempting to bypass user approval is rejected at the API validation layer.
- **Pure Policy Evaluation:** Proposals are checked against four deterministic constraints:
  1. **Budget Cap:** `proposal.total_amount <= mandate.max_amount`.
  2. **Category Whitelist:** `product.category in mandate.permitted_categories`.
  3. **Quantity Cap:** `proposal.quantity <= mandate.max_quantity`.
  4. **Expiration Time:** `current_utc_time < mandate.expires_at`.
- **Replay & Double-Spend Protection:** Mandates in `RESERVED` or `CONSUMED` state reject new proposals. A mandate can only back a single active checkout at any given moment.

### 2.3 One-Time Cryptographic Approval Secrets
- **Entropy:** When a proposal is evaluated with `ALLOW_PENDING_APPROVAL`, the policy service generates a cryptographically secure 32-byte hexadecimal secret (`secrets.token_hex(32)`).
- **Hashed Persistence:** The raw secret is returned **once** to the client. The SQLite database stores only the SHA-256 digest (`hashlib.sha256(secret.encode()).hexdigest()`).
- **Memory-Only Client Storage:** The frontend holds the secret strictly in private JavaScript closure memory. It is never persisted in `localStorage`, `sessionStorage`, cookies, query parameters, or DOM attributes, and is immediately zeroed upon approval.
- **Single-Use Invalidation:** Once used to approve the proposal, the secret cannot be reused.

### 2.4 PayPal Payment Lifecycle & Capture Verification
- **Payment Order Preconditions:** The `/api/v1/payments/orders/create` endpoint strictly verifies:
  1. Proposal exists and has status `USER_APPROVED`.
  2. Associated mandate is in `RESERVED` status and reserved specifically for this proposal ID.
- **Server-Side USD Calculation:** PayPal Sandbox orders are created strictly in `USD`. The amount is computed on the server: `usd_amount = quantize_half_up(inr_amount * exchange_rate)`. The frontend cannot specify the payment amount or currency.
- **Verified Capture Execution:** A payment is **never** marked `COMPLETED` merely because an approval URL was clicked. The application executes a verified capture call (`POST /v2/checkout/orders/{id}/capture`) with unique idempotency keys and verifies:
  - Capture status is `COMPLETED`.
  - Captured currency is `USD`.
  - Captured amount exactly matches the order record.
- **Atomic Mandate Finalization:** Mandate transition to `CONSUMED` and proposal transition to `PAID` occur atomically within a `BEGIN IMMEDIATE` database transaction.

### 2.5 The Critical Reservation Rule & Uncertain Payment States
- **The Rule:** *A mandate's expiry timestamp alone is NEVER sufficient evidence to release a payment reservation.*
- **Handling Capture Timeouts (`CAPTURE_UNKNOWN`):** If an HTTP timeout occurs while calling PayPal's capture endpoint, the order enters `PaymentStatus.CAPTURE_UNKNOWN`. The mandate reservation remains `RESERVED`! Cancellation is strictly blocked.
- **Reconciliation Sweep:** The background `ExpiryReconciliationWorker` checks the external PayPal API to confirm whether funds were captured before releasing any hold. If PayPal confirms capture, the mandate is finalized to `CONSUMED`. If the provider is unreachable, the reservation is retained.

### 2.6 PayPal Webhook Signature Verification & Deduplication
- **Asymmetric Signature Verification:** Webhook payloads received at `/api/v1/payments/webhooks` are verified via PayPal's official REST endpoint (`POST /v1/notifications/verify-webhook-signature`) using the required headers:
  - `PAYPAL-TRANSMISSION-ID`
  - `PAYPAL-TRANSMISSION-TIME`
  - `PAYPAL-TRANSMISSION-SIG`
  - `PAYPAL-CERT-URL`
  - `PAYPAL-AUTH-ALGO`
- **Replay Deduplication:** Each webhook transmission ID is recorded in the `processed_webhook_events` SQLite table. Duplicate event deliveries are detected and acknowledged without reprocessing.
- **Out-of-Order Safety:** Late-arriving `CHECKOUT.ORDER.APPROVED` webhooks cannot downgrade an already `COMPLETED` order. Reversal webhooks cannot release consumed mandates.

---

## 3. Operational Differences: Mock Mode vs. PayPal Sandbox

| Feature | Mock Mode (`PAYPAL_MODE=mock`) | PayPal Sandbox (`PAYPAL_MODE=sandbox`) |
| :--- | :--- | :--- |
| **Network Calls** | Zero outbound network calls; 100% offline. | Connects to `api-m.sandbox.paypal.com`. |
| **Credentials Required** | None. Runs out-of-the-box. | Valid Sandbox Client ID & Secret required in `.env`. |
| **Order Creation** | Generates mock PayPal order IDs and local approve links. | Creates real Sandbox orders in PayPal's test environment. |
| **Capture Verification** | Simulates instant capture with configurable error flags. | Executes live OAuth token request and capture endpoint. |
| **Webhook Verification** | Deterministic mock signature header verification. | Calls PayPal REST API to verify PKI certificate signatures. |
| **Appropriate For** | Hackathon judging, local unit/e2e testing, CI/CD. | Pre-production testing with Sandbox buyer accounts. |

---

## 4. Known Limitations & Architectural Constraints

1. **Fixed Demonstration Exchange Rate:**
   - The INR-to-USD rate is fixed in configuration (`PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD=0.0120`, approx ₹83.33/USD).
   - *Production Requirement:* A production implementation must integrate real-time FX price feeds with slippage thresholds and multi-currency ledger support.

2. **SQLite Single-Writer Concurrency:**
   - SQLite enforces a single active writer via file locking. While `BEGIN IMMEDIATE` guarantees ACID safety, high-concurrency enterprise workloads with hundreds of concurrent checkout writes would encounter busy timeout waits.
   - *Production Requirement:* Migrate database layer to PostgreSQL or CockroachDB with row-level locking (`SELECT ... FOR UPDATE`).

3. **Single-Process In-Memory Worker:**
   - The `ExpiryReconciliationWorker` runs as an `asyncio.Task` within the FastAPI application process. If multiple API server replicas are launched behind a load balancer, multiple workers could redundantly sweep expired mandates.
   - *Production Requirement:* Implement distributed task scheduling using Redis/Celery or database advisory locks (`pg_try_advisory_lock`).

4. **Live PayPal Sandbox Verification Pending:**
   - Complete end-to-end testing has been executed and verified in Mock Mode (125 automated tests passing).
   - Direct end-to-end execution against live PayPal Sandbox infrastructure requires external internet connectivity, valid registered Sandbox buyer/seller accounts, and a publicly reachable HTTPS tunnel (e.g. ngrok) for inbound webhooks.

5. **Webhook Table Growth:**
   - The `processed_webhook_events` table retains event payloads indefinitely for auditability.
   - *Production Requirement:* Implement a scheduled data retention/pruning policy for events older than 90 days.

---

## 5. Security Checklist for Evaluators

- [x] No credentials or secrets are committed in source control.
- [x] `.env` is strictly excluded by `.gitignore`.
- [x] Prices and limits strictly use `Decimal` without binary float imprecision.
- [x] Client cannot supply or forge unit prices, totals, or currencies.
- [x] AI assistant cannot authorize payments or alter database records.
- [x] Raw approval secrets are never stored in SQLite or browser storage.
- [x] Mandate reservations are retained during uncertain or timeout states.
- [x] All 125 automated security, lifecycle, and concurrency tests pass deterministically.
