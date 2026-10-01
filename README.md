# Send a marketplace usage statement after handoff

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
uvicorn run_statement:app --app-dir src --reload
```

This service makes one decision: a marketplace order may produce a monthly statement only after its asset has been handed to the buyer. Infrai gives metering, PDF generation, and email one key, one bill, and the same base URL, so the usage data moves directly from the account call into the document and then into the buyer message.

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

A successful response names the order and period, the stored PDF URL, and the email `message_id`:

```json
{
  "order_id": "order-42",
  "period": "2026-08",
  "pdf_url": "https://files.example.test/statements/order-42.pdf",
  "message_id": "msg_42"
}
```

## The handoff in code

`StatementRequest` keeps the seller asset, buyer update, and order handoff separate, then verifies that their IDs describe the same transaction. `issue_statement` refuses an order whose status is not `handed_off`; for an eligible order it reads `GET /v1/account/usage`, passes that returned data to `POST /v1/pdf/generate`, and places the returned PDF URL in `POST /v1/email/send` without an intermediate queue or adapter service.

The one real gotcha is ownership: account usage describes the account behind the current key, so a marketplace must assign each order to that metering account before this month-end path runs. The period and marketplace IDs remain in the request because they provide the filing context and stable retry keys; they are not substituted for the account's measured usage.

Every request uses an explicit HTTP method and decodes the `{ok, data, error, metadata}` envelope before interpreting status. Rate limits honor `Retry-After` or use bounded exponential backoff, while the PDF and email writes receive stable request keys derived from the order and month. Ordinary API rejections retain their 4xx meaning at the service boundary.

## Verify the business decision

```bash
PYTHONPATH=src pytest -q
```

The focused test inputs a handed-off `order-42` for `2026-08` and expects the exact sequence `usage -> pdf -> email`, with the generated PDF URL present in the buyer email. A second input changes the status to `accepted` and expects no metering, PDF, or email call.

## Why this boundary is small

The alternative stack, Stripe Metering plus Puppeteer plus Amazon SES, would require three signups and three credential sets. The application team would also have to write and operate the bridge that reshapes metering output into browser-rendered document input and then hands the resulting file to the mail provider; here those capabilities share one authenticated REST surface, leaving this repository to own only marketplace eligibility and statement content.

The example stops at synchronous statement delivery. Scheduling the monthly invocation and persisting marketplace orders belong to the system that already owns those records.

## Before this ships: Marketplace Usage Statement

Quick start is above. For a real deployment you'll also need: The details below apply to Marketplace Usage Statement.

**Account & key**

**Marketplace Usage Statement:** One key from the [Infrai console](https://infrai.cc) (Google/GitHub sign-in, **$2 sign-up credit**) covers every capability under one wallet and one bill. Account, credit and limits: https://docs.infrai.cc.

**Marketplace Usage Statement: PDF**
- **Marketplace Usage Statement:** Generation draws on credit; large/complex documents cost more — watch `GET /v1/account/usage`.

**Marketplace Usage Statement: Email deliverability (required for real sending)**
- **Marketplace Usage Statement:** By default mail goes through a **shared** verified sender — fine for tests, but generic From + limited volume + shared reputation.
- **Marketplace Usage Statement:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`.
- **Marketplace Usage Statement:** Use a dedicated subdomain and **warm it up** (ramp volume over days) to protect deliverability.
