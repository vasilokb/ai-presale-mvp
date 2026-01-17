import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "backend"))

from app import worker  # noqa: E402


def test_load_schema_handles_bom(monkeypatch, tmp_path):
    schema_file = tmp_path / "schema.json"
    schema_file.write_bytes(b"\xef\xbb\xbf{\"type\":\"object\"}")
    monkeypatch.setattr(worker, "SCHEMA_PATH", schema_file)

    assert worker.load_schema() == {"type": "object"}
