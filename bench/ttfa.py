"""
Benchmark: time to first audio byte (TTFA) and total synthesis time for

  proxy   client -> server_approach1 (/ws) -> Deepgram
  direct  client -> server_approach2 (/api/token), then client -> Deepgram

The client here is Python (websockets/httpx), speaking the same protocol the
browser pages speak. It measures bytes on the wire, not browser decode or
playback.

Usage (from the repo root, with .env containing DEEPGRAM_API_KEY):

    python bench/ttfa.py --runs 20 --location "Pakistan"

By default the script starts both servers itself on 127.0.0.1:8000/8001.
Raw per-run results are written to bench/results/<date>.json and a markdown
summary is printed.
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import random
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEEPGRAM_WS = "wss://api.deepgram.com/v1/speak"
SAMPLE_RATE = 24000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # linear16 mono

TEXTS = {
    "short": "Hello! Your order has shipped and should arrive on Friday.",
    "medium": (
        "Thanks for calling. I can help you reset your password, check the status "
        "of an order, or update your billing details. If you already have a ticket "
        "number, say it now, or tell me in a few words what you need help with today."
    ),
    "long": (
        "Streaming text to speech matters most when a person is waiting on the other "
        "end. In a voice assistant, every hundred milliseconds of silence before the "
        "first word makes the system feel slower, even if the full answer arrives at "
        "the same time. That is why this benchmark separates two numbers. The first "
        "is the time until the first audio byte arrives, which is roughly when "
        "playback can start. The second is the time until the last byte arrives, "
        "which tells you how long the whole utterance took to synthesize and "
        "transfer. Network distance to the speech provider affects both, and so does "
        "where your own server sits relative to your users and to the provider."
    ),
}


def now() -> float:
    return time.perf_counter()


def ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


async def receive_until_flushed(ws, t_send: float) -> dict:
    first = None
    n_bytes = 0
    async for msg in ws:
        if isinstance(msg, bytes):
            if first is None and len(msg) > 0:
                first = now()
            n_bytes += len(msg)
            continue
        data = json.loads(msg)
        if data.get("type") == "Flushed":
            done = now()
            break
        if data.get("type") == "Error":
            raise RuntimeError(f"server error: {data}")
    else:
        raise RuntimeError("connection closed before Flushed")
    if first is None:
        raise RuntimeError("no audio received")
    return {
        "t_first": first,
        "ttfa_ms": ms(first - t_send),
        "total_ms": ms(done - t_send),
        "audio_bytes": n_bytes,
        "audio_s": round(n_bytes / BYTES_PER_SECOND, 3),
    }


async def deepgram_peers_of(pid: int) -> list:
    """Remote IPs of a process's established :443 connections (best effort).

    The proxy server picks its Deepgram edge through DNS, same as the direct
    path. Recording which edge each run hit makes the comparison checkable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lsof", "-a", "-p", str(pid), "-iTCP:443", "-sTCP:ESTABLISHED", "-n", "-P", "-Fn",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
    except FileNotFoundError:
        return []
    return sorted({line.split("->")[1].rsplit(":", 1)[0]
                   for line in out.decode().splitlines() if "->" in line})


async def run_proxy(proxy_ws: str, text: str, server_pid=None) -> dict:
    t0 = now()
    async with websockets.connect(proxy_ws, max_size=None) as ws:
        t_open = now()
        msg = json.loads(await ws.recv())
        if msg.get("type") != "Ready":
            raise RuntimeError(f"expected Ready, got {msg}")
        t_ready = now()
        t_send = now()
        await ws.send(text)
        r = await receive_until_flushed(ws, t_send)
        # Timed section is over; look up which Deepgram edge the server used.
        peers = await deepgram_peers_of(server_pid) if server_pid else []
    return {
        "ws_open_ms": ms(t_open - t0),
        "connect_ms": ms(t_ready - t0),  # includes the server's own Deepgram handshake
        "ttfa_ms": r["ttfa_ms"],
        "total_ms": r["total_ms"],
        "cold_ttfa_ms": ms(r["t_first"] - t0),
        "audio_bytes": r["audio_bytes"],
        "audio_s": r["audio_s"],
        "peer_ip": ",".join(peers) or None,
    }


