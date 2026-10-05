"""
Approach 1: server proxy.

The browser opens a WebSocket to this server. The server holds the Deepgram
API key, opens its own WebSocket to Deepgram, and relays audio back.

    Browser  <--ws-->  this server  <--wss-->  Deepgram

Browser protocol on /ws:
  client -> server  text frame: the text to speak (one utterance per frame)
  server -> client  {"type": "Ready"}    Deepgram connection is open
                    binary frames         linear16 PCM, mono, 24 kHz
                    {"type": "Flushed"}  all audio for the last utterance was sent
                    {"type": "Error", "message": "..."}
"""

import json
import logging
import os
from pathlib import Path

import uvicorn
from deepgram import DeepgramClient, SpeakWebSocketEvents, SpeakWSOptions
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("approach1")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    raise ValueError("DEEPGRAM_API_KEY not found in environment variables")

MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
SAMPLE_RATE = 24000

app = FastAPI()
deepgram = DeepgramClient(DEEPGRAM_API_KEY)


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "approach": "proxy", "model": MODEL}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Client connected")

    # The async SDK client runs on the same event loop as FastAPI, so the
    # handlers below can forward to the browser directly without a queue or
    # cross-thread handoff.
    dg = deepgram.speak.asyncwebsocket.v("1")

    async def on_audio(_client, data, **kwargs):
        await websocket.send_bytes(data)

    async def on_flushed(_client, flushed, **kwargs):
        await websocket.send_text(json.dumps({"type": "Flushed"}))

    async def on_error(_client, error, **kwargs):
        logger.error("Deepgram error: %s", error)
        await websocket.send_text(json.dumps({"type": "Error", "message": str(error)}))

    dg.on(SpeakWebSocketEvents.AudioData, on_audio)
    dg.on(SpeakWebSocketEvents.Flushed, on_flushed)
    dg.on(SpeakWebSocketEvents.Error, on_error)

    options = SpeakWSOptions(model=MODEL, encoding="linear16", sample_rate=SAMPLE_RATE)

    try:
        if not await dg.start(options):
            await websocket.send_text(
                json.dumps({"type": "Error", "message": "Could not connect to Deepgram"})
            )
            await websocket.close()
            return
        await websocket.send_text(json.dumps({"type": "Ready"}))

        while True:
            text = await websocket.receive_text()
            await dg.send_text(text)
            await dg.flush()
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception:
        logger.exception("Proxy error")
    finally:
        await dg.finish()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
