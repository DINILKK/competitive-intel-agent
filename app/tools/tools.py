import json
import logging
import os
import time
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool
from tavily import TavilyClient

from app.db.database import get_cached_report

logger = logging.getLogger(__name__)


def _log(event: str, tool_name: str, latency_ms: float, **extra) -> None:
    record = {
        "event": event,
        "tool": tool_name,
        "latency_ms": round(latency_ms, 2),
        **extra,
    }
    logger.info(json.dumps(record))


@tool
def search_web(query: str) -> dict:
    """Search the web for competitive intelligence about a company or topic."""
    start = time.perf_counter()
    tool_name = "search_web"
    try:
        api_key = os.environ["TAVILY_API_KEY"]
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=5,
            search_depth="basic",
            include_answer=True,
        )
        latency = (time.perf_counter() - start) * 1000
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": (r.get("content") or "")[:400],
            }
            for r in response.get("results", [])
        ]
        payload = {
            "answer": response.get("answer", ""),
            "results": results,
        }
        _log("tool_success", tool_name, latency, query=query, result_count=len(results))
        return payload
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        _log("tool_failure", tool_name, latency, query=query, error=str(exc))
        raise


@tool
def get_company_metadata(domain: str) -> dict:
    """Fetch company metadata (name, domain) from Clearbit autocomplete."""
    start = time.perf_counter()
    tool_name = "get_company_metadata"
    try:
        parsed = urlparse(domain if "://" in domain else f"https://{domain}")
        clean_domain = parsed.netloc or parsed.path
        clean_domain = clean_domain.split("/")[0]

        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={clean_domain}"
        resp = httpx.get(url, timeout=8.0)

        latency = (time.perf_counter() - start) * 1000

        if resp.status_code != 200 or not resp.json():
            _log("tool_success", tool_name, latency, domain=clean_domain, found=False)
            return {"found": False, "domain": clean_domain}

        first = resp.json()[0]
        payload = {
            "found": True,
            "name": first.get("name", ""),
            "domain": first.get("domain", clean_domain),
        }
        _log("tool_success", tool_name, latency, domain=clean_domain, found=True)
        return payload

    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        _log("tool_failure", tool_name, latency, domain=domain, error=str(exc))
        return {"found": False, "domain": domain, "error": str(exc)}


@tool
def query_past_reports(company_url: str) -> dict:
    """Check if a fresh cached report exists for a company URL."""
    start = time.perf_counter()
    tool_name = "query_past_reports"
    try:
        cached = get_cached_report(company_url)
        latency = (time.perf_counter() - start) * 1000
        if cached:
            _log("tool_success", tool_name, latency, company_url=company_url, found=True)
            return {
                "found": True,
                "report": cached.get("report", {}),
                "created_at": cached.get("created_at", ""),
            }
        _log("tool_success", tool_name, latency, company_url=company_url, found=False)
        return {"found": False}
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        _log("tool_failure", tool_name, latency, company_url=company_url, error=str(exc))
        raise


ALL_TOOLS = [search_web, get_company_metadata, query_past_reports]
