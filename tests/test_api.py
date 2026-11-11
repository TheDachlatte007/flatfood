import io
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, get_db
import app.main as main_mod
from app.db import connect, init_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    receipts_dir = data_dir / "receipts"
    receipts_dir.mkdir()
    db_path = data_dir / "test.db"

    test_settings = Settings(
        data_dir=data_dir,
        gemini_api_key=None,
        gemini_model="gemini-3.8-flash",
        parser="mock",
        ollama_url="http://localhost:11434",
        ollama_model="qwen2.5vl:7b",
        app_pin=None,
        max_upload_mb=15,
    )
    monkeypatch.setattr(main_mod, "settings", test_settings)

    # Initialize db with seed categories
    init_db(db_path, None)

    def override_get_db():
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_meta_and_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    r = client.get("/api/meta")
    assert r.status_code == 200
    assert r.json()["has_persons"] is False


def test_person_crud(client):
    # Create person
    r = client.post("/api/persons", json={"name": "Daniel", "color": "#10b981", "tags": ["vegan"]})
    assert r.status_code == 200
    p1 = r.json()
    assert p1["name"] == "Daniel"
    assert p1["tags"] == ["vegan"]

    # Create second person
    r = client.post("/api/persons", json={"name": "Mitbewohner", "color": "#3b82f6", "tags": ["fleisch", "milch", "alkohol"]})
    assert r.status_code == 200
    p2 = r.json()

    # List
    r = client.get("/api/persons")
    assert len(r.json()) == 2

    # Update
    r = client.patch(f"/api/persons/{p1['id']}", json={"tags": ["vegan", "bio-kulanz"]})
    assert r.status_code == 200
    assert "bio-kulanz" in r.json()["tags"]


def test_receipt_mock_upload_and_confirm(client):
    # 1. Create 2 persons
    p1 = client.post("/api/persons", json={"name": "Daniel", "tags": []}).json()
    p2 = client.post("/api/persons", json={"name": "Mitbewohner", "tags": ["fleisch", "alkohol", "milch"]}).json()

    # 2. Upload dummy image with parser=mock
    fake_img = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"fakeimagecontent" * 10
    files = {"image": ("bon.jpg", fake_img, "image/jpeg")}
    data = {"buyer_id": p1["id"], "month_id": "2026-11"}
    r = client.post("/api/receipts/upload", files=files, data=data)
    assert r.status_code == 200
    rec = r.json()
    assert rec["status"] == "draft"
    assert rec["store"] == "Edeka"
    assert len(rec["items"]) > 0

    # 3. Check items classified
    item_names = [i["name"] for i in rec["items"]]
    assert "Bio Hafermilch 1L" in item_names

    # 4. Confirm receipt -> auto learns rules
    r = client.post(f"/api/receipts/{rec['id']}/confirm")
    assert r.status_code == 200
    confirmed = r.json()
    assert confirmed["status"] == "confirmed"
    assert "learn_result" in confirmed

    # 5. Set presence
    r = client.post("/api/months/2026-11/presence", json={"days": {p1["id"]: 25, p2["id"]: 25}})
    assert r.status_code == 200
    month = r.json()
    assert month["total_cents"] > 0
    assert len(month["transfers"]) >= 0


def test_product_rules_crud(client):
    cats = client.get("/api/categories").json()
    basis_cat = next(c["id"] for c in cats if c["key"] == "basis")

    # Create product rule
    r = client.post("/api/products", json={"pattern": "Haferdrink Vanille", "category_id": basis_cat})
    assert r.status_code == 200
    prod = r.json()
    assert prod["norm_pattern"] == "haferdrink vanille"

    # Search
    r = client.get("/api/products?q=vanille")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    # Bulk update
    r = client.post("/api/products/bulk", json={"ids": [prod["id"]], "flag": "review"})
    assert r.status_code == 200

    # Export CSV
    r = client.get("/api/products/export-csv")
    assert r.status_code == 200
    assert "haferdrink vanille" in r.text.lower()


def test_backup_sqlite_download_and_restore(client):
    # 1. Download active SQLite backup
    r = client.get("/api/backup/sqlite")
    assert r.status_code == 200
    db_bytes = r.content
    assert db_bytes.startswith(b"SQLite format 3\000")

    # 2. Try restore with invalid file (should fail)
    files = {"file": ("corrupt.db", b"not-a-sqlite-file", "application/x-sqlite3")}
    r_bad = client.post("/api/backup/restore", files=files)
    assert r_bad.status_code == 400

    # 3. Restore valid backup
    files_good = {"file": ("backup.db", db_bytes, "application/x-sqlite3")}
    r_good = client.post("/api/backup/restore", files=files_good)
    assert r_good.status_code == 200
    assert r_good.json()["status"] == "ok"
