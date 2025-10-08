we need to create a htmls/js file which can play/stream tts audio coming from deepgram via python backend (single file).

we need to test two implementations for the above requirement.

1. Deepgram’s TTS supports real-time streaming over WebSockets. A Python backend can open a Deepgram TTS WebSocket, relay the incoming audio frames to your browser over your own WebSocket (or HTTP chunked/MSE), and your HTML/JS app can play them as they arrive.
2. Use a short-lived Deepgram access token minted by your Python backend. The browser asks your backend for a token, then connects directly to Deepgram (WebSocket for real-time TTS, or REST for single-shot), authenticating with that token. Your server is only in the signaling/auth path; audio streams go browser ↔ Deepgram thereafter.
