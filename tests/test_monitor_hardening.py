from __future__ import annotations

import json
from datetime import date, timedelta

from src.monitor import _truthy, _valid_https_url, run_health_check


def _write_minimal_artifacts(tmp_path, snapshot_date: date) -> None:
    data = tmp_path / "data"
    models = tmp_path / "models"
    data.mkdir()
    models.mkdir()
    (data / "official_gadgets.csv").write_text(
        "product_id,entity_key,manufacturer,name,model_number,category,source_name,source_url,market_date,is_primary_record\n"
        "p1,e1,Maker,Device,M1,Laptop,Registry,https://example.com/device,2026-01-01,False\n"
    )
    (data / "source_metadata.json").write_text(
        json.dumps(
            {
                "snapshot_id": "snapshot-1",
                "snapshot_date": snapshot_date.isoformat(),
                "record_count": 1,
                "unique_entities": 1,
            }
        )
    )
    (models / "metrics.json").write_text(json.dumps({"snapshot_id": "snapshot-1"}))


def test_monitor_rejects_future_snapshots_and_string_false_primary(tmp_path):
    today = date(2026, 9, 1)
    _write_minimal_artifacts(tmp_path, today + timedelta(days=1))
    report = run_health_check(root=tmp_path, write_report=False, today=today)
    assert report["status"] == "attention"
    assert report["checks"]["snapshot_freshness"]["ok"] is False
    assert report["checks"]["one_primary_per_entity"]["primary_entities"] == 0
    assert _truthy("False") is False


def test_monitor_reports_malformed_artifacts_instead_of_crashing(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "data" / "official_gadgets.csv").write_text("")
    (tmp_path / "data" / "source_metadata.json").write_text("{")
    (tmp_path / "models" / "metrics.json").write_text("[]")
    report = run_health_check(root=tmp_path, write_report=True)
    assert report["status"] == "attention"
    assert report["checks"]["input_artifacts"]["ok"] is False
    assert json.loads((tmp_path / "data" / "health_report.json").read_text())["status"] == "attention"


def test_monitor_requires_credential_free_https_source_urls():
    assert _valid_https_url("https://example.com/source")
    assert not _valid_https_url("http://example.com/source")
    assert not _valid_https_url("https://user:secret@example.com/source")
    assert not _valid_https_url("https://127.0.0.1/source")
    assert not _valid_https_url("https://localhost/source")
    assert not _valid_https_url(" https://example.com/source")
