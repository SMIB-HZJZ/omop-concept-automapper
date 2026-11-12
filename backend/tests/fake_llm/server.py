"""A tiny OpenAI-compatible server for running the whole stack without an LLM provider.

- /v1/embeddings: deterministic bag-of-words vectors (texts sharing words are similar)
- /v1/chat/completions: answers the translation tool call by echoing the term, and the
  reranker's tool call by picking the candidate with the
  largest word overlap with the input term (confidence 9 for a strong overlap, 6 otherwise);
  any other request gets a plain text reply
- /v1/models and /health/liveliness for the settings page and health checks

Dev only:  docker compose -f docker-compose.dev.yaml --profile fake-llm up -d fake-llm
and set LLM_GATEWAY_URL=http://fake-llm:4000 for backend and worker.
"""

import hashlib
import json
import math
import re
import time

from fastapi import FastAPI, Request

app = FastAPI(title="Fake LLM gateway")
WORD = re.compile(r"[a-z0-9]+")


def embed(text: str, dims: int) -> list[float]:
    vector = [0.0] * dims
    for token in WORD.findall(text.lower()):
        digest = hashlib.md5(token.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % dims] += 1.0 if digest[4] % 2 else -1.0
        vector[int.from_bytes(digest[5:9], "big") % dims] += 0.5
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@app.get("/health/liveliness")
def health():
    return "I'm alive!"


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [{"id": m, "object": "model"} for m in ("fake/chat", "fake/embedder")],
    }


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    dims = int(body.get("dimensions") or 256)
    return {
        "object": "list",
        "model": body.get("model"),
        "data": [
            {"object": "embedding", "index": i, "embedding": embed(t, dims)}
            for i, t in enumerate(inputs)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


def _rerank(user_text: str) -> dict:
    # Prompt format: "Source term: …", optional "English rendering …: …", "<id>: <name> [<class> | …]"
    terms = re.findall(r"^(?:Source term|English rendering[^:]*): (.*)$", user_text, re.MULTILINE)
    term_words = set(WORD.findall(" ".join(terms).lower()))
    best, best_overlap = None, 0
    for line in user_text.splitlines():
        match = re.match(r"\s*(\d+): ([^\[]*)", line)
        if match:
            overlap = len(term_words & set(WORD.findall(match.group(2).lower())))
            if overlap > best_overlap:
                best, best_overlap = int(match.group(1)), overlap
    if best is None:
        return {"most_similar_item_id": None, "confidence_score": None}
    return {"most_similar_item_id": best, "confidence_score": 9 if best_overlap >= 2 else 6}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    user_text = "\n".join(
        m["content"]
        for m in body.get("messages", [])
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    )
    tools = body.get("tools") or []
    message: dict = {"role": "assistant", "content": None}
    finish = "stop"
    if tools:
        name = tools[0]["function"]["name"]
        if name == "RerankerResponse":
            args = _rerank(user_text)
        elif name == "TranslationResponse":
            # No real translation: echo the term (enough to exercise the pipeline)
            term = re.search(r"^Term: (.*)$", user_text, re.MULTILINE)
            args = {"english": term.group(1) if term else "", "language": "en"}
        else:
            args = {}
        message["tool_calls"] = [
            {
                "id": "call_fake",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ]
        finish = "tool_calls"
    else:
        message["content"] = "OK"
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
