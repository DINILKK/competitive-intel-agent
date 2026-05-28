import json
import logging
import logging.config
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

# ── Structured JSON logging (must be configured before anything uses loggers) ──
logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "format": '{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
            }
        },
        "root": {"level": "INFO", "handlers": ["console"]},
    }
)

logger = logging.getLogger(__name__)

from app.agent.graph import run_agent  # noqa: E402  (after logging setup)
from app.db.database import (  # noqa: E402
    create_report_record,
    get_cached_report,
    get_report_by_id,
    init_db,
    list_reports,
    update_report,
)

# ── Module-level counters ──────────────────────────────────────────────────
APP_START = time.time()
RUN_COUNTER: dict[str, int] = {"total": 0, "success": 0, "error": 0}


# ── Lifespan ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info('"startup complete"')
    yield
    logger.info('"shutdown"')


# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Competitive Intelligence Agent",
    description="LangGraph-powered company research API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    company_url: str
    force_refresh: bool = False


class AnalyzeResponse(BaseModel):
    report_id: int
    status: str
    message: str


# ── Background task ────────────────────────────────────────────────────────
def run_analysis_task(report_id: int, company_url: str) -> None:
    RUN_COUNTER["total"] += 1
    try:
        result = run_agent(company_url, force_refresh=force_refresh)
        report = result["report"]
        update_report(
            report_id,
            status="done",
            report_json=json.dumps(report),
            company_name=report.get("company_name", ""),
            token_cost=result["token_cost_usd"],
            run_ms=result["run_ms"],
        )
        RUN_COUNTER["success"] += 1
        logger.info(json.dumps({
            "event": "analysis_done",
            "report_id": report_id,
            "company_url": company_url,
            "token_cost_usd": result["token_cost_usd"],
            "run_ms": result["run_ms"],
        }))
    except Exception as exc:
        update_report(report_id, status="error", error_log=str(exc))
        RUN_COUNTER["error"] += 1
        logger.error(json.dumps({
            "event": "analysis_error",
            "report_id": report_id,
            "company_url": company_url,
            "error": str(exc),
        }))


# ── Routes ──────────────────────────────────────────────────────────────────
@app.post("/analyze", response_model=AnalyzeResponse, status_code=202)
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    if not req.force_refresh:
        cached = get_cached_report(req.company_url)
        if cached:
            return AnalyzeResponse(
                report_id=cached["id"],
                status="done",
                message="Returning cached report (< 24 h old).",
            )

    report_id = create_report_record(req.company_url)
    background_tasks.add_task(run_analysis_task, report_id, req.company_url, req.force_refresh)
    return AnalyzeResponse(
        report_id=report_id,
        status="running",
        message="Analysis started. Poll GET /reports/{report_id} for results.",
    )


@app.get("/reports")
def get_reports(limit: int = Query(default=20, ge=1, le=100)):
    return {"reports": list_reports(limit=limit)}


@app.get("/reports/{report_id}")
def get_report(report_id: int):
    report = get_report_by_id(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return report


@app.get("/health")
def health():
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - APP_START, 1),
        "runs": RUN_COUNTER,
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    uptime = round(time.time() - APP_START, 1)
    lines = [
        "# HELP agent_runs_total Total runs",
        "# TYPE agent_runs_total counter",
        f'agent_runs_total{{status="total"}} {RUN_COUNTER["total"]}',
        f'agent_runs_total{{status="success"}} {RUN_COUNTER["success"]}',
        f'agent_runs_total{{status="error"}} {RUN_COUNTER["error"]}',
        "# HELP agent_uptime_seconds Uptime",
        "# TYPE agent_uptime_seconds gauge",
        f"agent_uptime_seconds {uptime}",
    ]
    return "\n".join(lines) + "\n"
