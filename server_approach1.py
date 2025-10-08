"""
Approach 1: Proxied Audio (Python Relay)
WebSocket server that relays audio from Deepgram TTS to browser clients.
Browser -> Python Backend -> Deepgram -> Python Backend -> Browser
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn
import logging
import asyncio
import os
from dotenv import load_dotenv
from deepgram import DeepgramClient, SpeakWebSocketEvents, SpeakWSOptions

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
    """Serve a simple test page"""
    return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>WebSocket Test</title>
        </head>
        <body>
            <h1>WebSocket Connection Test</h1>
            <div id="status">Disconnected</div>
            <button onclick="connect()">Connect</button>
            <button onclick="disconnect()">Disconnect</button>
            <button onclick="sendMessage()">Send Test Message</button>
            <div id="messages"></div>

            <script>
                let ws = null;

                function connect() {
                    ws = new WebSocket('ws://localhost:8000/ws');

                    ws.onopen = function(event) {
                        document.getElementById('status').textContent = 'Connected';
                        document.getElementById('status').style.color = 'green';
                        addMessage('Connected to server');
                    };

                    ws.onmessage = function(event) {
                        addMessage('Server: ' + event.data);
                    };

                    ws.onclose = function(event) {
                        document.getElementById('status').textContent = 'Disconnected';
                        document.getElementById('status').style.color = 'red';
                        addMessage('Disconnected from server');
                    };

                    ws.onerror = function(error) {
                        addMessage('Error: ' + error);
                    };
                }

                function disconnect() {
                    if (ws) {
                        ws.close();
                        ws = null;
                    }
                }

                function sendMessage() {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send('Hello from browser!');
                        addMessage('You: Hello from browser!');
                    } else {
                        addMessage('Not connected!');
                    }
                }

                function addMessage(message) {
                    const messagesDiv = document.getElementById('messages');
                    const messageElement = document.createElement('div');
                    messageElement.textContent = message;
                    messagesDiv.appendChild(messageElement);
                }
            </script>
        </body>
        </html>
    """)


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

    try:
        # Initialize Deepgram client
        deepgram = DeepgramClient(DEEPGRAM_API_KEY)
        dg_connection = deepgram.speak.websocket.v("1")

        # Flag to track if we're still connected
        is_connected = True

        # Event handlers for Deepgram connection
        def on_open(self, open_event, **kwargs):
            logger.info("Deepgram connection opened")

        def on_binary_data(self, data, **kwargs):
            """Receive audio chunks from Deepgram and send to browser"""
            if is_connected:
                try:
                    # Send binary audio data to browser
                    asyncio.create_task(websocket.send_bytes(data))
                    logger.debug(f"Relaying {len(data)} bytes to browser")
                except Exception as e:
                    logger.error(f"Error sending audio to browser: {e}")

        def on_metadata(self, metadata, **kwargs):
            logger.info(f"Deepgram metadata: {metadata}")

        def on_flush(self, flushed, **kwargs):
            logger.info("Deepgram flush event received")

        def on_close(self, close_event, **kwargs):
            logger.info("Deepgram connection closed")

        def on_error(self, error, **kwargs):
            logger.error(f"Deepgram error: {error}")

        # Register event handlers
        dg_connection.on(SpeakWebSocketEvents.Open, on_open)
        dg_connection.on(SpeakWebSocketEvents.AudioData, on_binary_data)
        dg_connection.on(SpeakWebSocketEvents.Metadata, on_metadata)
        dg_connection.on(SpeakWebSocketEvents.Flushed, on_flush)
        dg_connection.on(SpeakWebSocketEvents.Close, on_close)
        dg_connection.on(SpeakWebSocketEvents.Error, on_error)

        # Configure TTS options
        options = SpeakWSOptions(
            model="aura-asteria-en",  # Deepgram voice model
            encoding="linear16",       # Audio encoding format
            sample_rate=24000          # Sample rate in Hz
        )

        # Start Deepgram connection
        if not dg_connection.start(options):
            logger.error("Failed to start Deepgram connection")
            await websocket.close()
            return

        logger.info("Deepgram connection started, waiting for text from client...")

        # Wait for text messages from browser
        while True:
            data = await websocket.receive_text()
            logger.info(f"Received text from client: {data}")

            # Send text to Deepgram for TTS
            dg_connection.send_text(data)
            logger.info("Text sent to Deepgram")

            # Flush to ensure all audio is sent
            dg_connection.flush()

    except WebSocketDisconnect:
        logger.info("Client disconnected")
        is_connected = False
    except Exception as e:
        logger.error(f"Error in websocket_endpoint: {e}")
        is_connected = False
    finally:
        # Clean up Deepgram connection
        if dg_connection:
            try:
                dg_connection.finish()
                logger.info("Deepgram connection closed")
            except Exception as e:
                logger.error(f"Error closing Deepgram connection: {e}")


if __name__ == "__main__":
    logger.info("Starting server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
