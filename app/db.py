"""SQLite storage: schema, connection handling and seed import."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Iterator

from .matching import normalize

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#3b82f6',
    active INTEGER NOT NULL DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS category (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    required_tag TEXT,
    mode TEXT NOT NULL DEFAULT 'shared' CHECK (mode IN ('shared','buyer')),
    color TEXT NOT NULL DEFAULT '#64748b',
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS month (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS presence (
    month_id TEXT NOT NULL REFERENCES month(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    days INTEGER NOT NULL DEFAULT 0 CHECK (days >= 0 AND days <= 31),
    PRIMARY KEY (month_id, person_id)
);
CREATE TABLE IF NOT EXISTS receipt (
    id INTEGER PRIMARY KEY,
    month_id TEXT NOT NULL REFERENCES month(id),
    store TEXT,
    date TEXT,
    total_cents INTEGER,
    buyer_id INTEGER REFERENCES person(id),
    image_path TEXT,
    image_sha256 TEXT,
    parser TEXT,
    parse_error TEXT,
    raw_json TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','confirmed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_receipt_month ON receipt(month_id);
CREATE INDEX IF NOT EXISTS ix_receipt_sha ON receipt(image_sha256);
CREATE TABLE IF NOT EXISTS product (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,
    norm_pattern TEXT NOT NULL,
    store TEXT,
    category_id INTEGER NOT NULL REFERENCES category(id),
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','pending','disabled')),
    flag TEXT NOT NULL DEFAULT 'sure' CHECK (flag IN ('sure','review')),
    conflict INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    note TEXT,
    seed_category_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_product_pattern ON product(norm_pattern, IFNULL(store, ''));
CREATE TABLE IF NOT EXISTS item (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipt(id) ON DELETE CASCADE,
    pos INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    norm_name TEXT NOT NULL DEFAULT '',
    qty REAL NOT NULL DEFAULT 1,
    price_cents INTEGER NOT NULL,
    category_id INTEGER NOT NULL REFERENCES category(id),
    suggested_category_id INTEGER REFERENCES category(id),
    override_persons TEXT,
    source TEXT NOT NULL DEFAULT 'none',
    match_kind TEXT,
    review INTEGER NOT NULL DEFAULT 0,
    product_id INTEGER REFERENCES product(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_item_receipt ON item(receipt_id);
CREATE INDEX IF NOT EXISTS ix_item_product ON item(product_id);
CREATE TABLE IF NOT EXISTS payment (
    id INTEGER PRIMARY KEY,
    month_id TEXT NOT NULL REFERENCES month(id),
    from_id INTEGER NOT NULL REFERENCES person(id),
    to_id INTEGER NOT NULL REFERENCES person(id),
    cents INTEGER NOT NULL CHECK (cents > 0),
    date TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

SEED_CATEGORIES = [
    # key, name, description, required_tag, mode, color, sort
    ("basis", "Basis / Vegan", "Pflanzliche Lebensmittel, Grundnahrung, Obst, Gemüse, Gewürze, alkoholfreie Getränke", None, "shared", "#16a34a", 10),
    ("haushalt", "Drogerie / Haushalt", "Reinigungsmittel, Spülmittel, Küchenpapier, Toilettenpapier, Müllbeutel", None, "shared", "#0891b2", 20),
    ("milch", "Milch (Kuh) & Milchprodukte", "Kuhmilch, Joghurt, Quark, Käse, Butter, Sahne", "milch", "shared", "#ca8a04", 30),
    ("eier", "Eier", "Eier und Produkte mit Ei als Hauptzutat (Eiernudeln)", "ei", "shared", "#ea580c", 40),
    ("fleisch", "Fleisch & Fisch", "Fleisch, Wurst, Geflügel, Fisch, Meeresfrüchte", "fleisch", "shared", "#dc2626", 50),
    ("alkohol", "Alkohol", "Bier, Wein, Sekt, Spirituosen", "alkohol", "shared", "#7c3aed", 60),
    ("pfand", "Pfand / Leergut", "Leergut-Rückgabe und Pfand ohne erkennbaren Artikel", None, "shared", "#64748b", 70),
    ("privat", "Privat (nur Käufer)", "Persönliche Dinge: Körperpflege, Tragetaschen; zahlt nur der Käufer", None, "buyer", "#94a3b8", 80),
]

DEFAULT_SETTINGS = {
    "reference_tmm_cents": "726",  # 30-Monats-Median 7,26 €/TmM
    "default_days": "25",
}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path, seed_csv: Path | None) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        with conn:
            conn.execute("INSERT OR IGNORE INTO setting(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO setting(key, value) VALUES (?, ?)", (k, v))
            if conn.execute("SELECT COUNT(*) FROM category").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO category(key, name, description, required_tag, mode, color, sort) VALUES (?,?,?,?,?,?,?)",
                    SEED_CATEGORIES,
                )
            seeded = conn.execute("SELECT value FROM setting WHERE key='seed_imported'").fetchone()
            if not seeded and seed_csv and seed_csv.is_file():
                import_seed(conn, seed_csv)
                conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES ('seed_imported', '1')")
    finally:
        conn.close()


def read_seed(seed_csv: Path) -> list[dict]:
    with seed_csv.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh) if (row.get("pattern") or "").strip()]


def import_seed(conn: sqlite3.Connection, seed_csv: Path, only_missing: bool = True) -> int:
    """Insert seed rows. Returns number of inserted rows. Never touches non-seed rows."""
    cats = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM category")}
    inserted = 0
    for row in read_seed(seed_csv):
        key = (row.get("category") or "").strip()
        if key not in cats:
            continue
        pattern = row["pattern"].strip()
        norm = normalize(pattern)
        if not norm:
            continue
        flag = "review" if (row.get("flag") or "").strip() == "review" else "sure"
        cur = conn.execute(
            "INSERT OR IGNORE INTO product(pattern, norm_pattern, category_id, source, status, flag, seed_category_key) "
            "VALUES (?,?,?,?,?,?,?)",
            (pattern, norm, cats[key], "seed", "confirmed", flag, key),
        )
        inserted += cur.rowcount
    return inserted


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def loads(value: str | None, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
