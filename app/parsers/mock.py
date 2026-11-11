from __future__ import annotations

import datetime as dt

from .base import BaseReceiptParser, ParseContext, ParsedItem, ParsedReceipt


class MockParser(BaseReceiptParser):
    """Deterministic demo/test parser (PARSER=mock). Ignores the image."""

    name = "mock"

    def parse(self, image: bytes, mime_type: str, ctx: ParseContext) -> ParsedReceipt:
        items = [
            ParsedItem(name="Bio Hafermilch 1L", qty=1, price=1.29, suggested_category="basis"),
            ParsedItem(name="H-MILCH 3,5% 1L", qty=1, price=1.15, suggested_category="milch"),
            ParsedItem(name="Rinderhack 500g", qty=1, price=4.99, suggested_category="fleisch"),
            ParsedItem(name="Pils 0,5L", qty=6, price=5.94, suggested_category="alkohol"),
            ParsedItem(name="Pfand", qty=6, price=0.48, suggested_category="alkohol"),
            ParsedItem(name="Frosch Spülmittel", qty=1, price=1.85, suggested_category="haushalt"),
            ParsedItem(name="Zaubernuss Knusperkram", qty=1, price=2.49, suggested_category="basis"),
            ParsedItem(name="Rabatt Rinderhack", qty=1, price=-0.50, suggested_category="fleisch"),
        ]
        total = round(sum(i.price for i in items), 2)
        return ParsedReceipt(store="Edeka", date=dt.date.today().isoformat(), total=total, items=items)
