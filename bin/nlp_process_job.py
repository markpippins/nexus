#!/usr/bin/env python3
"""
Process a rover job with NLP extraction via Ollama (local).

Usage:
    python3 nlp_process_job.py <job_id> [--ollama-url URL] [--model MODEL]

Connects to the rover MCP server, pulls each pending chunk, runs it through
Ollama for structured extraction, and submits the result. The human can
review and override via rover_submit_extraction after the fact.

This is an experimental replacement for the manual chunk-by-chunk loop.
"""
import asyncio
import json
import sys
from mcp import ClientSession
from mcp.client.sse import sse_client

ROVER_URL = "http://localhost:3102/sse"
import os
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = "qwen3:4b"   # will fall back to gemma4 if not available

async def get_chunk(session, job_id, ollama_url="", model=""):
    """Get pending chunk with optional NLP extraction."""
    result = await session.call_tool("rover_get_pending_chunk", {
        "job_id": job_id,
        "ollama_url": ollama_url,
        "model": model,
    })
    return json.loads(result.content[0].text)

async def submit(session, job_id, chunk_index, agenda):
    result = await session.call_tool("rover_submit_extraction", {
        "job_id": job_id,
        "chunk_index": chunk_index,
        "agenda_json": json.dumps(agenda),
    })
    return json.loads(result.content[0].text)

async def main():
    job_id = sys.argv[1] if len(sys.argv) > 1 else "25423376"
    ollama_url = OLLAMA_URL
    model = MODEL

    # Check what models are available on the Ollama host
    import urllib.request
    try:
        tags = json.loads(urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5).read())
        available = [m["name"] for m in tags.get("models", [])]
        print(f"Available on Ollama host: {available}")
        if "qwen3:4b" in available:
            model = "qwen3:4b"
        elif "gemma4:latest" in available:
            model = "gemma4:latest"
        else:
            model = available[0] if available else model
        print(f"Using model: {model}")
    except Exception as e:
        print(f"Ollama check failed: {e}, using {model}")

    async with sse_client(url=ROVER_URL, timeout=30, sse_read_timeout=600) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            processed = 0
            while True:
                data = await get_chunk(session, job_id, ollama_url, model)
                if data.get("done"):
                    print(f"\nAll done! {processed} chunks processed.")
                    break
                if data.get("error"):
                    print(f"Error: {data['error']}")
                    break

                idx = data["chunk_index"]
                remaining = data["pending_count"]
                nlp = data.get("nlp_proposal")
                verdict = data.get("nlp_verdict", "unknown")

                if nlp and verdict == "content":
                    items = nlp.get("agenda_items", [])
                    print(f"[{remaining-1:>2} remaining] Chunk {idx:>2}: NLP says content — {len(items)} candidate(s)")
                    for item in items:
                        print(f"   → {item['title'][:80]}")
                    # Auto-submit the NLP proposal
                    resp = await submit(session, job_id, idx, nlp)
                    print(f"   submitted ✓")
                elif nlp and verdict == "noise":
                    print(f"[{remaining-1:>2} remaining] Chunk {idx:>2}: NLP says noise — skipping")
                    resp = await submit(session, job_id, idx, {"agenda_items": []})
                else:
                    # No NLP or NLP failed — pause for human
                    preview = data["chunk_text"][:200].replace("\n", " | ")
                    print(f"\n[{remaining-1:>2} remaining] Chunk {idx:>2}: NLP unavailable — manual review needed")
                    print(f"   Preview: {preview[:120]}...")
                    print(f"   Submit via: rover_submit_extraction(job_id={job_id}, chunk_index={idx}, agenda_json=...)")
                    break

                processed += 1

                if data.get("pending_count", 1) - 1 == 0:
                    print("\n→ All chunks submitted! Call rover_compile_agenda to write harvest.")

asyncio.run(main())