async def run_direct(http: httpx.AsyncClient, token_url: str, model: str, text: str,
                     key_fallback: bool) -> dict:
    t0 = now()
    resp = await http.get(token_url)
    t_minted = now()
    mint_ok = resp.status_code == 200
    if mint_ok:
        body = resp.json()
        subprotocols = ["bearer", body["access_token"]]
        model = body.get("model", model)
    elif key_fallback:
        # Same handshake to the same host, authenticated with the API key
        # instead of a JWT. Only acceptable here because this script already
        # runs server-side with the key; never do this in a browser.
        subprotocols = ["token", os.environ["DEEPGRAM_API_KEY"]]
    else:
        raise RuntimeError(
            f"token endpoint returned {resp.status_code}: {resp.text}. "
            "Use a Member-role key, or pass --key-fallback to time the grant "
            "round trip and connect with the API key instead."
        )

    url = f"{DEEPGRAM_WS}?model={model}&encoding=linear16&sample_rate={SAMPLE_RATE}"
    t_c0 = now()
    async with websockets.connect(url, subprotocols=subprotocols, max_size=None) as ws:
        t_open = now()
        peer = ws.transport.get_extra_info("peername")
        t_send = now()
        await ws.send(json.dumps({"type": "Speak", "text": text}))
        await ws.send(json.dumps({"type": "Flush"}))
        r = await receive_until_flushed(ws, t_send)
        await ws.send(json.dumps({"type": "Close"}))
    return {
        "mint_ms": ms(t_minted - t0),
        "mint_ok": mint_ok,
        "connect_ms": ms(t_open - t_c0),
        "ttfa_ms": r["ttfa_ms"],
        "total_ms": r["total_ms"],
        "cold_ttfa_preminted_ms": ms(r["t_first"] - t_c0),
        "cold_ttfa_ms": ms(r["t_first"] - t0),
        "audio_bytes": r["audio_bytes"],
        "audio_s": r["audio_s"],
        "peer_ip": peer[0] if peer else None,
    }


def pct(values, p):
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    # Linear interpolation between closest ranks (statistics "inclusive").
    return round(statistics.quantiles(values, n=100, method="inclusive")[p - 1], 0)


def fmt(values):
    if not values:
        return "n/a"
    return f"{pct(values, 50):.0f} / {pct(values, 95):.0f}"


def summarize(runs: list, texts: dict) -> str:
    ok = [r for r in runs if "error" not in r]

    def vals(path, field, text=None):
        return [r[field] for r in ok
                if r["path"] == path and (text is None or r["text"] == text)]

    out = []
    out.append("All times in ms, shown as p50 / p95.\n")
    out.append("Time to first audio byte after sending text (connection already open):\n")
    out.append("| Text | Chars | Proxy | Direct |")
    out.append("|---|---:|---:|---:|")
    for name, text in texts.items():
        out.append(f"| {name} | {len(text)} | {fmt(vals('proxy', 'ttfa_ms', name))} "
                   f"| {fmt(vals('direct', 'ttfa_ms', name))} |")

    out.append("\nTotal synthesis time, send to last byte (Flushed):\n")
    out.append("| Text | Audio length (s) | Proxy | Direct |")
    out.append("|---|---:|---:|---:|")
    for name in texts:
        audio = vals("direct", "audio_s", name) or vals("proxy", "audio_s", name)
        a = f"{statistics.median(audio):.1f}" if audio else "n/a"
        out.append(f"| {name} | {a} | {fmt(vals('proxy', 'total_ms', name))} "
                   f"| {fmt(vals('direct', 'total_ms', name))} |")

    out.append("\nCold start, nothing connected yet (all texts pooled for the setup steps):\n")
    out.append("| Step | Proxy | Direct |")
    out.append("|---|---:|---:|")
    out.append(f"| Mint token (client -> token server -> Deepgram grant) | - | {fmt(vals('direct', 'mint_ms'))} |")
    out.append(f"| Open connection ready to speak | {fmt(vals('proxy', 'connect_ms'))} | {fmt(vals('direct', 'connect_ms'))} |")
    for name in texts:
        out.append(f"| First audio, {name} text, pre-minted token | {fmt(vals('proxy', 'cold_ttfa_ms', name))} "
                   f"| {fmt(vals('direct', 'cold_ttfa_preminted_ms', name))} |")
    for name in texts:
        out.append(f"| First audio, {name} text, minting on demand | - "
                   f"| {fmt(vals('direct', 'cold_ttfa_ms', name))} |")

    errors = [r for r in runs if "error" in r]
    out.append(f"\nRuns: {len(ok)} ok, {len(errors)} failed.")
    for path in ("proxy", "direct"):
        edges = {}
        for r in ok:
            if r["path"] == path:
                edges.setdefault(r.get("peer_ip") or "unknown", []).append(r["ttfa_ms"])
        desc = ", ".join(f"{ip} x{len(v)} (TTFA p50 {statistics.median(v):.0f})"
                         for ip, v in sorted(edges.items(), key=lambda kv: -len(kv[1])))
        out.append(f"Deepgram edges, {path}: {desc}")
    return "\n".join(out)


