"""Currency conversion tool for the agent runtime.

Wraps the public ECB feed at frankfurter.dev so an agent can answer questions
like "how much is 250 EUR in TRY".

Run:  ./run.sh   (or: uvicorn main:app --reload)
"""

from __future__ import annotations

import os
from datetime import date

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

UPSTREAM_BASE = os.environ.get("FX_UPSTREAM_BASE", "https://api.frankfurter.dev")
PORT = int(os.environ.get("PORT", "8080"))

app = FastAPI(title="fx-tool", version="0.1")

client = httpx.AsyncClient(base_url=UPSTREAM_BASE, timeout=5.0)


def error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code, "message": message})


def is_valid_amount(amount: float) -> bool:
    if amount <= 0:
        return False
    # Reject amounts with more than 2 decimal places (e.g. 10-decimal
    # floating point noise). Currency amounts realistically don't need more.
    cents = round(amount * 100)
    return abs(amount * 100 - cents) < 1e-6


def is_valid_currency_code(code: str) -> bool:
    return len(code) == 3 and code.isalpha() and code.isupper()


@app.get("/tools/convert")
async def convert(
    amount: float = Query(...),
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    date_param: date | None = Query(None, alias="date"),
):
    from_code = from_.upper()
    to_code = to.upper()

    if not is_valid_amount(amount):
        return error(
            400,
            "invalid_amount",
            "Amount must be a positive number with at most 2 decimal places.",
        )

    if not is_valid_currency_code(from_code) or not is_valid_currency_code(to_code):
        return error(
            400,
            "invalid_currency",
            "Currency codes must be 3-letter ISO codes, e.g. EUR, TRY.",
        )

    path = date_param.isoformat() if date_param else "latest"
    response = await client.get(f"/v1/{path}", params={"base": from_code, "symbols": to_code})
    payload = response.json()
    rate = payload["rates"][to_code]
    return {
        "amount": amount,
        "from": from_code,
        "to": to_code,
        "rate": rate,
        "result": round(amount * rate, 2),
        "rate_date": payload.get("date"),
        "source": "ECB via frankfurter.dev",
    }


@app.get("/health")
async def health() -> dict:
    return {"ok": True}