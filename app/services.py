"""Business logic on top of SQLite (receipts, learning, months, stats)."""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any

from .db import loads
from .engine import CategoryIn, ItemIn, PaymentIn, PersonIn, compute_month
from .matching import Matcher, Rule, normalize, normalize_store
from .money import to_cents
from .parsers.base import ParseContext, ParsedReceipt

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AUTO_SOURCES = ("ai", "none")


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
        self.message = message


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def current_month() -> str:
    return dt.date.today().strftime("%Y-%m")


def check_month_id(month_id: str) -> str:
    if not MONTH_RE.match(month_id or ""):
        raise ServiceError("Ungültiger Monat (erwartet YYYY-MM).")
    return month_id


def ensure_month(conn: sqlite3.Connection, month_id: str) -> dict:
    check_month_id(month_id)
    conn.execute("INSERT OR IGNORE INTO month(id) VALUES (?)", (month_id,))
    return dict(conn.execute("SELECT * FROM month WHERE id=?", (month_id,)).fetchone())


def assert_open(conn: sqlite3.Connection, month_id: str) -> None:
    row = conn.execute("SELECT status FROM month WHERE id=?", (month_id,)).fetchone()
    if row and row["status"] == "closed":
        raise ServiceError(f"Monat {month_id} ist abgeschlossen. Zum Bearbeiten erst wieder öffnen.", 409)


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# --------------------------------------------------------------------------- persons/categories

def person_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tags"] = loads(d.get("tags"), [])
    d["active"] = bool(d["active"])
    return d


def list_persons(conn: sqlite3.Connection) -> list[dict]:
    return [person_dict(r) for r in conn.execute("SELECT * FROM person ORDER BY active DESC, name COLLATE NOCASE")]


def clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        t = re.sub(r"\s+", "-", str(t).strip().lower())
        if t and t not in out:
            out.append(t[:40])
    return out


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM category ORDER BY sort, id")]


def default_category_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM category WHERE key='basis'").fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT id FROM category WHERE mode='shared' ORDER BY sort, id LIMIT 1").fetchone()
    if not row:
        raise ServiceError("Keine Kategorie vorhanden.", 500)
    return row["id"]


# --------------------------------------------------------------------------- matching

def load_matcher(conn: sqlite3.Connection) -> Matcher:
    rules = [
        Rule(id=r["id"], pattern=r["norm_pattern"], category_id=r["category_id"], source=r["source"], flag=r["flag"],
             store=normalize_store(r["store"]) if r["store"] else None)
        for r in conn.execute("SELECT id, norm_pattern, category_id, source, flag, store FROM product WHERE status='confirmed'")
    ]
    return Matcher(rules)


def classify(name: str, store: str | None, ai_category_id: int | None, matcher: Matcher, default_cat: int) -> dict:
    """Decide category for one line. Trusted DB matches beat the AI suggestion."""
    m = matcher.match(name, store)
    if m is not None:
        trusted = m.kind in ("full", "exact") and m.rule.flag == "sure"
        if trusted or ai_category_id is None or ai_category_id == m.category_id:
            return {
                "category_id": m.category_id, "suggested_category_id": m.category_id, "source": m.rule.source,
                "match_kind": m.kind, "review": int(m.needs_review), "product_id": m.rule.id,
            }
        return {
            "category_id": ai_category_id, "suggested_category_id": ai_category_id, "source": "ai",
            "match_kind": m.kind, "review": 1, "product_id": None,
        }
    if ai_category_id is not None:
        return {"category_id": ai_category_id, "suggested_category_id": ai_category_id, "source": "ai",
                "match_kind": None, "review": 1, "product_id": None}
    return {"category_id": default_cat, "suggested_category_id": None, "source": "none",
            "match_kind": None, "review": 1, "product_id": None}


def parse_context(conn: sqlite3.Connection, n_examples: int = 30) -> ParseContext:
    cats = [{"key": c["key"], "name": c["name"], "description": c["description"]} for c in list_categories(conn)]
    rows = conn.execute(
        "SELECT p.pattern, c.key FROM product p JOIN category c ON c.id=p.category_id "
        "WHERE p.source IN ('manual','learned') AND p.status='confirmed' ORDER BY p.updated_at DESC, p.id DESC LIMIT ?",
        (n_examples,),
    ).fetchall()
    return ParseContext(categories=cats, examples=[(r[0], r[1]) for r in rows])


# --------------------------------------------------------------------------- receipts