def wait_healthy(url: str, timeout: float = 20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"server at {url} did not become healthy")


def spawn_servers(proxy_port: int, token_port: int):
    procs = []
    for module, port in (("server_approach1", proxy_port), ("server_approach2", token_port)):
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", f"{module}:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
    wait_healthy(f"http://127.0.0.1:{proxy_port}/api/health")
    wait_healthy(f"http://127.0.0.1:{token_port}/api/health")
    return procs


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=20, help="measured runs per path per text")
    ap.add_argument("--warmup", type=int, default=1, help="unrecorded runs per path first")
    ap.add_argument("--texts", default="short,medium,long")
    ap.add_argument("--proxy-port", type=int, default=8000)
    ap.add_argument("--token-port", type=int, default=8001)
    ap.add_argument("--no-spawn", action="store_true", help="use already running servers")
    ap.add_argument("--key-fallback", action="store_true",
                    help="if minting fails, still time the grant round trip, then "
                         "connect with the API key")
    ap.add_argument("--pause", type=float, default=0.3, help="seconds between runs")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--location", default="", help="free text, stored with results")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
    texts = {k: TEXTS[k] for k in args.texts.split(",")}
    proxy_ws = f"ws://127.0.0.1:{args.proxy_port}/ws"
    token_url = f"http://127.0.0.1:{args.token_port}/api/token"

    procs = [] if args.no_spawn else spawn_servers(args.proxy_port, args.token_port)
    proxy_pid = procs[0].pid if procs else None
    rng = random.Random(args.seed)
    runs = []
    started = dt.datetime.now(dt.timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            async def one(path, name, record):
                try:
                    if path == "proxy":
                        r = await asyncio.wait_for(run_proxy(proxy_ws, texts[name], proxy_pid), 60)
                    else:
                        r = await asyncio.wait_for(
                            run_direct(http, token_url, model, texts[name], args.key_fallback), 60)
                except Exception as e:  # recorded, excluded from stats
                    r = {"error": f"{type(e).__name__}: {e}"}
                    print(f"  {path} {name}: {r['error']}", file=sys.stderr)
                if record:
                    r.update(path=path, text=name)
                    runs.append(r)
                await asyncio.sleep(args.pause)

            for _ in range(args.warmup):
                for path in ("proxy", "direct"):
                    await one(path, next(iter(texts)), record=False)

            for i in range(args.runs):
                for name in texts:
                    order = ["proxy", "direct"]
                    rng.shuffle(order)
                    for path in order:
                        await one(path, name, record=True)
                print(f"iteration {i + 1}/{args.runs} done", file=sys.stderr)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait()

    summary = summarize(runs, texts)
    print(summary)

    meta = {
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "location": args.location,
        "model": model,
        "runs_per_path_per_text": args.runs,
        "warmup": args.warmup,
        "seed": args.seed,
        "key_fallback_used": any(r.get("mint_ok") is False for r in runs),
        "texts": {k: {"chars": len(v), "text": v} for k, v in texts.items()},
        "characters_synthesized_recorded": sum(len(texts[r["text"]]) for r in runs if "error" not in r),
        "deepgram_ips_seen": sorted({r["peer_ip"] for r in runs if r.get("peer_ip")}),
        "dns_at_end": sorted({a[4][0] for a in socket.getaddrinfo("api.deepgram.com", 443, type=socket.SOCK_STREAM)}),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "websockets": websockets.__version__,
    }
    out = Path(args.out) if args.out else ROOT / "bench" / "results" / f"{started.date()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "runs": runs}, indent=2) + "\n")
    print(f"\nRaw results: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
