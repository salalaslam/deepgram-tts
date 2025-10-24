"""
Approach 1: Proxied Audio (Python Relay)
WebSocket server that relays audio from Deepgram TTS to browser clients.
Browser -> Python Backend -> Deepgram -> Python Backend -> Browser
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import logging
import asyncio
import os
from dotenv import load_dotenv
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import SpeakV1SocketClientResponse, SpeakV1TextMessage, SpeakV1ControlMessage

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get Deepgram API key
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    raise ValueError("DEEPGRAM_API_KEY not found in environment variables")

app = FastAPI()


@app.get("/")
async def get():
    """Serve the main HTML file"""
    return FileResponse("index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint that:
    1. Accepts browser connection
    2. Receives text from browser
    3. Connects to Deepgram TTS WebSocket
    4. Relays audio chunks from Deepgram to browser
    """
    await websocket.accept()
    logger.info("Client connected")

    dg_connection = None
    audio_queue = asyncio.Queue()
    is_connected = True

    async def audio_sender():
        """Background task to send audio chunks to browser"""
        try:
            while is_connected:
                try:
                    data = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                    await websocket.send_bytes(data)
                    logger.debug(f"Sent {len(data)} bytes to browser")
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error in audio_sender: {e}")
                    break
        except Exception as e:
            logger.error(f"Audio sender task error: {e}")

    try:
        # Initialize Deepgram client
        deepgram = DeepgramClient(api_key=DEEPGRAM_API_KEY)

        # Connect to Deepgram Speak WebSocket using v5 API
        async with deepgram.speak.v1.connect(
            model="aura-asteria-en",
            encoding="linear16",
            sample_rate=24000
        ) as dg_connection:

            # Event handler for messages from Deepgram
            def on_message(message: SpeakV1SocketClientResponse) -> None:
                """Receive audio chunks or metadata from Deepgram"""
                if isinstance(message, bytes):
                    # Audio data received
                    if is_connected:
                        try:
                            audio_queue.put_nowait(message)
                            logger.debug(f"Queued {len(message)} bytes of audio")
                        except Exception as e:
                            logger.error(f"Error queuing audio: {e}")
                else:
                    # Metadata or control message
                    msg_type = getattr(message, "type", "Unknown")
                    logger.info(f"Received {msg_type} event from Deepgram")

            # Register event handlers
            dg_connection.on(EventType.OPEN, lambda _: logger.info("Deepgram connection opened"))
            dg_connection.on(EventType.MESSAGE, on_message)
            dg_connection.on(EventType.CLOSE, lambda _: logger.info("Deepgram connection closed"))
            dg_connection.on(EventType.ERROR, lambda error: logger.error(f"Deepgram error: {error}"))

            # Start listening to Deepgram
            await dg_connection.start_listening()
            logger.info("Deepgram connection started, waiting for text from client...")

            # Start background audio sender task
            sender_task = asyncio.create_task(audio_sender())

            # Wait for text messages from browser
            while is_connected:
                try:
                    data = await websocket.receive_text()
                    logger.info(f"Received text from client: {data}")

                    # Send text to Deepgram for TTS
                    await dg_connection.send_text(SpeakV1TextMessage(text=data))
                    logger.info("Text sent to Deepgram")

                    # Flush to ensure all audio is sent
                    await dg_connection.send_control(SpeakV1ControlMessage(type="Flush"))

                except WebSocketDisconnect:
                    logger.info("Client disconnected")
                    is_connected = False
                    break
                except Exception as e:
                    logger.error(f"Error receiving/processing text: {e}")
                    is_connected = False
                    break

            # Send close control message to Deepgram
            await dg_connection.send_control(SpeakV1ControlMessage(type="Close"))

    except Exception as e:
        logger.error(f"Error in websocket_endpoint: {e}")
        is_connected = False
    finally:
        is_connected = False
        logger.info("WebSocket connection cleanup complete")


if __name__ == "__main__":
    logger.info("Starting server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
