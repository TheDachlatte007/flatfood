from __future__ import annotations

from .base import BaseReceiptParser, ParseContext, ParsedReceipt, ParserError, build_prompt, parse_json_payload, response_schema


class GeminiParser(BaseReceiptParser):
    """Google Gemini (Developer API, API key) with structured JSON output."""

    name = "gemini"

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        if client is None:
            from google import genai  # imported lazily so tests run without network

            client = genai.Client(api_key=api_key)
        self.client = client

    def parse(self, image: bytes, mime_type: str, ctx: ParseContext) -> ParsedReceipt:
        from google.genai import types

        keys = [c["key"] for c in ctx.categories]
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=image, mime_type=mime_type or "image/jpeg"),
                    build_prompt(ctx),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=response_schema(keys),
                    temperature=0,
                ),
            )
        except Exception as exc:
            raise ParserError(f"Gemini-Anfrage fehlgeschlagen: {exc}") from exc
        text = getattr(response, "text", None)
        if not text:
            raise ParserError("Gemini hat keine Antwort geliefert (evtl. Bild unlesbar).")
        return parse_json_payload(text)
