"""
Approach 2: Direct Connection (Short-lived Token)
Python backend provides API token to browser.
Browser connects directly to Deepgram for TTS.
Browser -> Python Backend (token only) -> Browser -> Deepgram (direct audio)
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging
import os
from dotenv import load_dotenv

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

# Add CORS middleware to allow browser requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def get():
    """Serve the main HTML file"""
    return FileResponse("index_approach2.html")


@app.get("/api/token")
async def get_token():
    """
    Endpoint to provide Deepgram API token to the browser.

    In production, you should:
    1. Use Deepgram's temporary key creation API
    2. Set expiration time on the key
    3. Implement rate limiting
    4. Add authentication/authorization
    """
    try:
        logger.info("Token requested by client")

        # For demo purposes, we're sending the main API key
        # In production, create a temporary key with limited permissions
        response = {
            "token": DEEPGRAM_API_KEY,
            "message": "Token retrieved successfully"
        }

        return JSONResponse(content=response)

    except Exception as e:
        logger.error(f"Error providing token: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve token")


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "approach": "direct-connection"}


if __name__ == "__main__":
    logger.info("Starting Approach #2 server on http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)
