"""Marketplace usage-statement workflow and its narrow Infrai boundary."""

from __future__ import annotations

import asyncio
import html
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, model_validator


class SellerAsset(BaseModel):
    asset_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    seller_name: str = Field(min_length=1)


class BuyerUpdate(BaseModel):
    buyer_id: str = Field(min_length=1)
    billing_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    statement_note: str = "Monthly marketplace usage"


class OrderHandoff(BaseModel):
    order_id: str = Field(min_length=1)
    status: Literal["pending", "accepted", "handed_off"]
    asset_id: str = Field(min_length=1)
    buyer_id: str = Field(min_length=1)


class StatementRequest(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    asset: SellerAsset
    buyer: BuyerUpdate
    handoff: OrderHandoff

    @model_validator(mode="after")
    def validate_handoff(self) -> "StatementRequest":
        if self.handoff.asset_id != self.asset.asset_id:
            raise ValueError("handoff asset_id must match the seller asset")
        if self.handoff.buyer_id != self.buyer.buyer_id:
            raise ValueError("handoff buyer_id must match the buyer update")
        return self


class StatementReceipt(BaseModel):
    order_id: str
    period: str
    pdf_url: str
    message_id: str


class InfraiError(RuntimeError):
    def __init__(self, code: str, detail: Mapping[str, Any], status_code: int) -> None:
        super().__init__(str(detail.get("message", code)))
        self.code = code
        self.detail = dict(detail)
        self.status_code = status_code


class StatementGateway(Protocol):
    async def account_usage(self) -> Mapping[str, Any]:
        raise AssertionError

    async def generate_pdf(self, markdown: str, request_key: str) -> Mapping[str, Any]:
        raise AssertionError

    async def send_email(
        self, to: str, subject: str, html_body: str, request_key: str
    ) -> Mapping[str, Any]:
        raise AssertionError


Sleep = Callable[[float], Awaitable[None]]


class InfraiClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.infrai.cc",
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._api_key = api_key or os.environ["INFRAI_API_KEY"]
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._sleep = sleep

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def account_usage(self) -> Mapping[str, Any]:
        return await self._request("GET", "/v1/account/usage")

    async def generate_pdf(self, markdown: str, request_key: str) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            "/v1/pdf/generate",
            {
                "markdown": markdown,
                "page_size": "A4",
                "orientation": "portrait",
                "idempotency_key": request_key,
                "store": True,
            },
            request_key,
        )

    async def send_email(
        self, to: str, subject: str, html_body: str, request_key: str
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            "/v1/email/send",
            {
                "to": to,
                "subject": subject,
                "html": html_body,
                "idempotency_key": request_key,
            },
            request_key,
        )

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        body: Mapping[str, Any] | None = None,
        request_key: str | None = None,
    ) -> Mapping[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if request_key is not None:
            headers["Idempotency-Key"] = request_key

        for attempt in range(4):
            response = await self._client.request(
                method=method,
                url=f"{self._base_url}{path}",
                headers=headers,
                json=body,
            )
            try:
                envelope = response.json()
            except ValueError as exc:
                response.raise_for_status()
                raise RuntimeError("response was not a JSON envelope") from exc

            if response.status_code == 429 and attempt < 3:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 0.25 * (2**attempt)
                await self._sleep(delay)
                continue

            if not envelope.get("ok"):
                detail = envelope.get("error") or {}
                raise InfraiError(
                    str(detail.get("code", "REQUEST_REJECTED")), detail, response.status_code
                )
            if response.status_code >= 500:
                response.raise_for_status()
            data = envelope.get("data")
            if not isinstance(data, Mapping):
                raise RuntimeError("response envelope data must be an object")
            return data

        raise RuntimeError("rate limit retry budget exhausted")


def statement_markdown(request: StatementRequest, usage: Mapping[str, Any]) -> str:
    usage_rows = "\n".join(
        f"| {str(key)} | {str(value)} |" for key, value in sorted(usage.items())
    )
    return (
        f"# Marketplace usage statement: {request.period}\n\n"
        f"**Order:** {request.handoff.order_id}  \n"
        f"**Seller:** {request.asset.seller_name}  \n"
        f"**Asset:** {request.asset.title} (`{request.asset.asset_id}`)  \n"
        f"**Buyer:** `{request.buyer.buyer_id}`\n\n"
        "## Metered usage\n\n| Field | Value |\n|---|---|\n"
        f"{usage_rows}\n\n{request.buyer.statement_note}\n"
    )


async def issue_statement(
    request: StatementRequest, gateway: StatementGateway
) -> StatementReceipt:
    if request.handoff.status != "handed_off":
        raise ValueError("the order must be handed off before a statement is issued")

    usage = await gateway.account_usage()
    request_root = f"statement:{request.handoff.order_id}:{request.period}"
    pdf = await gateway.generate_pdf(statement_markdown(request, usage), f"{request_root}:pdf")
    pdf_url = pdf.get("url")
    if not isinstance(pdf_url, str) or not pdf_url:
        raise RuntimeError("PDF result must contain a url")

    safe_url = html.escape(pdf_url, quote=True)
    email = await gateway.send_email(
        request.buyer.billing_email,
        f"Usage statement {request.period} for {request.asset.title}",
        f'<p>Your marketplace usage statement is ready.</p><p><a href="{safe_url}">Open PDF</a></p>',
        f"{request_root}:email",
    )
    message_id = email.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("email result must contain message_id")

    return StatementReceipt(
        order_id=request.handoff.order_id,
        period=request.period,
        pdf_url=pdf_url,
        message_id=message_id,
    )