def _month_for(date: str | None) -> str:
    if date and DATE_RE.match(date):
        return date[:7]
    return current_month()


def create_receipt(conn: sqlite3.Connection, *, buyer_id: int | None, store: str | None = None, date: str | None = None,
                   total_cents: int | None = None, month_id: str | None = None, image_path: str | None = None,
                   image_sha256: str | None = None, parser: str | None = None, parse_error: str | None = None,
                   raw_json: str | None = None) -> int:
    month_id = month_id or _month_for(date)
    ensure_month(conn, month_id)
    assert_open(conn, month_id)
    if buyer_id is not None and not conn.execute("SELECT 1 FROM person WHERE id=?", (buyer_id,)).fetchone():
        raise ServiceError("Käufer unbekannt.")
    cur = conn.execute(
        "INSERT INTO receipt(month_id, store, date, total_cents, buyer_id, image_path, image_sha256, parser, parse_error, raw_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (month_id, store, date, total_cents, buyer_id, image_path, image_sha256, parser, parse_error, raw_json),
    )
    return cur.lastrowid


def apply_parsed(conn: sqlite3.Connection, receipt_id: int, parsed: ParsedReceipt) -> None:
    """Replace a draft's header/items with parser output, running the matcher."""
    cats = {c["key"]: c["id"] for c in list_categories(conn)}
    default_cat = default_category_id(conn)
    matcher = load_matcher(conn)
    rec = conn.execute("SELECT * FROM receipt WHERE id=?", (receipt_id,)).fetchone()
    store = parsed.store or rec["store"]
    date = parsed.date or rec["date"]
    month_id = _month_for(date) if parsed.date else rec["month_id"]
    ensure_month(conn, month_id)
    assert_open(conn, month_id)
    total = to_cents(parsed.total) if parsed.total is not None else None
    conn.execute(
        "UPDATE receipt SET store=?, date=?, total_cents=?, month_id=?, raw_json=?, parse_error=NULL WHERE id=?",
        (store, date, total, month_id, parsed.model_dump_json(), receipt_id),
    )
    conn.execute("DELETE FROM item WHERE receipt_id=?", (receipt_id,))
    for pos, it in enumerate(parsed.items):
        if not it.name:
            continue
        cls = classify(it.name, store, cats.get(it.suggested_category or ""), matcher, default_cat)
        conn.execute(
            "INSERT INTO item(receipt_id, pos, name, norm_name, qty, price_cents, category_id, suggested_category_id, source, match_kind, review, product_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, pos, it.name, normalize(it.name), it.qty or 1, to_cents(it.price), cls["category_id"],
             cls["suggested_category_id"], cls["source"], cls["match_kind"], cls["review"], cls["product_id"]),
        )


