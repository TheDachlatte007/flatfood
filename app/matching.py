"""Normalization and deterministic product matching for receipt lines."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

# Tokens without meaning for categorisation (units, packaging, bon noise).
STOP_TOKENS = {
    "bio", "kl", "stk", "st", "stck", "pck", "pkg", "pack", "packung", "ca", "je", "x",
    "kg", "g", "gr", "l", "ltr", "ml", "cl", "mg", "eur", "euro", "oder", "und", "mit",
    "dose", "glas", "fl", "flasche", "btl", "beutel", "becher", "ds", "gl", "stueck", "stuck",
    "mwst", "nr", "art", "a", "b", "c",
}
UNIT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:kg|g|gr|ml|l|ltr|cl|st|stk|x|%)?(?![a-z])")
NON_ALPHA_RE = re.compile(r"[^a-z]+")
SOURCE_RANK = {"manual": 4, "learned": 3, "import": 2, "seed": 1, "ai": 1, "off": 0}
MIN_COMPOUND = 4      # min pattern length for prefix/suffix (compound word) matches
MIN_TRUNCATED = 4     # min bon-token length for "abbreviated" matches


def _fold(text: str) -> str:
    s = text.lower()
    s = s.replace("ß", "ss").replace("ä", "a").replace("ö", "o").replace("ü", "u")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Printers without umlauts write AE/OE/UE; fold these the same way on both sides.
    s = s.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    return s


def normalize(text: str | None) -> str:
    if not text:
        return ""
    s = _fold(text)
    # Join hyphenated tokens like h-milch -> hmilch, bio-apfel -> bioapfel
    s = re.sub(r"(?<=[a-z0-9])[-_]+(?=[a-z0-9])", "", s)
    s = UNIT_RE.sub(" ", s)
    s = NON_ALPHA_RE.sub(" ", s)
    tokens = [t for t in s.split() if len(t) > 1 and t not in STOP_TOKENS]
    return " ".join(tokens)


def normalize_store(text: str | None) -> str | None:
    n = normalize(text)
    return n.split(" ")[0] if n else None


@dataclass(frozen=True)
class Rule:
    id: int
    pattern: str          # normalized
    category_id: int
    source: str = "seed"
    flag: str = "sure"    # sure | review
    store: str | None = None  # normalized store key

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(self.pattern.split(" "))


@dataclass(frozen=True)
class Match:
    rule: Rule
    kind: str   # full | exact | compound | truncated
    score: float

    @property
    def category_id(self) -> int:
        return self.rule.category_id

    @property
    def needs_review(self) -> bool:
        return self.rule.flag == "review" or self.kind == "truncated"


class Matcher:
    def __init__(self, rules: Iterable[Rule]):
        self.rules = [r for r in rules if r.pattern]
        self.full: dict[str, list[Rule]] = {}
        self.single: dict[str, list[Rule]] = {}
        self.multi: list[Rule] = []
        self.compound: list[Rule] = []
        for r in self.rules:
            self.full.setdefault(r.pattern, []).append(r)
            if " " in r.pattern:
                self.multi.append(r)
            else:
                self.single.setdefault(r.pattern, []).append(r)
                if len(r.pattern) >= MIN_COMPOUND:
                    self.compound.append(r)

    @staticmethod
    def _key(m: Match, store: str | None):
        return (
            m.score,
            1 if (m.rule.store and m.rule.store == store) else 0,
            SOURCE_RANK.get(m.rule.source, 0),
            -m.rule.id,
        )

    def _store_ok(self, r: Rule, store: str | None) -> bool:
        return r.store is None or r.store == store

    def match(self, name: str, store: str | None = None) -> Match | None:
        norm = normalize(name)
        if not norm:
            return None
        store_key = normalize_store(store) if store else None
        tokens = norm.split(" ")
        cands: list[Match] = []

        for r in self.full.get(norm, []):
            if self._store_ok(r, store_key):
                cands.append(Match(r, "full", 1000 + len(r.pattern)))

        padded = f" {norm} "
        for r in self.multi:
            if f" {r.pattern} " in padded and self._store_ok(r, store_key):
                cands.append(Match(r, "exact", len(r.pattern) + 0.3))

        for t in tokens:
            for r in self.single.get(t, []):
                if self._store_ok(r, store_key):
                    cands.append(Match(r, "exact", len(r.pattern) + 0.3))
        for r in self.compound:
            p = r.pattern
            for t in tokens:
                if t != p and (t.startswith(p) or t.endswith(p)) and self._store_ok(r, store_key):
                    cands.append(Match(r, "compound", float(len(p))))
                    break

        if cands:
            return max(cands, key=lambda m: self._key(m, store_key))

        # Abbreviated bon tokens ("VOLLM" -> "vollmilch"). Only accept if unambiguous.
        trunc: list[Match] = []
        for t in tokens:
            if len(t) < MIN_TRUNCATED:
                continue
            for pattern, rules in self.single.items():
                if pattern != t and pattern.startswith(t):
                    for r in rules:
                        if self._store_ok(r, store_key):
                            trunc.append(Match(r, "truncated", len(t) - 0.5))
        if not trunc:
            return None
        cats = {m.rule.category_id for m in trunc}
        if len(cats) != 1:
            return None
        return max(trunc, key=lambda m: self._key(m, store_key))
