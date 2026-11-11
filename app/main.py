"""FastAPI Application for Flatfood."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import APP_VERSION, SEED_CSV, WEB_DIR, Settings, load_settings
from .db import connect, import_seed, init_db, loads
from .matching import normalize
from .parsers import ParserError, make_parser
from .parsers.base import ParsedReceipt
from .services import (
    ServiceError,
    apply_parsed,
    assert_open,
    check_month_id,
    clean_tags,
    confirm_receipt,
    create_receipt,
    current_month,
    ensure_month,
    list_categories,
    list_months,
    list_persons,
    list_receipts,
    month_summary,
    now_iso,
    parse_context,
    previous_month,
    receipt_detail,
    set_presence,
    stats,
    update_receipt,
)

settings: Settings = load_settings()

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.receipts_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path, SEED_CSV if SEED_CSV.is_file() else None)
    yield


app = FastAPI(
    title="Flatfood",
    description="Smarte, diät- und anwesenheitsbewusste WG-Küchenkasse",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def verify_pin(request: Request) -> None:
    if not settings.app_pin:
        return
    client_pin = request.headers.get("X-App-Pin") or request.query_params.get("pin")
    if client_pin != settings.app_pin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN ungültig.")


# --------------------------------------------------------------------------- Models

class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = "#3b82f6"
    active: bool = True
    tags: list[str] = []


class PersonUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    active: bool | None = None
    tags: list[str] | None = None


class CategoryCreate(BaseModel):
    key: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    required_tag: str | None = None
    mode: str = "shared"  # shared | buyer
    color: str = "#64748b"
    sort: int = 100


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    required_tag: str | None = None
    mode: str | None = None
    color: str | None = None
    sort: int | None = None


class ItemPayload(BaseModel):
    id: int | None = None
    name: str
    price_cents: int
    qty: float = 1.0
    category_id: int | None = None
    override_persons: list[int] | None = None


class ReceiptUpdatePayload(BaseModel):
    store: str | None = None
    date: str | None = None
    buyer_id: int | None = None
    total_cents: int | None = None
    month_id: str | None = None
    note: str | None = None
    items: list[ItemPayload] | None = None


class PresencePayload(BaseModel):
    days: dict[int, int]


class ProductCreate(BaseModel):
    pattern: str = Field(min_length=2)
    category_id: int
    store: str | None = None
    flag: str = "sure"
    note: str | None = None


class ProductUpdate(BaseModel):
    pattern: str | None = None
    category_id: int | None = None
    store: str | None = None
    flag: str | None = None
    status: str | None = None
    note: str | None = None


class BulkProductUpdate(BaseModel):
    ids: list[int]
    category_id: int | None = None
    status: str | None = None
    flag: str | None = None


class PaymentCreate(BaseModel):
    month_id: str
    from_id: int
    to_id: int
    cents: int = Field(gt=0)
    date: str | None = None
    note: str | None = None


# --------------------------------------------------------------------------- System & Meta

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "database": str(settings.db_path),
        "parser": settings.parser,
        "has_gemini_key": bool(settings.gemini_api_key),
    }


@app.get("/api/meta")
def get_meta(conn=Depends(get_db)):
    has_persons = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] > 0
    return {
        "version": APP_VERSION,
        "current_month": current_month(),
        "has_persons": has_persons,
        "pin_required": bool(settings.app_pin),
        "parser": settings.parser,
        "gemini_active": bool(settings.gemini_api_key),
    }


# --------------------------------------------------------------------------- Persons

@app.get("/api/persons")
def api_list_persons(conn=Depends(get_db)):
    return list_persons(conn)


@app.post("/api/persons", dependencies=[Depends(verify_pin)])
def api_create_person(p: PersonCreate, conn=Depends(get_db)):
    tags = clean_tags(p.tags)
    cur = conn.execute(
        "INSERT INTO person(name, color, active, tags) VALUES (?,?,?,?)",
        (p.name.strip(), p.color, int(p.active), json.dumps(tags)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM person WHERE id=?", (cur.lastrowid,)).fetchone()
    d = dict(row)
    d["tags"] = tags
    d["active"] = bool(d["active"])
    return d


@app.patch("/api/persons/{person_id}", dependencies=[Depends(verify_pin)])
def api_update_person(person_id: int, p: PersonUpdate, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM person WHERE id=?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Person nicht gefunden.")
    fields: dict[str, Any] = {}
    if p.name is not None:
        fields["name"] = p.name.strip()
    if p.color is not None:
        fields["color"] = p.color
    if p.active is not None:
        fields["active"] = int(p.active)
    if p.tags is not None:
        fields["tags"] = json.dumps(clean_tags(p.tags))
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE person SET {sets} WHERE id=?", (*fields.values(), person_id))
        conn.commit()
    r = conn.execute("SELECT * FROM person WHERE id=?", (person_id,)).fetchone()
    d = dict(r)
    d["tags"] = loads(d["tags"], [])
    d["active"] = bool(d["active"])
    return d


@app.delete("/api/persons/{person_id}", dependencies=[Depends(verify_pin)])
def api_delete_person(person_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM person WHERE id=?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Person nicht gefunden.")
    # Check if referenced in receipts or payments
    receipt_count = conn.execute("SELECT COUNT(*) FROM receipt WHERE buyer_id=?", (person_id,)).fetchone()[0]
    payment_count = conn.execute("SELECT COUNT(*) FROM payment WHERE from_id=? OR to_id=?", (person_id, person_id)).fetchone()[0]
    if receipt_count > 0 or payment_count > 0:
        # soft delete
        conn.execute("UPDATE person SET active=0 WHERE id=?", (person_id,))
        conn.commit()
        return {"action": "deactivated", "message": "Person hat Belege oder Zahlungen und wurde deaktiviert."}
    conn.execute("DELETE FROM presence WHERE person_id=?", (person_id,))
    conn.execute("DELETE FROM person WHERE id=?", (person_id,))
    conn.commit()
    return {"action": "deleted"}


# --------------------------------------------------------------------------- Categories

@app.get("/api/categories")
def api_list_categories(conn=Depends(get_db)):
    return list_categories(conn)


@app.post("/api/categories", dependencies=[Depends(verify_pin)])
def api_create_category(c: CategoryCreate, conn=Depends(get_db)):
    clean_key = re.sub(r"[^a-z0-9_]+", "_", c.key.strip().lower())
    if conn.execute("SELECT 1 FROM category WHERE key=?", (clean_key,)).fetchone():
        raise HTTPException(400, f"Kategorie-Schlüssel '{clean_key}' existiert bereits.")
    cur = conn.execute(
        "INSERT INTO category(key, name, description, required_tag, mode, color, sort) VALUES (?,?,?,?,?,?,?)",
        (clean_key, c.name.strip(), c.description.strip(), c.required_tag or None, c.mode, c.color, c.sort),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM category WHERE id=?", (cur.lastrowid,)).fetchone())


@app.patch("/api/categories/{cat_id}", dependencies=[Depends(verify_pin)])
def api_update_category(cat_id: int, c: CategoryUpdate, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM category WHERE id=?", (cat_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Kategorie nicht gefunden.")
    fields: dict[str, Any] = {}
    if c.name is not None:
        fields["name"] = c.name.strip()
    if c.description is not None:
        fields["description"] = c.description.strip()
    if "required_tag" in c.model_fields_set:
        fields["required_tag"] = c.required_tag or None
    if c.mode is not None:
        if c.mode not in ("shared", "buyer"):
            raise HTTPException(400, "Modus muss 'shared' oder 'buyer' sein.")
        fields["mode"] = c.mode
    if c.color is not None:
        fields["color"] = c.color
    if c.sort is not None:
        fields["sort"] = c.sort
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE category SET {sets} WHERE id=?", (*fields.values(), cat_id))
        conn.commit()
    return dict(conn.execute("SELECT * FROM category WHERE id=?", (cat_id,)).fetchone())


@app.delete("/api/categories/{cat_id}", dependencies=[Depends(verify_pin)])
def api_delete_category(cat_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM category WHERE id=?", (cat_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Kategorie nicht gefunden.")
    item_count = conn.execute("SELECT COUNT(*) FROM item WHERE category_id=?", (cat_id,)).fetchone()[0]
    prod_count = conn.execute("SELECT COUNT(*) FROM product WHERE category_id=?", (cat_id,)).fetchone()[0]
    if item_count > 0 or prod_count > 0:
        raise HTTPException(400, f"Kategorie wird noch verwendet ({item_count} Posten, {prod_count} Produktregeln).")
    conn.execute("DELETE FROM category WHERE id=?", (cat_id,))
    conn.commit()
    return {"action": "deleted"}


# --------------------------------------------------------------------------- Months & Presence

@app.get("/api/months")
def api_list_months(conn=Depends(get_db)):
    return list_months(conn)


@app.get("/api/months/{month_id}")
def api_get_month(month_id: str, conn=Depends(get_db)):
    try:
        return month_summary(conn, month_id)
    except ServiceError as e:
        raise HTTPException(e.status, e.message)


@app.post("/api/months/{month_id}/presence", dependencies=[Depends(verify_pin)])
def api_set_presence(month_id: str, p: PresencePayload, conn=Depends(get_db)):
    try:
        with conn:
            set_presence(conn, month_id, p.days)
        return month_summary(conn, month_id)
    except ServiceError as e:
        raise HTTPException(e.status, e.message)


@app.post("/api/months/{month_id}/copy-presence-from-previous", dependencies=[Depends(verify_pin)])
def api_copy_presence(month_id: str, conn=Depends(get_db)):
    try:
        check_month_id(month_id)
        assert_open(conn, month_id)
        prev = previous_month(month_id)
        rows = conn.execute("SELECT person_id, days FROM presence WHERE month_id=?", (prev,)).fetchall()
        if not rows:
            raise HTTPException(400, f"Keine Anwesenheitsdaten für Vormonat {prev} vorhanden.")
        with conn:
            ensure_month(conn, month_id)
            for r in rows:
                conn.execute(
                    "INSERT INTO presence(month_id, person_id, days) VALUES (?,?,?) "
                    "ON CONFLICT(month_id, person_id) DO UPDATE SET days=excluded.days",
                    (month_id, r["person_id"], r["days"]),
                )
        return month_summary(conn, month_id)
    except ServiceError as e:
        raise HTTPException(e.status, e.message)


@app.post("/api/months/{month_id}/close", dependencies=[Depends(verify_pin)])
def api_close_month(month_id: str, conn=Depends(get_db)):
    check_month_id(month_id)
    ensure_month(conn, month_id)
    # Check drafts
    drafts = conn.execute("SELECT COUNT(*) FROM receipt WHERE month_id=? AND status='draft'", (month_id,)).fetchone()[0]
    if drafts > 0:
        raise HTTPException(400, f"Es gibt noch {drafts} unbestätigte Belegentwürfe in {month_id}.")
    conn.execute("UPDATE month SET status='closed', closed_at=? WHERE id=?", (now_iso(), month_id))
    conn.commit()
    return month_summary(conn, month_id)


@app.post("/api/months/{month_id}/reopen", dependencies=[Depends(verify_pin)])
def api_reopen_month(month_id: str, conn=Depends(get_db)):
    check_month_id(month_id)
    conn.execute("UPDATE month SET status='open', closed_at=NULL WHERE id=?", (month_id,))
    conn.commit()
    return month_summary(conn, month_id)


# --------------------------------------------------------------------------- Receipts

@app.post("/api/receipts/upload", dependencies=[Depends(verify_pin)])
async def api_upload_receipt(
    image: UploadFile = File(...),
    buyer_id: int | None = Form(None),
    month_id: str | None = Form(None),
    store: str | None = Form(None),
    date: str | None = Form(None),
    conn=Depends(get_db),
):
    contents = await image.read()
    if len(contents) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(400, f"Bilddatei zu groß (max. {settings.max_upload_mb} MB).")
    sha256 = hashlib.sha256(contents).hexdigest()

    # Save to disk
    ext = Path(image.filename or "receipt.jpg").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
        ext = ".jpg"
    filename = f"{sha256[:16]}_{int(os.path.getmtime(settings.data_dir) if settings.data_dir.exists() else 0)}_{len(contents)}{ext}"
    rel_path = f"receipts/{filename}"
    abs_path = settings.data_dir / rel_path
    abs_path.write_bytes(contents)

    parser = make_parser(settings)
    parser_name = parser.name if parser else None
    parse_err = None
    parsed_res: ParsedReceipt | None = None

    if parser:
        mime = image.content_type or ("image/png" if ext == ".png" else "image/jpeg")
        ctx = parse_context(conn)
        try:
            parsed_res = parser.parse(contents, mime, ctx)
        except ParserError as pe:
            parse_err = str(pe)
        except Exception as ex:
            parse_err = f"Unerwarteter Parserfehler: {ex}"

    try:
        with conn:
            rid = create_receipt(
                conn,
                buyer_id=buyer_id,
                store=store,
                date=date,
                month_id=month_id,
                image_path=rel_path,
                image_sha256=sha256,
                parser=parser_name,
                parse_error=parse_err,
            )
            if parsed_res:
                apply_parsed(conn, rid, parsed_res)
        return receipt_detail(conn, rid)
    except ServiceError as se:
        raise HTTPException(se.status, se.message)


@app.post("/api/receipts/manual", dependencies=[Depends(verify_pin)])
def api_create_manual_receipt(
    buyer_id: int | None = None,
    month_id: str | None = None,
    store: str | None = None,
    date: str | None = None,
    conn=Depends(get_db),
):
    try:
        with conn:
            rid = create_receipt(conn, buyer_id=buyer_id, month_id=month_id, store=store, date=date, parser="manual")
        return receipt_detail(conn, rid)
    except ServiceError as se:
        raise HTTPException(se.status, se.message)


@app.get("/api/receipts")
def api_list_receipts(
    month: str | None = None,
    store: str | None = None,
    buyer_id: int | None = None,
    category_id: int | None = None,
    q: str | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    return list_receipts(
        conn,
        month=month,
        store=store,
        buyer_id=buyer_id,
        category_id=category_id,
        q=q,
        status=status,
        limit=limit,
        offset=offset,
    )


@app.get("/api/receipts/{receipt_id}")
def api_get_receipt(receipt_id: int, conn=Depends(get_db)):
    try:
        return receipt_detail(conn, receipt_id)
    except ServiceError as se:
        raise HTTPException(se.status, se.message)


@app.get("/api/receipts/{receipt_id}/image")
def api_get_receipt_image(receipt_id: int, conn=Depends(get_db)):
    rec = conn.execute("SELECT image_path FROM receipt WHERE id=?", (receipt_id,)).fetchone()
    if not rec or not rec["image_path"]:
        raise HTTPException(404, "Kein Bild für diesen Beleg.")
    img_path = settings.data_dir / rec["image_path"]
    if not img_path.is_file():
        raise HTTPException(404, "Bilddatei nicht auf der Festplatte gefunden.")
    return FileResponse(img_path)


@app.patch("/api/receipts/{receipt_id}", dependencies=[Depends(verify_pin)])
def api_update_receipt(receipt_id: int, payload: ReceiptUpdatePayload, conn=Depends(get_db)):
    try:
        with conn:
            update_receipt(conn, receipt_id, payload.model_dump(exclude_unset=True))
        return receipt_detail(conn, receipt_id)
    except ServiceError as se:
        raise HTTPException(se.status, se.message)


@app.post("/api/receipts/{receipt_id}/confirm", dependencies=[Depends(verify_pin)])
def api_confirm_receipt(receipt_id: int, conn=Depends(get_db)):
    try:
        with conn:
            res = confirm_receipt(conn, receipt_id)
        detail = receipt_detail(conn, receipt_id)
        detail["learn_result"] = res
        return detail
    except ServiceError as se:
        raise HTTPException(se.status, se.message)


@app.delete("/api/receipts/{receipt_id}", dependencies=[Depends(verify_pin)])
def api_delete_receipt(receipt_id: int, conn=Depends(get_db)):
    rec = conn.execute("SELECT * FROM receipt WHERE id=?", (receipt_id,)).fetchone()
    if not rec:
        raise HTTPException(404, "Beleg nicht gefunden.")
    assert_open(conn, rec["month_id"])
    with conn:
        conn.execute("DELETE FROM item WHERE receipt_id=?", (receipt_id,))
        conn.execute("DELETE FROM receipt WHERE id=?", (receipt_id,))
    if rec["image_path"]:
        p = settings.data_dir / rec["image_path"]
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    return {"action": "deleted", "id": receipt_id}


# --------------------------------------------------------------------------- Products (Rules Catalog)

@app.get("/api/products")
def api_list_products(
    q: str | None = None,
    category_id: int | None = None,
    source: str | None = None,
    flag: str | None = None,
    status: str | None = None,
    conflict: int | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    where, params = [], []
    if q:
        where.append("(lower(p.pattern) LIKE ? OR lower(p.norm_pattern) LIKE ?)")
        params += [f"%{q.lower()}%", f"%{q.lower()}%"]
    if category_id:
        where.append("p.category_id=?"); params.append(category_id)
    if source:
        where.append("p.source=?"); params.append(source)
    if flag:
        where.append("p.flag=?"); params.append(flag)
    if status:
        where.append("p.status=?"); params.append(status)
    if conflict is not None:
        where.append("p.conflict=?"); params.append(conflict)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM product p {where_sql}", params).fetchone()[0]

    sql = (
        f"SELECT p.*, c.name AS category_name, c.color AS category_color, c.key AS category_key "
        f"FROM product p JOIN category c ON c.id=p.category_id {where_sql} "
        f"ORDER BY p.conflict DESC, p.hits DESC, p.updated_at DESC LIMIT ? OFFSET ?"
    )
    rows = [dict(r) for r in conn.execute(sql, params + [limit, offset])]
    return {"total": total, "items": rows, "limit": limit, "offset": offset}


@app.post("/api/products", dependencies=[Depends(verify_pin)])
def api_create_product(p: ProductCreate, conn=Depends(get_db)):
    norm = normalize(p.pattern)
    if not norm:
        raise HTTPException(400, "Muster enthält keine verwertbaren Buchstaben.")
    if not conn.execute("SELECT 1 FROM category WHERE id=?", (p.category_id,)).fetchone():
        raise HTTPException(400, "Ungültige Kategorie.")
    ts = now_iso()
    try:
        cur = conn.execute(
            "INSERT INTO product(pattern, norm_pattern, category_id, store, source, status, flag, note, hits, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,0,?,?)",
            (p.pattern.strip(), norm, p.category_id, p.store or None, "manual", "confirmed", p.flag, p.note, ts, ts),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT p.*, c.name AS category_name, c.color AS category_color FROM product p "
            "JOIN category c ON c.id=p.category_id WHERE p.id=?",
            (cur.lastrowid,),
        ).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Ein Produktregel mit diesem Muster/Laden existiert bereits.")


@app.patch("/api/products/{prod_id}", dependencies=[Depends(verify_pin)])
def api_update_product(prod_id: int, p: ProductUpdate, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM product WHERE id=?", (prod_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Produkt nicht gefunden.")
    fields: dict[str, Any] = {"updated_at": now_iso()}
    if p.pattern is not None:
        norm = normalize(p.pattern)
        if not norm:
            raise HTTPException(400, "Muster ungültig.")
        fields["pattern"] = p.pattern.strip()
        fields["norm_pattern"] = norm
    if p.category_id is not None:
        if not conn.execute("SELECT 1 FROM category WHERE id=?", (p.category_id,)).fetchone():
            raise HTTPException(400, "Ungültige Kategorie.")
        fields["category_id"] = p.category_id
        fields["conflict"] = 0  # resolved
        fields["source"] = "manual"
    if "store" in p.model_fields_set:
        fields["store"] = p.store or None
    if p.flag is not None:
        fields["flag"] = p.flag
    if p.status is not None:
        fields["status"] = p.status
    if "note" in p.model_fields_set:
        fields["note"] = p.note
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE product SET {sets} WHERE id=?", (*fields.values(), prod_id))
    conn.commit()
    return dict(conn.execute(
        "SELECT p.*, c.name AS category_name, c.color AS category_color FROM product p "
        "JOIN category c ON c.id=p.category_id WHERE p.id=?",
        (prod_id,),
    ).fetchone())


@app.delete("/api/products/{prod_id}", dependencies=[Depends(verify_pin)])
def api_delete_product(prod_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM product WHERE id=?", (prod_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Produkt nicht gefunden.")
    conn.execute("DELETE FROM product WHERE id=?", (prod_id,))
    conn.commit()
    return {"action": "deleted", "id": prod_id}


@app.post("/api/products/bulk", dependencies=[Depends(verify_pin)])
def api_bulk_products(b: BulkProductUpdate, conn=Depends(get_db)):
    if not b.ids:
        return {"updated": 0}
    fields: dict[str, Any] = {"updated_at": now_iso()}
    if b.category_id is not None:
        fields["category_id"] = b.category_id
        fields["conflict"] = 0
        fields["source"] = "manual"
    if b.status is not None:
        fields["status"] = b.status
    if b.flag is not None:
        fields["flag"] = b.flag
    sets = ", ".join(f"{k}=?" for k in fields)
    placeholders = ",".join("?" * len(b.ids))
    conn.execute(f"UPDATE product SET {sets} WHERE id IN ({placeholders})", (*fields.values(), *b.ids))
    conn.commit()
    return {"updated": len(b.ids)}


@app.post("/api/products/reset-seed", dependencies=[Depends(verify_pin)])
def api_reset_seed(conn=Depends(get_db)):
    if not SEED_CSV.is_file():
        raise HTTPException(404, "Seed-Datei food-seed-de.csv nicht gefunden.")
    inserted = import_seed(conn, SEED_CSV, only_missing=True)
    conn.commit()
    return {"inserted": inserted, "message": f"{inserted} fehlende Seed-Einträge hinzugefügt."}


@app.get("/api/products/export-csv")
def api_export_products_csv(conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT p.pattern, c.key AS category, p.flag, p.store, p.source, p.status, p.hits, p.note "
        "FROM product p JOIN category c ON c.id=p.category_id ORDER BY c.sort, p.pattern"
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["pattern", "category", "flag", "store", "source", "status", "hits", "note"])
    for r in rows:
        writer.writerow([r["pattern"], r["category"], r["flag"], r["store"] or "", r["source"], r["status"], r["hits"], r["note"] or ""])
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="flatfood_products.csv"'},
    )


@app.post("/api/products/import-csv", dependencies=[Depends(verify_pin)])
async def api_import_products_csv(file: UploadFile = File(...), conn=Depends(get_db)):
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    cats = {c["key"]: c["id"] for c in list_categories(conn)}
    imported = 0
    ts = now_iso()
    with conn:
        for row in reader:
            pat = (row.get("pattern") or "").strip()
            cat_key = (row.get("category") or "").strip()
            if not pat or cat_key not in cats:
                continue
            norm = normalize(pat)
            if not norm:
                continue
            flag = "review" if (row.get("flag") or "").strip() == "review" else "sure"
            status_val = (row.get("status") or "confirmed").strip()
            store = (row.get("store") or "").strip() or None
            note = (row.get("note") or "").strip() or None
            conn.execute(
                "INSERT INTO product(pattern, norm_pattern, category_id, store, source, status, flag, note, hits, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,0,?,?) "
                "ON CONFLICT(norm_pattern, IFNULL(store, '')) DO UPDATE SET category_id=excluded.category_id, flag=excluded.flag, "
                "source='manual', status='confirmed', updated_at=excluded.updated_at",
                (pat, norm, cats[cat_key], store, "import", status_val, flag, note, ts, ts),
            )
            imported += 1
    return {"imported": imported}


# --------------------------------------------------------------------------- Payments

@app.post("/api/payments", dependencies=[Depends(verify_pin)])
def api_create_payment(p: PaymentCreate, conn=Depends(get_db)):
    check_month_id(p.month_id)
    ensure_month(conn, p.month_id)
    assert_open(conn, p.month_id)
    if p.from_id == p.to_id:
        raise HTTPException(400, "Sender und Empfänger dürfen nicht identisch sein.")
    persons = {r["id"] for r in conn.execute("SELECT id FROM person")}
    if p.from_id not in persons or p.to_id not in persons:
        raise HTTPException(400, "Person unbekannt.")
    cur = conn.execute(
        "INSERT INTO payment(month_id, from_id, to_id, cents, date, note) VALUES (?,?,?,?,?,?)",
        (p.month_id, p.from_id, p.to_id, p.cents, p.date or dt.date.today().isoformat(), p.note),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM payment WHERE id=?", (cur.lastrowid,)).fetchone())


@app.delete("/api/payments/{pay_id}", dependencies=[Depends(verify_pin)])
def api_delete_payment(pay_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM payment WHERE id=?", (pay_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Zahlung nicht gefunden.")
    assert_open(conn, row["month_id"])
    conn.execute("DELETE FROM payment WHERE id=?", (pay_id,))
    conn.commit()
    return {"action": "deleted", "id": pay_id}


# --------------------------------------------------------------------------- Stats & Backup

@app.get("/api/stats")
def api_get_stats(months: int = Query(24, ge=1, le=60), conn=Depends(get_db)):
    return stats(conn, months_back=months)


@app.get("/api/backup/sqlite")
def api_backup_sqlite():
    if not settings.db_path.is_file():
        raise HTTPException(404, "Datenbankdatei existiert noch nicht.")
    return FileResponse(
        settings.db_path,
        media_type="application/x-sqlite3",
        filename=f"flatfood_backup_{dt.date.today().isoformat()}.db",
    )


@app.post("/api/backup/restore", dependencies=[Depends(verify_pin)])
async def api_restore_sqlite(file: UploadFile = File(...)):
    contents = await file.read()
    if len(contents) < 100:
        raise HTTPException(400, "Datei ist zu klein oder leer.")
    if not contents.startswith(b"SQLite format 3\000"):
        raise HTTPException(400, "Ungültiges Dateiformat. Es muss eine SQLite .db Datei sein.")

    temp_path = settings.data_dir / "restore_test.db"
    temp_path.write_bytes(contents)

    try:
        import sqlite3
        test_conn = sqlite3.connect(str(temp_path))
        check = test_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            test_conn.close()
            temp_path.unlink(missing_ok=True)
            raise HTTPException(400, f"Datenbank-Integritätsprüfung fehlgeschlagen: {check}")

        tables = {r[0] for r in test_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {"person", "category", "receipt", "month"}
        missing = required - tables
        if missing:
            test_conn.close()
            temp_path.unlink(missing_ok=True)
            raise HTTPException(400, f"Backup-Datei ist unvollständig (Tabellen fehlen: {', '.join(missing)}).")
        test_conn.close()
    except Exception as e:
        if temp_path.is_file():
            temp_path.unlink(missing_ok=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(400, f"Fehler beim Überprüfen der Backup-Datei: {e}")

    # Safely create backup copy of currently active database before swapping
    if settings.db_path.is_file():
        backup_existing = settings.data_dir / f"flatfood_pre_restore_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(settings.db_path, backup_existing)

    # Move restored database into place
    shutil.move(temp_path, settings.db_path)

    # Clean up stale WAL / SHM files if any
    for suffix in ("-wal", "-shm"):
        wal_file = settings.data_dir / f"{settings.db_path.name}{suffix}"
        if wal_file.is_file():
            try:
                wal_file.unlink()
            except OSError:
                pass

    # Ensure schema migrations if any
    init_db(settings.db_path, None)

    return {"status": "ok", "message": "Datenbank erfolgreich wiederhergestellt!"}


@app.get("/api/backup/json")
def api_backup_json(conn=Depends(get_db)):
    data = {
        "persons": list_persons(conn),
        "categories": list_categories(conn),
        "months": [dict(r) for r in conn.execute("SELECT * FROM month ORDER BY id")],
        "presence": [dict(r) for r in conn.execute("SELECT * FROM presence ORDER BY month_id, person_id")],
        "receipts": [receipt_detail(conn, r["id"]) for r in conn.execute("SELECT id FROM receipt ORDER BY id")],
        "payments": [dict(r) for r in conn.execute("SELECT * FROM payment ORDER BY id")],
        "products": [dict(r) for r in conn.execute("SELECT * FROM product ORDER BY id")],
    }
    return JSONResponse(data)


# --------------------------------------------------------------------------- Static Files / PWA

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        file = WEB_DIR / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(WEB_DIR / "index.html")