def update_receipt(conn: sqlite3.Connection, receipt_id: int, data: dict[str, Any]) -> None:
    rec = conn.execute("SELECT * FROM receipt WHERE id=?", (receipt_id,)).fetchone()
    if not rec:
        raise ServiceError("Beleg nicht gefunden.", 404)
    assert_open(conn, rec["month_id"])
    fields: dict[str, Any] = {}
    for key in ("store", "date", "note"):
        if key in data:
            fields[key] = (data[key] or None)
    if fields.get("date") and not DATE_RE.match(fields["date"]):
        raise ServiceError("Datum muss YYYY-MM-DD sein.")
    if "buyer_id" in data:
        if data["buyer_id"] is not None and not conn.execute("SELECT 1 FROM person WHERE id=?", (data["buyer_id"],)).fetchone():
            raise ServiceError("Käufer unbekannt.")
        fields["buyer_id"] = data["buyer_id"]
    if "total_cents" in data:
        fields["total_cents"] = data["total_cents"]
    if data.get("month_id"):
        fields["month_id"] = check_month_id(data["month_id"])
    elif "date" in data and fields.get("date") and data.get("month_id") is None and rec["date"] != fields["date"]:
        fields["month_id"] = fields["date"][:7]
    if "month_id" in fields:
        ensure_month(conn, fields["month_id"])
        assert_open(conn, fields["month_id"])
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE receipt SET {sets} WHERE id=?", (*fields.values(), receipt_id))

    if "items" in data and data["items"] is not None:
        store = fields.get("store", rec["store"])
        existing = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM item WHERE receipt_id=?", (receipt_id,))}
        valid_cats = {r["id"] for r in conn.execute("SELECT id FROM category")}
        person_ids = {r["id"] for r in conn.execute("SELECT id FROM person")}
        matcher: Matcher | None = None
        default_cat = default_category_id(conn)
        keep: set[int] = set()
        for pos, it in enumerate(data["items"]):
            name = (it.get("name") or "").strip()
            if not name:
                continue
            price = int(it["price_cents"])
            cat_id = it.get("category_id")
            override = it.get("override_persons") or None
            if override:
                override = [int(p) for p in override if int(p) in person_ids] or None
            old = existing.get(it.get("id")) if it.get("id") else None
            if old and old["name"] == name:
                meta = {k: old[k] for k in ("suggested_category_id", "source", "match_kind", "product_id")}
            else:
                matcher = matcher or load_matcher(conn)
                cls = classify(name, store, None, matcher, default_cat)
                meta = {k: cls[k] for k in ("suggested_category_id", "source", "match_kind", "product_id")}
                if meta["source"] == "none" and not old:
                    meta["source"] = "none"
                if cat_id is None:
                    cat_id = cls["category_id"]
            if cat_id not in valid_cats:
                raise ServiceError(f"Ungültige Kategorie für '{name}'.")
            values = (pos, name, normalize(name), float(it.get("qty") or 1), price, cat_id,
                      meta["suggested_category_id"], json.dumps(override) if override else None,
                      meta["source"], meta["match_kind"], 0, meta["product_id"])
            if old:
                conn.execute(
                    "UPDATE item SET pos=?, name=?, norm_name=?, qty=?, price_cents=?, category_id=?, suggested_category_id=?, "
                    "override_persons=?, source=?, match_kind=?, review=?, product_id=? WHERE id=?",
                    (*values, old["id"]),
                )
                keep.add(old["id"])
            else:
                cur = conn.execute(
                    "INSERT INTO item(receipt_id, pos, name, norm_name, qty, price_cents, category_id, suggested_category_id, "
                    "override_persons, source, match_kind, review, product_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (receipt_id, *values),
                )
                keep.add(cur.lastrowid)
        for iid in set(existing) - keep:
            conn.execute("DELETE FROM item WHERE id=?", (iid,))


def confirm_receipt(conn: sqlite3.Connection, receipt_id: int) -> dict:
    """Mark receipt confirmed and learn product rules (call inside one transaction)."""
    rec = conn.execute("SELECT * FROM receipt WHERE id=?", (receipt_id,)).fetchone()
    if not rec:
        raise ServiceError("Beleg nicht gefunden.", 404)
    assert_open(conn, rec["month_id"])
    if rec["buyer_id"] is None:
        raise ServiceError("Bitte zuerst den Käufer auswählen.")
    items = [dict(r) for r in conn.execute("SELECT * FROM item WHERE receipt_id=?", (receipt_id,))]
    if not items:
        raise ServiceError("Der Beleg hat keine Posten.")
    ts = now_iso()
    learned = updated = 0
    for it in items:
        changed = it["suggested_category_id"] is not None and it["category_id"] != it["suggested_category_id"]
        is_new = it["source"] in AUTO_SOURCES or it["suggested_category_id"] is None
        norm = it["norm_name"] or normalize(it["name"])
        if not norm:
            continue
        if changed or is_new:
            source = "manual" if (changed or it["source"] == "none") else "learned"
            existing = conn.execute(
                "SELECT * FROM product WHERE norm_pattern=? AND store IS NULL", (norm,)
            ).fetchone()
            if existing:
                conflict = int(existing["category_id"] != it["category_id"] and existing["source"] == "manual")
                conn.execute(
                    "UPDATE product SET category_id=?, source=?, status='confirmed', flag='sure', hits=hits+1, last_seen=?, "
                    "updated_at=?, conflict=MAX(conflict, ?) WHERE id=?",
                    (it["category_id"], source, ts, ts, conflict, existing["id"]),
                )
                pid = existing["id"]
                updated += 1
            else:
                cur = conn.execute(
                    "INSERT INTO product(pattern, norm_pattern, category_id, source, status, flag, hits, last_seen, created_at, updated_at) "
                    "VALUES (?,?,?,?, 'confirmed', 'sure', 1, ?, ?, ?)",
                    (it["name"], norm, it["category_id"], source, ts, ts, ts),
                )
                pid = cur.lastrowid
                learned += 1
            conn.execute(
                "UPDATE item SET product_id=?, suggested_category_id=?, source=?, review=0 WHERE id=?",
                (pid, it["category_id"], source, it["id"]),
            )
        elif it["product_id"]:
            conn.execute("UPDATE product SET hits=hits+1, last_seen=? WHERE id=?", (ts, it["product_id"]))
            conn.execute("UPDATE item SET review=0 WHERE id=?", (it["id"],))
    conn.execute("UPDATE receipt SET status='confirmed', confirmed_at=? WHERE id=?", (ts, receipt_id))
    return {"learned": learned, "updated": updated}


