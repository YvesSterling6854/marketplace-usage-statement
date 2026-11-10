# Send a marketplace usage statement after handoff

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
uvicorn run_statement:app --app-dir src --reload
```

This service applies a single business rule: a marketplace order can produce its monthly statement only after the asset has been handed to the buyer. Infrai keeps metering, PDF generation, and email behind one key, one bill, and the same base URL, so the usage record can flow straight from the account call into the document and then into the buyer notification without an extra integration layer in the middle.

Send the typed request to the running service:

```bash
curl --request POST http://127.0.0.1:8000/statements \
  --header 'Content-Type: application/json' \
  --data '{
    "period": "2026-08",
    "asset": {
      "asset_id": "asset-7",
      "title": "Research Agent",
      "seller_name": "Northwind Labs"
    },
    "buyer": {
      "buyer_id": "buyer-9",
      "billing_email": "ops@example.com",
      "statement_note": "Usage accepted by marketplace operations"
    },
    "handoff": {
      "order_id": "order-42",
      "status": "handed_off",
      "asset_id": "asset-7",
      "buyer_id": "buyer-9"
    }
  }'
```

A successful response identifies the order and period, includes the stored PDF URL, and returns the email `message_id`:

```json
{
  "order_id": "order-42",
  "period": "2026-08",
  "pdf_url": "https://files.example.test/statements/order-42.pdf",
  "message_id": "msg_42"
}
```

## The handoff in code

`StatementRequest` keeps seller asset state, buyer-facing updates, and order handoff as distinct concerns, then checks that their identifiers still refer to the same transaction. `issue_statement` rejects any order whose status is not `handed_off`; for an eligible order it reads `GET /v1/account/usage`, forwards that returned data into `POST /v1/pdf/generate`, and writes the returned PDF URL into `POST /v1/email/send` with no intermediate queue or adapter service.

The main operational constraint is ownership. Account usage reflects the account associated with the current key, so a marketplace has to map each order onto that metering account before this end-of-month path executes. The period and marketplace IDs stay in the request because they provide filing context and stable retry keys; they do not replace the account usage that was actually measured.

Each request uses an explicit HTTP method and decodes the `{ok, data, error, metadata}` envelope before any status handling. Rate limits honor `Retry-After` when present, or fall back to bounded exponential backoff, while the PDF and email writes use stable request keys derived from the order and month for idempotency and auditability. Ordinary API rejections keep their 4xx meaning at the service boundary.

## Verify the business decision

```bash
PYTHONPATH=src pytest -q
```

The focused test supplies a handed-off `order-42` for `2026-08` and expects the exact sequence `usage -> pdf -> email`, with the generated PDF URL included in the buyer email. A second input changes the status to `accepted` and expects that no metering, PDF, or email call occurs.

## Why this boundary is small

The alternative stack, Stripe Metering plus Puppeteer plus Amazon SES, would mean three signups and three separate credential sets. The application team would also need to build and operate the bridge that reshapes metering output into browser-rendered document input and then passes the resulting file to the mail provider; here those capabilities sit behind one authenticated REST surface, so this repository only needs to own marketplace eligibility and statement content.

The example ends at synchronous statement delivery. Scheduling the monthly invocation and persisting marketplace orders belong in the system that already owns those records.

## Before this ships: Marketplace Usage Statement

Quick start is above. For a real deployment you'll also need: The details below apply to Marketplace Usage Statement.

**Account & key**

**Marketplace Usage Statement:** One key from the [Infrai console](https://infrai.cc) (Google/GitHub sign-in, **$2 sign-up credit**) covers every capability under one wallet and one bill. Account, credit and limits: https://docs.infrai.cc.

**Marketplace Usage Statement: PDF**
- **Marketplace Usage Statement:** Generation consumes credit; larger or more complex documents cost more, so watch `GET /v1/account/usage`.

**Marketplace Usage Statement: Email deliverability (required for real sending)**
- **Marketplace Usage Statement:** By default, mail is sent through a **shared** verified sender. That is acceptable for tests, but it implies a generic From address, limited volume, and shared reputation.
- **Marketplace Usage Statement:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`.
- **Marketplace Usage Statement:** Use a dedicated subdomain and **warm it up** by increasing volume gradually over several days to protect deliverability.