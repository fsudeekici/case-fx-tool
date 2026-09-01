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

UPSTREAM_BASE = os.environ.get("FX_UPSTREAM_BASE", "https://api.frankfurter.dev")
PORT = int(os.environ.get("PORT", "8080"))

app = FastAPI(title="fx-tool", version="0.1")

client = httpx.AsyncClient(base_url=UPSTREAM_BASE, timeout=5.0)


@app.get("/tools/convert")
async def convert(
    amount: float = Query(...),
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    date_param: date | None = Query(None, alias="date"),
):
    path = date_param.isoformat() if date_param else "latest"
    response = await client.get(f"/v1/{path}", params={"base": from_.upper(), "symbols": to.upper()})
    payload = response.json()
    rate = payload["rates"][to.upper()]
    return {
        "amount": amount,
        "from": from_.upper(),
        "to": to.upper(),
        "rate": rate,
        "result": round(amount * rate, 2),
        "rate_date": payload.get("date"),
        "source": "ECB via frankfurter.dev",
    }


@app.get("/health")
async def health() -> dict:
    return {"ok": True}