def find_duplicates(conn: sqlite3.Connection, rec: sqlite3.Row | dict) -> list[dict]:
    out: list[dict] = []
    if rec["image_sha256"]:
        out += [dict(r) for r in conn.execute(
            "SELECT id, store, date, total_cents, status FROM receipt WHERE image_sha256=? AND id<>?",
            (rec["image_sha256"], rec["id"]),
        )]
    if rec["store"] and rec["date"] and rec["total_cents"] is not None:
        for r in conn.execute(
            "SELECT id, store, date, total_cents, status FROM receipt WHERE id<>? AND date=? AND total_cents=? AND lower(store)=lower(?)",
            (rec["id"], rec["date"], rec["total_cents"], rec["store"]),
        ):
            if all(o["id"] != r["id"] for o in out):
                out.append(dict(r))
    return out


def receipt_detail(conn: sqlite3.Connection, receipt_id: int) -> dict:
    rec = conn.execute("SELECT * FROM receipt WHERE id=?", (receipt_id,)).fetchone()
    if not rec:
        raise ServiceError("Beleg nicht gefunden.", 404)
    d = dict(rec)
    d.pop("raw_json", None)
    d["has_image"] = bool(rec["image_path"])
    items = []
    for r in conn.execute("SELECT * FROM item WHERE receipt_id=? ORDER BY pos, id", (receipt_id,)):
        it = dict(r)
        it["override_persons"] = loads(it["override_persons"], None)
        it["review"] = bool(it["review"])
        it["is_new"] = it["source"] in AUTO_SOURCES
        items.append(it)
    d["items"] = items
    items_sum = sum(i["price_cents"] for i in items)
    d["items_sum_cents"] = items_sum
    warnings: list[dict] = []
    if rec["parse_error"]:
        warnings.append({"code": "parse_error", "level": "error", "message": rec["parse_error"]})
    if rec["total_cents"] is not None and items and items_sum != rec["total_cents"]:
        warnings.append({"code": "sum_mismatch", "level": "error",
                         "message": f"Summe der Posten ({items_sum/100:.2f} €) weicht von der Bon-Summe ({rec['total_cents']/100:.2f} €) ab."})
    zero_neg = [i for i in items if i["price_cents"] <= 0]
    if zero_neg:
        warnings.append({"code": "zero_negative", "level": "info",
                         "message": f"{len(zero_neg)} Posten mit Preis ≤ 0 (Rabatt/Pfand) – bitte Kategorie prüfen."})
    dups = find_duplicates(conn, rec)
    if dups:
        warnings.append({"code": "duplicate", "level": "warn",
                         "message": "Möglicher doppelter Beleg: " + ", ".join(f"#{x['id']} ({x['store'] or '?'} {x['date'] or ''})" for x in dups),
                         "receipts": [x["id"] for x in dups]})
    new_count = sum(1 for i in items if i["is_new"])
    if new_count > 3:
        warnings.append({"code": "many_new", "level": "info", "message": f"{new_count} neue Artikel – Zuordnung bitte kurz prüfen."})
    if rec["buyer_id"] is None:
        warnings.append({"code": "no_buyer", "level": "warn", "message": "Käufer fehlt."})
    d["warnings"] = warnings
    d["duplicates"] = dups
    month = conn.execute("SELECT status FROM month WHERE id=?", (rec["month_id"],)).fetchone()
    d["month_status"] = month["status"] if month else "open"
    return d


