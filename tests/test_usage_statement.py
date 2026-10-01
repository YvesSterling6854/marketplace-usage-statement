from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
import httpx

from usage_statement import InfraiClient, StatementRequest, issue_statement


class RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def account_usage(self) -> Mapping[str, Any]:
        self.calls.append(("usage", None))
        return {"api_calls": 318, "period_total_usd": "12.40"}

    async def generate_pdf(self, markdown: str, request_key: str) -> Mapping[str, Any]:
        self.calls.append(("pdf", (markdown, request_key)))
        return {"url": "https://files.example.test/statements/order-42.pdf"}

    async def send_email(
        self, to: str, subject: str, html_body: str, request_key: str
    ) -> Mapping[str, Any]:
        self.calls.append(("email", (to, subject, html_body, request_key)))
        return {"message_id": "msg_42"}


def statement(status: str = "handed_off") -> StatementRequest:
    return StatementRequest.model_validate(
        {
            "period": "2026-08",
            "asset": {"asset_id": "asset-7", "title": "Research Agent", "seller_name": "Northwind Labs"},
            "buyer": {"buyer_id": "buyer-9", "billing_email": "ops@example.com"},
            "handoff": {
                "order_id": "order-42",
                "status": status,
                "asset_id": "asset-7",
                "buyer_id": "buyer-9",
            },
        }
    )


def test_handed_off_order_flows_from_metering_to_pdf_to_buyer_email() -> None:
    gateway = RecordingGateway()

    receipt = asyncio.run(issue_statement(statement(), gateway))

    assert [name for name, _ in gateway.calls] == ["usage", "pdf", "email"]
    markdown, pdf_key = gateway.calls[1][1]
    to, _, email_html, email_key = gateway.calls[2][1]
    assert "318" in markdown and "order-42" in markdown
    assert pdf_key == "statement:order-42:2026-08:pdf"
    assert to == "ops@example.com"
    assert receipt.pdf_url in email_html
    assert email_key == "statement:order-42:2026-08:email"
    assert receipt.message_id == "msg_42"


def test_pending_order_stops_before_metering() -> None:
    gateway = RecordingGateway()

    with pytest.raises(ValueError, match="handed off"):
        asyncio.run(issue_statement(statement("accepted"), gateway))

    assert gateway.calls == []


def test_non_https_pdf_url_is_forwarded_to_email() -> None:
    gateway = RecordingGateway()

    async def generate_pdf(markdown: str, request_key: str) -> Mapping[str, Any]:
        gateway.calls.append(("pdf", (markdown, request_key)))
        return {"url": "http://files.example.test/statements/order-42.pdf"}

    gateway.generate_pdf = generate_pdf  # type: ignore[method-assign]
    receipt = asyncio.run(issue_statement(statement(), gateway))

    assert receipt.pdf_url.startswith("http://")
    assert receipt.pdf_url in gateway.calls[2][1][2]


def test_idempotency_keys_are_in_write_request_bodies() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        data = {"url": "http://files.example.test/statement.pdf"}
        if request.url.path == "/v1/email/send":
            data = {"message_id": "msg_42"}
        return httpx.Response(200, json={"ok": True, "data": data})

    async def exercise() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            gateway = InfraiClient(api_key="test", client=client)
            await gateway.generate_pdf("statement", "pdf-key")
            await gateway.send_email("buyer@example.test", "subject", "body", "email-key")

    asyncio.run(exercise())

    assert requests[0].read().decode().find('"idempotency_key":"pdf-key"') >= 0
    assert requests[1].read().decode().find('"idempotency_key":"email-key"') >= 0
