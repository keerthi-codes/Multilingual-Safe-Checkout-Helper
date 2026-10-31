# Multilingual Safe-Checkout Helper — 3-Minute Demonstration Script

This script provides an exact, step-by-step walkthrough for a hackathon presentation or video demonstration lasting under three minutes.

> **Important Disclosure:** This demonstration operates in **Mock Mode** by default (`AI_PROVIDER=mock`, `PAYPAL_MODE=mock`). All AI reasoning and PayPal payment lifecycles are deterministically simulated locally. No real money or live PayPal accounts are used. The INR-to-USD conversion is a fixed demo bridge.

---

## Demo Overview & Timeline (0:00 – 3:00)

| Timestamp | Phase | Core Action | Expected Screen Result |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:30** | The Problem | Explain language barriers & runaway checkout risks | Clean home dashboard at `http://127.0.0.1:8000/` |
| **0:30 – 1:00** | Spending Mandate | Define ₹3,000 budget cap on "Electronics" | Active mandate card appears with green badge |
| **1:00 – 1:35** | Multilingual AI Chat | Query assistant in Spanish (*"Quiero auriculares inalámbricos"*) | AI recommends Noise-Cancelling Headphones in Spanish with a purchase proposal |
| **1:35 – 2:00** | Deterministic Policy & Approval | Review proposal; click "Approve Proposal" | Proposal approved; mandate state moves to `RESERVED` |
| **2:00 – 2:35** | PayPal Demo Checkout | Initiate checkout; review USD bridge; simulate capture | Currency breakdown card ($35.99 USD); payment marked `COMPLETED` |
| **2:35 – 3:00** | Final Verification | Show mandate consumed and transaction persistence | Mandate badge `CONSUMED`; proposal `PAID`; SQLite durability |

---

## Detailed Step-by-Step Walkthrough

### 1. The Problem Statement (0:00 – 0:30)

**Narration:**
> *"Welcome! Millions of cross-border online shoppers struggle when checking out on foreign e-commerce platforms. Complex English terms, hidden add-ons, currency confusion, and unclear cancellation policies often lead to costly checkout mistakes or buyer remorse.*
> 
> *Our solution is the **Multilingual Safe-Checkout Helper**—an AI shopping assistant that speaks your language (English, Spanish, French), but is strictly governed by a deterministic spending mandate engine. The AI can advise and translate, but it has zero power over prices or payments."*

**Action:**
- Show the web interface at `http://127.0.0.1:8000/` with the Product Catalogue, Spending Mandate form, and Assistant chat interface visible.

---

### 2. Setting a Spending Mandate (0:30 – 1:00)

**Narration:**
> *"Before shopping, the buyer establishes guardrails by creating a spending mandate. Here, the user sets a maximum budget of ₹3,000 INR, restricts allowed purchases strictly to 'Electronics', and mandates explicit personal approval before any money moves."*

**Action:**
1. In the **Create Spending Mandate** card on the left:
   - **Max Amount (INR):** `3000.00`
   - **Permitted Categories:** Check `Electronics`.
   - **Requires User Approval:** Checked (mandatory).
2. Click **Create Spending Mandate**.

**Expected Screen Result:**
- An active mandate card appears showing:
  - **Status:** `ACTIVE` (green badge)
  - **Limit:** ₹3,000.00 INR
  - **Permitted:** Electronics
  - **Mandate ID:** `mandate-xxxx`

---

### 3. Multilingual AI Assistance & Proposal Generation (1:00 – 1:35)

**Narration:**
> *"Now the buyer chats in their native language. Let's ask in Spanish: 'Quiero unos auriculares inalámbricos de buena calidad' (I want good quality wireless headphones).*
> 
> *Notice: The AI uses read-only catalogue inspection tools to find 'prod-001' (Noise-Cancelling Headphones). It responds natively in Spanish and generates a structured purchase proposal."*

**Action:**
1. In the **Multilingual Shopping Assistant** chat box:
   - Select Language: **Spanish (es)**
   - Type message: `Quiero auriculares inalámbricos de buena calidad`
   - Click **Send**.

