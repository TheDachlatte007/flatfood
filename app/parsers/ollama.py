from __future__ import annotations

import base64

import httpx

from .base import BaseReceiptParser, ParseContext, ParsedReceipt, ParserError, build_prompt, parse_json_payload, response_schema


class OllamaParser(BaseReceiptParser):
    """Local vision model via Ollama (e.g. qwen2.5vl). Optional fallback."""

    name = "ollama"

    def __init__(self, url: str, model: str, transport: httpx.BaseTransport | None = None, timeout: float = 180):
        self.url = url.rstrip("/")
        self.model = model
        self.transport = transport
        self.timeout = timeout

    def parse(self, image: bytes, mime_type: str, ctx: ParseContext) -> ParsedReceipt:
        keys = [c["key"] for c in ctx.categories]
        payload = {
            "model": self.model,
            "stream": False,
            "format": response_schema(keys),
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "user",
                    "content": build_prompt(ctx),
                    "images": [base64.b64encode(image).decode("ascii")],
                }
            ],
        }
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                resp = client.post(f"{self.url}/api/chat", json=payload)
                resp.raise_for_status()
                content = resp.json().get("message", {}).get("content", "")
        except Exception as exc:
            raise ParserError(f"Ollama-Anfrage fehlgeschlagen: {exc}") from exc
        return parse_json_payload(content)
