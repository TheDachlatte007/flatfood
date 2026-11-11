"""Pure, deterministic splitting engine (no I/O).

Model ("Tage mit Mahlzeit", TmM): every shared receipt line is split among its
participants proportionally to the days each participant was present in the month.
Participants of a line are
  * the explicit ``override_persons`` of the line, otherwise
  * every person carrying the category's ``required_tag`` (or everybody if the
    category has no tag).
People with 0 days pay nothing. Lines of ``buyer``-mode categories (private) are
ignored for the shared kitty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .money import allocate


@dataclass(frozen=True)
class PersonIn:
    id: int
    name: str
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CategoryIn:
    id: int
    required_tag: str | None = None
    mode: str = "shared"  # shared | buyer


@dataclass(frozen=True)
class ItemIn:
    id: int
    price_cents: int
    category_id: int
    buyer_id: int | None
    receipt_id: int = 0
    override_persons: tuple[int, ...] | None = None


@dataclass(frozen=True)
class PaymentIn:
    from_id: int
    to_id: int
    cents: int


@dataclass
class Warning_:
    code: str
    message: str
    item_id: int | None = None
    receipt_id: int | None = None


@dataclass
class PersonResult:
    id: int
    name: str
    days: int
    soll: int = 0          # share of the costs
    haben: int = 0         # own shared purchases
    private: int = 0       # own private purchases (info only)
    paid: int = 0          # settlement payments sent
    received: int = 0      # settlement payments received
    by_category: dict[int, int] = field(default_factory=dict)

    @property
    def saldo_before_payments(self) -> int:
        return self.haben - self.soll

    @property
    def saldo(self) -> int:
        return self.haben - self.soll + self.paid - self.received


@dataclass
class Transfer:
    from_id: int
    to_id: int
    cents: int


@dataclass
class MonthResult:
    persons: dict[int, PersonResult]
    total_cents: int                 # shared kitty total
    private_cents: int
    total_days: int
    category_totals: dict[int, int]
    category_tmm: dict[int, int]     # sum of participant days per category (default participants)
    transfers: list[Transfer]
    warnings: list[Warning_]

    @property
    def cost_per_tmm(self) -> float | None:
        if self.total_days <= 0:
            return None
        return self.total_cents / self.total_days


def _participants(item: ItemIn, cat: CategoryIn, persons: Sequence[PersonIn]) -> list[int]:
    known = {p.id for p in persons}
    if item.override_persons:
        return [pid for pid in item.override_persons if pid in known]
    if cat.required_tag:
        return [p.id for p in persons if cat.required_tag in p.tags]
    return [p.id for p in persons]


def compute_month(
    persons: Sequence[PersonIn],
    days: dict[int, int],
    categories: dict[int, CategoryIn],
    items: Iterable[ItemIn],
    payments: Iterable[PaymentIn] = (),
) -> MonthResult:
    res = {p.id: PersonResult(id=p.id, name=p.name, days=max(0, int(days.get(p.id, 0) or 0))) for p in persons}
    warnings: list[Warning_] = []
    cat_totals: dict[int, int] = {}
    total = 0
    private_total = 0
    present = {pid: r.days for pid, r in res.items() if r.days > 0}

    for item in items:
        cat = categories.get(item.category_id)
        if cat is None:
            warnings.append(Warning_("unknown_category", "Posten ohne gültige Kategorie wurde ignoriert.", item.id, item.receipt_id))
            continue
        if item.buyer_id is None or item.buyer_id not in res:
            warnings.append(Warning_("no_buyer", "Beleg ohne (bekannten) Käufer wurde ignoriert.", item.id, item.receipt_id))
            continue
        if cat.mode == "buyer":
            res[item.buyer_id].private += item.price_cents
            private_total += item.price_cents
            continue
        if item.price_cents == 0:
            continue

        cand = _participants(item, cat, persons)
        weights = {pid: res[pid].days for pid in cand if res[pid].days > 0}
        if not weights:
            if item.override_persons and cand:
                weights = {pid: 1 for pid in cand}
                warnings.append(Warning_("override_no_days", "Ausgewählte Personen haben 0 Tage – gleichmäßig verteilt.", item.id, item.receipt_id))
            elif present:
                weights = dict(present)
                warnings.append(Warning_("no_participant", "Kein anwesender Teilnehmer für diese Kategorie – auf alle Anwesenden verteilt.", item.id, item.receipt_id))
            else:
                weights = {item.buyer_id: 1}
                warnings.append(Warning_("no_days", "Im Monat sind keine Anwesenheitstage eingetragen – Kosten bleiben beim Käufer.", item.id, item.receipt_id))

        shares = allocate(item.price_cents, weights)
        for pid, cents in shares.items():
            r = res[pid]
            r.soll += cents
            r.by_category[item.category_id] = r.by_category.get(item.category_id, 0) + cents
        res[item.buyer_id].haben += item.price_cents
        cat_totals[item.category_id] = cat_totals.get(item.category_id, 0) + item.price_cents
        total += item.price_cents

    for pay in payments:
        if pay.from_id in res:
            res[pay.from_id].paid += pay.cents
        if pay.to_id in res:
            res[pay.to_id].received += pay.cents

    cat_tmm: dict[int, int] = {}
    for cid, cat in categories.items():
        if cat.mode == "buyer":
            continue
        cat_tmm[cid] = sum(res[p.id].days for p in persons if not cat.required_tag or cat.required_tag in p.tags)

    transfers = settle({pid: r.saldo for pid, r in res.items()})
    return MonthResult(
        persons=res,
        total_cents=total,
        private_cents=private_total,
        total_days=sum(r.days for r in res.values()),
        category_totals=cat_totals,
        category_tmm=cat_tmm,
        transfers=transfers,
        warnings=warnings,
    )


def settle(balances: dict[int, int]) -> list[Transfer]:
    """Greedy minimal-ish transfer list. Positive balance = gets money."""
    creditors = sorted(((b, pid) for pid, b in balances.items() if b > 0), key=lambda x: (-x[0], x[1]))
    debtors = sorted(((-b, pid) for pid, b in balances.items() if b < 0), key=lambda x: (-x[0], x[1]))
    cred = [[b, pid] for b, pid in creditors]
    debt = [[b, pid] for b, pid in debtors]
    out: list[Transfer] = []
    i = j = 0
    while i < len(debt) and j < len(cred):
        amount = min(debt[i][0], cred[j][0])
        if amount > 0:
            out.append(Transfer(from_id=debt[i][1], to_id=cred[j][1], cents=amount))
        debt[i][0] -= amount
        cred[j][0] -= amount
        if debt[i][0] == 0:
            i += 1
        if cred[j][0] == 0:
            j += 1
    return out