def list_receipts(conn: sqlite3.Connection, *, month: str | None = None, store: str | None = None, buyer_id: int | None = None,
                  category_id: int | None = None, q: str | None = None, status: str | None = None,
                  limit: int = 200, offset: int = 0) -> list[dict]:
    where, params = [], []
    if month:
        where.append("r.month_id=?"); params.append(month)
    if store:
        where.append("lower(r.store) LIKE ?"); params.append(f"%{store.lower()}%")
    if buyer_id:
        where.append("r.buyer_id=?"); params.append(buyer_id)
    if status:
        where.append("r.status=?"); params.append(status)
    if category_id:
        where.append("EXISTS (SELECT 1 FROM item i WHERE i.receipt_id=r.id AND i.category_id=?)"); params.append(category_id)
    if q:
        where.append("(lower(IFNULL(r.store,'')) LIKE ? OR lower(IFNULL(r.note,'')) LIKE ? OR EXISTS (SELECT 1 FROM item i WHERE i.receipt_id=r.id AND lower(i.name) LIKE ?))")
        params += [f"%{q.lower()}%"] * 3
    sql = (
        "SELECT r.id, r.month_id, r.store, r.date, r.total_cents, r.buyer_id, r.status, r.created_at, r.note, "
        "(r.image_path IS NOT NULL) AS has_image, p.name AS buyer_name, p.color AS buyer_color, "
        "(SELECT COUNT(*) FROM item i WHERE i.receipt_id=r.id) AS item_count, "
        "(SELECT IFNULL(SUM(i.price_cents),0) FROM item i WHERE i.receipt_id=r.id) AS items_sum_cents "
        "FROM receipt r LEFT JOIN person p ON p.id=r.buyer_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY IFNULL(r.date, substr(r.created_at,1,10)) DESC, r.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = []
    for r in conn.execute(sql, params):
        d = dict(r)
        d["has_image"] = bool(d["has_image"])
        rows.append(d)
    return rows


# --------------------------------------------------------------------------- months

def presence_map(conn: sqlite3.Connection, month_id: str) -> dict[int, int]:
    return {r["person_id"]: r["days"] for r in conn.execute("SELECT person_id, days FROM presence WHERE month_id=?", (month_id,))}


def month_result(conn: sqlite3.Connection, month_id: str):
    persons_rows = list_persons(conn)
    persons = [PersonIn(id=p["id"], name=p["name"], tags=frozenset(p["tags"])) for p in persons_rows]
    cats = {c["id"]: CategoryIn(id=c["id"], required_tag=c["required_tag"] or None, mode=c["mode"]) for c in list_categories(conn)}
    items = [
        ItemIn(id=r["id"], receipt_id=r["receipt_id"], price_cents=r["price_cents"], category_id=r["category_id"],
               buyer_id=r["buyer_id"], override_persons=tuple(loads(r["override_persons"], []) or []) or None)
        for r in conn.execute(
            "SELECT i.id, i.receipt_id, i.price_cents, i.category_id, i.override_persons, r.buyer_id FROM item i "
            "JOIN receipt r ON r.id=i.receipt_id WHERE r.month_id=? AND r.status='confirmed' ORDER BY i.id",
            (month_id,),
        )
    ]
    payments = [PaymentIn(r["from_id"], r["to_id"], r["cents"]) for r in conn.execute(
        "SELECT from_id, to_id, cents FROM payment WHERE month_id=?", (month_id,))]
    return compute_month(persons, presence_map(conn, month_id), cats, items, payments), persons_rows


def month_summary(conn: sqlite3.Connection, month_id: str) -> dict:
    check_month_id(month_id)
    month = conn.execute("SELECT * FROM month WHERE id=?", (month_id,)).fetchone()
    result, persons_rows = month_result(conn, month_id)
    pmap = {p["id"]: p for p in persons_rows}
    days = presence_map(conn, month_id)
    persons_out = []
    for p in persons_rows:
        r = result.persons[p["id"]]
        if not p["active"] and r.days == 0 and r.haben == 0 and r.soll == 0 and r.paid == 0 and r.received == 0 and r.private == 0:
            continue
        persons_out.append({
            "id": p["id"], "name": p["name"], "color": p["color"], "tags": p["tags"], "active": p["active"],
            "days": r.days, "days_set": p["id"] in days, "soll": r.soll, "haben": r.haben, "private": r.private,
            "paid": r.paid, "received": r.received, "saldo_before_payments": r.saldo_before_payments, "saldo": r.saldo,
            "by_category": {str(k): v for k, v in r.by_category.items()},
        })
    counts = conn.execute(
        "SELECT SUM(status='confirmed') AS confirmed, SUM(status='draft') AS drafts FROM receipt WHERE month_id=?", (month_id,)
    ).fetchone()
    payments = [dict(r) for r in conn.execute(
        "SELECT * FROM payment WHERE month_id=? ORDER BY created_at, id", (month_id,))]
    return {
        "id": month_id,
        "status": month["status"] if month else "open",
        "closed_at": month["closed_at"] if month else None,
        "exists": bool(month),
        "persons": persons_out,
        "total_cents": result.total_cents,
        "private_cents": result.private_cents,
        "total_days": result.total_days,
        "cost_per_tmm": result.cost_per_tmm,
        "category_totals": {str(k): v for k, v in result.category_totals.items()},
        "category_tmm": {str(k): v for k, v in result.category_tmm.items()},
        "transfers": [{"from_id": t.from_id, "to_id": t.to_id, "cents": t.cents,
                       "from_name": pmap[t.from_id]["name"], "to_name": pmap[t.to_id]["name"]} for t in result.transfers],
        "warnings": [w.__dict__ for w in result.warnings],
        "receipts_confirmed": counts["confirmed"] or 0,
        "receipts_draft": counts["drafts"] or 0,
        "payments": payments,
        "reference_tmm_cents": int(get_setting(conn, "reference_tmm_cents", "726") or 726),
    }


def set_presence(conn: sqlite3.Connection, month_id: str, days: dict[int, int]) -> None:
    ensure_month(conn, month_id)
    assert_open(conn, month_id)
    person_ids = {r["id"] for r in conn.execute("SELECT id FROM person")}
    for pid, d in days.items():
        pid = int(pid)
        if pid not in person_ids:
            raise ServiceError(f"Person {pid} unbekannt.")
        d = int(d)
        if not 0 <= d <= 31:
            raise ServiceError("Tage müssen zwischen 0 und 31 liegen.")
        conn.execute(
            "INSERT INTO presence(month_id, person_id, days) VALUES (?,?,?) "
            "ON CONFLICT(month_id, person_id) DO UPDATE SET days=excluded.days",
            (month_id, pid, d),
        )


def previous_month(month_id: str) -> str:
    y, m = map(int, month_id.split("-"))
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


def list_months(conn: sqlite3.Connection) -> list[dict]:
    ids = {r["id"] for r in conn.execute("SELECT id FROM month")}
    ids.add(current_month())
    out = []
    for mid in sorted(ids, reverse=True):
        row = conn.execute("SELECT status FROM month WHERE id=?", (mid,)).fetchone()
        agg = conn.execute(
            "SELECT COUNT(DISTINCT r.id) AS receipts, IFNULL(SUM(i.price_cents),0) AS total FROM receipt r "
            "LEFT JOIN item i ON i.receipt_id=r.id WHERE r.month_id=? AND r.status='confirmed'", (mid,)
        ).fetchone()
        out.append({"id": mid, "status": row["status"] if row else "open", "receipts": agg["receipts"], "total_cents": agg["total"]})
    return out


# --------------------------------------------------------------------------- statistics

def stats(conn: sqlite3.Connection, months_back: int = 24) -> dict:
    months = [r["id"] for r in conn.execute(
        "SELECT DISTINCT month_id AS id FROM receipt WHERE status='confirmed' "
        "UNION SELECT DISTINCT month_id FROM presence ORDER BY id DESC LIMIT ?", (months_back,))]
    months.sort()
    cats = list_categories(conn)
    series = []
    for mid in months:
        result, persons_rows = month_result(conn, mid)
        series.append({
            "month": mid,
            "total_cents": result.total_cents,
            "private_cents": result.private_cents,
            "total_days": result.total_days,
            "cost_per_tmm": result.cost_per_tmm,
            "category_totals": {str(k): v for k, v in result.category_totals.items()},
            "persons": {str(pid): {"soll": r.soll, "haben": r.haben, "days": r.days} for pid, r in result.persons.items()},
        })
    stores = [dict(r) for r in conn.execute(
        "SELECT IFNULL(NULLIF(trim(r.store),''),'Unbekannt') AS store, COUNT(DISTINCT r.id) AS receipts, IFNULL(SUM(i.price_cents),0) AS total_cents "
        "FROM receipt r JOIN item i ON i.receipt_id=r.id WHERE r.status='confirmed' GROUP BY 1 ORDER BY total_cents DESC LIMIT 12")]
    top_products = [dict(r) for r in conn.execute(
        "SELECT i.name, c.key AS category_key, COUNT(*) AS count, SUM(i.price_cents) AS total_cents FROM item i "
        "JOIN receipt r ON r.id=i.receipt_id JOIN category c ON c.id=i.category_id WHERE r.status='confirmed' "
        "GROUP BY i.norm_name ORDER BY total_cents DESC LIMIT 15")]
    return {
        "months": series,
        "categories": cats,
        "persons": list_persons(conn),
        "stores": stores,
        "top_products": top_products,
        "reference_tmm_cents": int(get_setting(conn, "reference_tmm_cents", "726") or 726),
    }
