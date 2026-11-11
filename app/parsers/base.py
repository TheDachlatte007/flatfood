"""Pluggable receipt parsers."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from ..money import to_cents


class ParsedItem(BaseModel):
    name: str
    qty: float = 1
    price: float = Field(description="Zeilensumme in Euro (negativ bei Rabatt/Leergut)")
    suggested_category: str | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()[:200]


class ParsedReceipt(BaseModel):
    store: str | None = None
    date: str | None = None  # YYYY-MM-DD
    total: float | None = None
    items: list[ParsedItem] = []

    @field_validator("date")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return v
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", v)
        if m:
            d, mo, y = m.groups()
            y = ("20" + y) if len(y) == 2 else y
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        return None


@dataclass
class ParseContext:
    categories: list[dict]           # [{key, name, description}]
    examples: list[tuple[str, str]] = field(default_factory=list)  # (name, category_key)


class ParserError(Exception):
    pass


class BaseReceiptParser(ABC):
    name = "base"

    @abstractmethod
    def parse(self, image: bytes, mime_type: str, ctx: ParseContext) -> ParsedReceipt:
        """Return a standardized receipt."""


def response_schema(category_keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "store": {"type": "string", "description": "Name des Geschäfts, z. B. Edeka, Rewe, Aldi, dm"},
            "date": {"type": "string", "description": "Kaufdatum im Format YYYY-MM-DD"},
            "total": {"type": "number", "description": "Bon-Gesamtsumme in Euro"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Artikeltext wie auf dem Bon, gut lesbar"},
                        "qty": {"type": "number", "description": "Menge, Standard 1"},
                        "price": {"type": "number", "description": "Zeilensumme in Euro, negativ bei Rabatt/Leergut"},
                        "suggested_category": {"type": "string", "enum": category_keys},
                    },
                    "required": ["name", "price", "suggested_category"],
                },
            },
        },
        "required": ["items"],
    }


def build_prompt(ctx: ParseContext) -> str:
    cats = "\n".join(f"- {c['key']}: {c['name']} – {c.get('description') or ''}" for c in ctx.categories)
    examples = ""
    if ctx.examples:
        examples = "\nBekannte Zuordnungen dieser WG (haben Vorrang):\n" + "\n".join(
            f"- \"{n}\" -> {k}" for n, k in ctx.examples
        )
    return f"""Du liest einen deutschen Kassenbon (Supermarkt/Drogerie) und gibst strukturiertes JSON zurück.

Regeln:
1. Jede gekaufte Position wird ein Eintrag in "items". "price" ist die Zeilensumme in Euro (Menge x Einzelpreis), nicht der Einzelpreis.
2. Mengenzeilen wie "3 x 1,29" gehören zur Position darüber: qty=3, price=3.87.
3. Gewichtsware (z. B. "0,532 kg x 2,99 €/kg") ist eine Position mit dem berechneten Zeilenpreis.
4. Rabatte, Coupons und Preisnachlässe sind eigene Positionen mit negativem Preis. Name: "Rabatt <Artikel>", Kategorie wie der rabattierte Artikel.
5. Pfand, das zu einem Artikel gehört (z. B. Flaschenpfand nach Bier), bekommt die Kategorie dieses Artikels. Leergut-Rückgabe (negativ) bekommt "pfand".
6. Ignoriere Zwischensummen, Gesamtsumme, Zahlungsart, MwSt-Tabellen, Payback/Kundenkarte, Rückgeld.
7. "total" ist der zu zahlende Endbetrag. "date" im Format YYYY-MM-DD.
8. Kategorisiere jede Position mit genau einem Schlüssel:
{cats}
Im Zweifel: pflanzliche Lebensmittel -> basis; Produkte mit Milch/Käse/Butter -> milch; Fleisch, Wurst, Fisch -> fleisch.
Abkürzungen auflösen (z. B. "H-MILCH" = haltbare Kuhmilch -> milch, "HAFERDR" = Haferdrink -> basis).{examples}
"""


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t


def parse_json_payload(text: str) -> ParsedReceipt:
    try:
        data = json.loads(_strip_code_fences(text))
    except (TypeError, ValueError) as exc:
        raise ParserError(f"Antwort war kein gültiges JSON: {exc}") from exc
    try:
        return ParsedReceipt.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise ParserError(f"Antwort passte nicht zum Schema: {exc}") from exc


def items_sum_cents(receipt: ParsedReceipt) -> int:
    return sum(to_cents(i.price) for i in receipt.items)
