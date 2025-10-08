# Deepgram Text-to-Speech WebSocket Implementations

This project demonstrates two different approaches for streaming Deepgram TTS audio in a web browser.

## Approaches

### Approach #1: Proxied Audio Relay
**Files:** `server_approach1.py`, `index.html`

The Python backend acts as a proxy between the browser and Deepgram:
- Browser connects to Python server via WebSocket
- Python server connects to Deepgram TTS API
- Audio chunks are relayed: Browser ↔ Python ↔ Deepgram
- Uses asyncio queue pattern to bridge synchronous Deepgram callbacks with async FastAPI WebSocket

**Pros:**
- Full server control over audio stream
- API key stays secure on server
- Can add server-side processing/logging

**Cons:**
- Additional latency through proxy
- More server resources required

### Approach #2: Direct Connection with Token
**Files:** `server_approach2.py`, `index_approach2.html`, `index_approach2_mse.html`

The browser connects directly to Deepgram after obtaining a token:
- Browser requests token from Python backend
- Browser establishes WebSocket directly to Deepgram using token
- Audio streams: Browser ↔ Deepgram (no proxy)
- Python server only provides token endpoint

**Pros:**
- Lower latency (direct connection)
- Less server load
- Scalable architecture

**Cons:**
- Token management required
- Less server control over audio stream

## Setup

1. **Install dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure API key:**
   ```bash
   cp .env.example .env
   # Edit .env and add your DEEPGRAM_API_KEY
   ```

## Running

### Approach #1 (Proxied)
```bash
# Start server (port 8000)
python server_approach1.py

# Open in browser
open index.html
```

### Approach #2 (Direct Connection)
```bash
# Start token server (port 8001)
python server_approach2.py

# Open in browser
open index_approach2.html  # Web Audio API version
# OR
open index_approach2_mse.html  # MediaSource Extensions version
```

## Technical Details

- **Audio Format:** Linear16 PCM, 24kHz, Mono
- **Model:** aura-asteria-en
- **WebSocket Protocol:** Deepgram Speak WebSocket API
- **Playback Strategy:** Collects all audio chunks, plays once complete (prevents cutoff)
- **Python Framework:** FastAPI with uvicorn
- **SDK:** deepgram-sdk 5.0.0

## Files

- `server_approach1.py` - Proxied relay server with asyncio queue
- `server_approach2.py` - Token endpoint server with CORS
- `index.html` - Client for Approach #1 (purple theme)
- `index_approach2.html` - Direct connection client with Web Audio API (pink theme)
- `index_approach2_mse.html` - Direct connection client using MediaSource Extensions (green theme)
- `requirements.txt` - Python dependencies with frozen versions
- `.env.example` - Environment variable template

## Notes

- Both approaches collect all audio chunks before playing to ensure complete playback
- 500ms timeout after last chunk to trigger playback
- Web Audio API decodes 16-bit PCM to Float32 for browser playback
- CORS configured for localhost developmentd to create a htmls/js file which can play/stream tts audio coming from deepgram via python backend (single file).