**Expected Screen Result:**
- The assistant replies in fluent Spanish explaining the features of the Noise-Cancelling Headphones.
- A **Purchase Proposal Card** appears in the chat:
  - **Product:** Noise-Cancelling Headphones (`prod-001`)
  - **Category:** Electronics
  - **Price:** ₹2,999.00 INR
  - **Quantity:** 1
  - **Policy Result:** `ALLOW_PENDING_APPROVAL`

---

### 4. Deterministic Policy Check & Explicit User Approval (1:35 – 2:00)

**Narration:**
> *"Look closely at the security boundary: The AI did not calculate ₹2,999.00—our deterministic backend verified the authoritative catalogue database. The backend verified that ₹2,999 is within the ₹3,000 mandate, matches the 'Electronics' category, and that stock is available.*
> 
> *To prevent unauthorized automated checkout, the policy engine issued a cryptographic, one-time `approval_secret`. The frontend holds this secret only in memory. When the user clicks 'Approve Proposal', the secret is sent once and immediately cleared."*

**Action:**
1. On the proposal card, click **Approve Proposal**.

**Expected Screen Result:**
- The proposal status changes to **USER_APPROVED** (blue badge).
- The spending mandate status updates to **RESERVED** (yellow badge), locking the ₹2,999 allocation so competing requests cannot double-spend it.
- The **PayPal Checkout** button appears.

---

### 5. PayPal Demo Checkout & Capture Verification (2:00 – 2:35)

**Narration:**
> *"Now we move to payment. Because PayPal Sandbox does not support domestic INR processing, our system provides an authoritative, server-side demo bridge to USD at a fixed demo conversion rate of 1 USD = 83.33 INR.*
> 
> *The client cannot forge this amount. In our mock demonstration mode, we click 'Pay with PayPal' to view the breakdown card and simulate the provider checkout."*

**Action:**
1. Click **Pay with PayPal (Mock Sandbox)**.
2. The payment breakdown displays:
   - **Catalogue Total:** ₹2,999.00 INR
   - **Sandbox Payment Total:** $35.99 USD
   - **Exchange Rate:** 0.0120 (Demo Rate)
   - **Order ID:** `MOCK-ORD-xxxx`
3. Click **Simulate PayPal Approval & Capture**.

**Expected Screen Result:**
- A loading indicator runs briefly while the backend executes transactional verification.
- The UI displays: **"Payment Completed! Transaction captured successfully."**
- Order status updates to **COMPLETED** with Capture ID `MOCK-CAP-xxxx`.

---

### 6. Mandate Consumption & SQLite Durability (2:35 – 3:00)

**Narration:**
> *"Finally, observe the financial integrity of the system:
> 1. The mandate status has transitioned from `RESERVED` to `CONSUMED`. It is permanently locked and cannot be replayed or reused.
> 2. The proposal is marked `PAID`.
> 3. Everything is persisted in SQLite with `BEGIN IMMEDIATE` transaction locking. Even if we refresh the page or restart the server, the transaction history is durable and tamper-proof.
> 
> Thank you!"*

**Action:**
1. Highlight the mandate badge: `CONSUMED` (gray locked badge).
2. Highlight the proposal status: `PAID` (green checkmark).
3. Refresh the browser page (`F5`) to demonstrate that the state persists cleanly across reloads without creating duplicate orders or losing records.

---

## Fallback Contingency Plan

| Failure Scenario | Fallback Action During Demo |
| :--- | :--- |
| **Gemini Live API Quota Exceeded or Offline** | The application is pre-configured with `AI_PROVIDER=mock`. The mock provider runs 100% offline and returns deterministic English, Spanish, and French responses with catalogue proposal intents. |
| **Port 8000 in use** | Start on port 8001: `.\.venv\Scripts\uvicorn.exe app.main:app --port 8001` and open `http://127.0.0.1:8001/`. |
| **Browser cache issues** | Hard refresh using `Ctrl + F5` or open an Incognito/Private window. |
| **Existing database has old test data** | Delete `data/checkout_helper.db`. The application automatically recreates clean SQLite tables upon startup. |
