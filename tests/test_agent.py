import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def use_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_reports.db")
    monkeypatch.setenv("DB_PATH", db_file)
    # Patch the module-level DB_PATH used by database.py connections
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    db_mod.init_db()
    yield


@pytest.fixture
def client(use_test_db):
    from app.api.server import app
    with TestClient(app) as c:
        yield c


# ── DB layer tests ───────────────────────────────────────────────────────────

def test_create_and_fetch_report():
    from app.db.database import create_report_record, get_report_by_id

    rid = create_report_record("https://linear.app")
    assert isinstance(rid, int)
    report = get_report_by_id(rid)
    assert report is not None
    assert report["status"] == "running"
    assert report["company_url"] == "https://linear.app"


def test_update_report_to_done():
    from app.db.database import create_report_record, get_report_by_id, update_report

    rid = create_report_record("https://notion.so")
    payload = {"company_name": "Notion", "overview": "A productivity tool."}
    update_report(rid, status="done", report_json=json.dumps(payload), company_name="Notion")
    report = get_report_by_id(rid)
    assert report["status"] == "done"
    assert report["company_name"] == "Notion"
    assert report["report"]["company_name"] == "Notion"


def test_cache_miss_returns_none():
    from app.db.database import get_cached_report

    result = get_cached_report("https://nonexistent-company-xyz.io")
    assert result is None


# ── Tool tests ───────────────────────────────────────────────────────────────

def test_search_web_returns_results():
    from app.tools.tools import search_web

    mock_response = {
        "answer": "Linear is a project management tool.",
        "results": [
            {
                "title": "Linear App",
                "url": "https://linear.app",
                "content": "Linear is a modern project management tool for software teams.",
            }
        ],
    }

    mock_client = MagicMock()
    mock_client.search.return_value = mock_response

    with patch("app.tools.tools.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            result = search_web.invoke({"query": "Linear app overview"})

    assert "answer" in result
    assert isinstance(result["results"], list)
    assert result["results"][0]["title"] == "Linear App"


def test_search_web_raises_on_failure():
    from app.tools.tools import search_web

    mock_client = MagicMock()
    mock_client.search.side_effect = Exception("Tavily API error")

    with patch("app.tools.tools.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            with pytest.raises(Exception, match="Tavily API error"):
                search_web.invoke({"query": "failing query"})


def test_get_company_metadata_success():
    from app.tools.tools import get_company_metadata

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"name": "Linear", "domain": "linear.app"}]

    with patch("app.tools.tools.httpx.get", return_value=mock_resp):
        result = get_company_metadata.invoke({"domain": "linear.app"})

    assert result["found"] is True
    assert result["name"] == "Linear"
    assert result["domain"] == "linear.app"


def test_get_company_metadata_404():
    from app.tools.tools import get_company_metadata

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = []

    with patch("app.tools.tools.httpx.get", return_value=mock_resp):
        result = get_company_metadata.invoke({"domain": "unknown-xyz-404.io"})

    assert result["found"] is False


# ── API endpoint tests ───────────────────────────────────────────────────────

def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "runs" in data


def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "agent_runs_total" in resp.text
    assert "agent_uptime_seconds" in resp.text


def test_get_reports_empty(client):
    resp = client.get("/reports")
    assert resp.status_code == 200
    assert resp.json() == {"reports": []}


def test_get_report_404(client):
    resp = client.get("/reports/9999")
    assert resp.status_code == 404


def test_analyze_202_async(client):
    with patch("app.api.server.run_analysis_task") as mock_task:
        mock_task.return_value = None
        # Also patch background task execution to be synchronous-safe
        resp = client.post(
            "/analyze",
            json={"company_url": "https://figma.com", "force_refresh": True},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "running"
    assert isinstance(data["report_id"], int)


def test_analyze_cache_hit(client):
    from app.db.database import create_report_record, update_report

    url = "https://cached-company.io"
    rid = create_report_record(url)
    payload = {"company_name": "Cached Co", "overview": "Already analysed."}
    update_report(
        rid,
        status="done",
        report_json=json.dumps(payload),
        company_name="Cached Co",
    )

    resp = client.post("/analyze", json={"company_url": url, "force_refresh": False})
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "done"
    assert data["report_id"] == rid
