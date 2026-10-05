"""
Approach 2: direct connection with a short-lived token.

This server never touches audio. It exchanges the long-lived Deepgram API key
for a short-lived JWT (Deepgram's /v1/auth/grant, default TTL 30 s) and hands
that to the browser. The browser then opens its own WebSocket to Deepgram.

    Browser  --GET /api/token-->  this server  --POST /v1/auth/grant-->  Deepgram
    Browser  <--------------------- wss (bearer JWT) --------------------->  Deepgram

The JWT only has to be valid when the WebSocket opens; an open connection keeps
working after the token expires.

The API key must have at least the Member role to mint tokens. Default
(usage-only) keys get 403 from /v1/auth/grant.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("approach2")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    raise ValueError("DEEPGRAM_API_KEY not found in environment variables")

MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
TOKEN_TTL_SECONDS = int(os.getenv("DEEPGRAM_TOKEN_TTL_SECONDS", "30"))
GRANT_URL = "https://api.deepgram.com/v1/auth/grant"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared client so the TLS connection to Deepgram is reused between
    # mints. A fresh TLS handshake costs several round trips.
    app.state.http = httpx.AsyncClient(
        timeout=10, headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    )
    yield
    await app.state.http.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(HERE / "index_approach2.html")


@app.get("/wav")
async def index_wav():
    return FileResponse(HERE / "index_approach2_wav.html")


@app.get("/api/token")
async def get_token():
    """
    Mint a short-lived Deepgram token for the browser.

    In a real app this endpoint must sit behind your own user authentication
    and rate limiting: anyone who can call it can spend your Deepgram credit
    for as long as the tokens they mint stay valid.
    """
    try:
        r = await app.state.http.post(GRANT_URL, json={"ttl_seconds": TOKEN_TTL_SECONDS})
    except httpx.HTTPError as e:
        logger.error("Token grant request failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not reach Deepgram")

    if r.status_code != 200:
        logger.error("Token grant rejected: %s %s", r.status_code, r.text)
        detail = "Deepgram refused to mint a token"
        if r.status_code == 403:
            detail += " (the API key needs at least the Member role)"
        raise HTTPException(status_code=502, detail=detail)

    body = r.json()
    return {
        "access_token": body["access_token"],
        "expires_in": body.get("expires_in", TOKEN_TTL_SECONDS),
        "model": MODEL,
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "approach": "direct", "model": MODEL}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
