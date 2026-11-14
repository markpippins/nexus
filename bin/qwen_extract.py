#!/usr/bin/env python3
"""
Direct Ollama HTTP API extractor using Qwen3.5.

Usage:
    python3 qwen_extract.py --input /path/to/transcript.md --output /path/to/harvest.md

Skips the HTML→markdown step — feed it pre-converted markdown.
Chunks via langchain, calls Qwen via HTTP, compiles structured candidates.
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from schemas import SpecificationAgenda, SpecificationCandidate

LOG_DIR = Path("/home/codex/dev/nexus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "qwen_extract.log"),
    ])
log = logging.getLogger("qwen-extract")

import os
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"
MODEL = "qwen2.5-coder:latest"  # 7.6B code-optimized

CODE_HARVESTER_PROMPT = """You are an advanced Software Archaeologist and Technical Analyst. Your primary mission is to extract actionable engineering intent and harvest implementable code blocks from unstructured developer chat transcripts.

Follow these execution guidelines closely:
1. Exact Code Extraction: If a participant shares code, scripts, configurations, or schemas, extract it word-for-word. Never truncate code with placeholders like '// ... rest of code'.
2. Code Contextualization: Link the code to its corresponding "Specification Candidate." Do not leave code blocks floating without their intent explanation.
3. Code Version Tracking: If a code snippet is updated or refactored later in the chat, capture the final corrected version as the primary asset, and note the change in implementation notes.
4. Separate Discussion from Code: Ensure conversational text surrounding the code blocks remains in the intent descriptions, while code objects contain only valid, executable script syntax."""


def chunk_text(text: str, chunk_size: int = 20000, overlap: int = 2000) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=overlap, length_function=len,
    )
    return splitter.split_text(text)


def call_ollama(chunk: str, schema_json: dict) -> dict | None:
    """Call Ollama HTTP API with structured output format."""
    import requests

    estimated_tokens = len(chunk) // 4
    target_ctx = max(8192, min(65536, estimated_tokens + 4096))

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": CODE_HARVESTER_PROMPT},
            {"role": "user", "content": f"Analyze this chat log and harvest all architectural specifications and code blocks:\n\n{chunk}"},
        ],
        "format": schema_json,
        "options": {
            "num_ctx": target_ctx,
            "temperature": 0.1,
            "num_thread": 3,
        },
        "stream": False,
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
        content = data["message"]["content"]
        return json.loads(content)
    except Exception as e:
        log.error("Ollama call failed: %s", e)
        return None


def agenda_to_markdown(items: list[dict]) -> str:
    md = []
    for idx, item in enumerate(items, 1):
        md.append(f"## {idx}. {item['title']}")
        md.append(f"**Status:** `{item['status']}`\n")
        md.append(f"### Architectural Intent\n{item['intent_description']}\n")

        if item.get("requirements"):
            md.append("### Requirements & Acceptance Criteria")
            for req in item["requirements"]:
                md.append(f"- [ ] {req}")
            md.append("")

        if item.get("code_snippets"):
            md.append("### Harvested Code Artifacts")
            for code in item["code_snippets"]:
                md.append(f"#### Purpose: {code['purpose']}")
                md.append(f"```{code['language']}")
                md.append(code["raw_code"].strip())
                md.append("```\n")

        if item.get("open_questions"):
            md.append("### Unresolved Follow-Ups")
            for q in item["open_questions"]:
                md.append(f"- {q}")
            md.append("")

        md.append("---\n")
    return "\n".join(md)


def run():
    parser = argparse.ArgumentParser(description="Qwen3.5 extraction from markdown")
    parser.add_argument("--input", required=True, help="Path to input markdown file")
    parser.add_argument("--output", required=True, help="Path to output harvest markdown")
    args = parser.parse_args()

    schema = SpecificationAgenda.model_json_schema()
    text = Path(args.input).read_text(encoding="utf-8")
    chunks = chunk_text(text)
    log.info("Split into %d chunks", len(chunks))

    all_items = []
    failures = 0
    output_path = Path(args.output)

    # Recover any already-saved progress
    chunk_results_path = output_path.with_suffix(".partial.json")
    if chunk_results_path.exists():
        with open(chunk_results_path) as f:
            all_items = json.load(f)
        log.info("Recovered %d items from partial output", len(all_items))

    for i, chunk in enumerate(chunks):
        if i < len(all_items) // 10:  # crude skip: already processed chunks
            continue
        log.info("Chunk %d/%d — calling %s...", i + 1, len(chunks), MODEL)
        result = call_ollama(chunk, schema)
        if result and result.get("agenda_items"):
            all_items.extend(result["agenda_items"])
            log.info("  → %d items extracted (total: %d)", len(result["agenda_items"]), len(all_items))
            # Save progress incrementally
            with open(chunk_results_path, "w") as f:
                json.dump(all_items, f, indent=2)
        else:
            failures += 1
            log.warning("  → extraction failed or empty")

    # Write output
    md_lines = [
        "# Harvested Specification & Code Repository\n",
        f"**Source:** `{args.input}`\n",
        f"**Model:** {MODEL}\n",
        f"**Chunks processed:** {len(chunks)}  **Failed:** {failures}\n",
        f"**Total candidates:** {len(all_items)}\n",
        "---\n",
        agenda_to_markdown(all_items),
    ]

    output = "\n".join(md_lines)
    output_path.write_text(output)
    log.info("Wrote %s (%d items, %d failures)", args.output, len(all_items), failures)
    # Clean up partial data on success
    if failures == 0 and chunk_results_path.exists():
        chunk_results_path.unlink()
    return 1 if failures == len(chunks) else 0


if __name__ == "__main__":
    sys.exit(run())
