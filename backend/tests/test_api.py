import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "backend"))

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


def build_test_session():
    engine = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_presale():
    TestingSessionLocal = build_test_session()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    response = client.post("/api/v1/presales", json={"name": "Test presale"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test presale"
    uuid.UUID(data["id"])
    app.dependency_overrides.clear()


def test_start_document_no_files():
    TestingSessionLocal = build_test_session()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    presale_response = client.post("/api/v1/presales", json={"name": "Test presale"})
    presale_id = presale_response.json()["id"]
    payload = {
        "presale_id": presale_id,
        "prompt": "Разбей требования на эпики и задачи и оцени PERT",
        "params": {"roles": ["Backend", "Frontend"], "round_to_hours": 0.5},
    }
    response = client.post("/api/v1/documents/start", json=payload)
    assert response.status_code == 400
    assert response.json() == {"error": "no_files_uploaded"}
    app.dependency_overrides.clear()
