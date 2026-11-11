from __future__ import annotations

from ..config import Settings
from .base import BaseReceiptParser, ParseContext, ParsedItem, ParsedReceipt, ParserError


def make_parser(settings: Settings) -> BaseReceiptParser | None:
    """Select parser from PARSER env: auto | gemini | ollama | mock | none."""
    choice = settings.parser
    if choice == "none":
        return None
    if choice == "mock":
        from .mock import MockParser

        return MockParser()
    if choice == "ollama":
        from .ollama import OllamaParser

        return OllamaParser(settings.ollama_url, settings.ollama_model)
    if choice in ("gemini", "auto"):
        if not settings.gemini_api_key:
            return None
        from .gemini import GeminiParser

        return GeminiParser(settings.gemini_api_key, settings.gemini_model)
    return None


__all__ = ["BaseReceiptParser", "ParseContext", "ParsedItem", "ParsedReceipt", "ParserError", "make_parser"]
