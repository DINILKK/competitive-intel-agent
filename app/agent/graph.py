"""
LangGraph agent — tools are called directly (no LLM tool binding).
The LLM is only used for synthesis, avoiding Groq tool_use_failed errors.

Graph flow:
  check_cache → (hit) → END
              → (miss) → gather → synthesize → write → END
"""

import json
import logging
import operator
import os
import re
import time
from typing import Annotated, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.tools.tools import get_company_metadata, query_past_reports, search_web

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    company_url: str
    messages: Annotated[list, operator.add]
    errors: Annotated[list, operator.add]
    input_tokens: Annotated[int, operator.add]
    output_tokens: Annotated[int, operator.add]
    token_cost_usd: float
    final_report: dict
    force_refresh: bool
    # Internal — not exposed in public result
    _gathered: dict
    _cache_hit: bool
    _start_ms: float
    _synthesis_text: str


# ---------------------------------------------------------------------------
# LLM (no tools bound — synthesis only)
# ---------------------------------------------------------------------------

def _get_llm() -> ChatGroq:
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.environ["GROQ_API_KEY"],
        temperature=0.1,
    )


SYSTEM_PROMPT = """\
You are a competitive intelligence analyst. Given research data about a company,
write a JSON object (no markdown fences) with exactly these keys:
  company_name, overview, products, competitors (list of top 3 names),
  recent_news, strengths, weaknesses, sources (list of URLs used).
Be factual and concise. Output ONLY the JSON object."""


# ---------------------------------------------------------------------------
# Node 1 — Check cache
# ---------------------------------------------------------------------------

def check_cache_node(state: AgentState) -> dict:
    if state.get("force_refresh"):
        return {"_cache_hit": False}
    url = state["company_url"]
    try:
        result = query_past_reports.invoke({"company_url": url})
        if result.get("found"):
            logger.info(json.dumps({"event": "cache_hit", "company_url": url}))
            return {
                "_cache_hit": True,
                "final_report": result["report"],
                "token_cost_usd": 0.0,
            }
    except Exception as exc:
        logger.warning(json.dumps({"event": "cache_check_error", "error": str(exc)}))
    return {"_cache_hit": False}


def route_cache(state: AgentState) -> Literal["gather", "__end__"]:
    if state.get("_cache_hit"):
        return "__end__"
    return "gather"


# ---------------------------------------------------------------------------
# Node 2 — Gather data (direct Python calls, no LLM tool binding)
# ---------------------------------------------------------------------------

def gather_node(state: AgentState) -> dict:
    url = state["company_url"]
    gathered: dict = {"metadata": {}, "searches": []}
    errors: list = []

    # 1. Company metadata
    try:
        meta = get_company_metadata.invoke({"domain": url})
        gathered["metadata"] = meta
        company_name = meta.get("name") or url
    except Exception as exc:
        errors.append(f"metadata: {exc}")
        company_name = url

    # 2. Three targeted web searches
    queries = [
        f"{company_name} company overview products 2024",
        f"{company_name} competitors market analysis",
        f"{company_name} recent news funding 2024 2025",
    ]
    for q in queries:
        try:
            result = search_web.invoke({"query": q})
            gathered["searches"].append({"query": q, "result": result})
        except Exception as exc:
            errors.append(f"search '{q}': {exc}")
            gathered["searches"].append({"query": q, "result": {}, "error": str(exc)})

    return {"_gathered": gathered, "errors": errors}


# ---------------------------------------------------------------------------
# Node 3 — Synthesize with LLM
# ---------------------------------------------------------------------------

def synthesize_node(state: AgentState) -> dict:
    llm = _get_llm()
    gathered = state.get("_gathered", {})
    url = state["company_url"]

    meta = gathered.get("metadata", {})
    searches = gathered.get("searches", [])

    # Build a compact context string (keep under 3000 chars for token efficiency)
    ctx_parts = [f"Company URL: {url}"]
    if meta.get("found"):
        ctx_parts.append(f"Name: {meta.get('name')} | Domain: {meta.get('domain')}")

    for s in searches:
        r = s.get("result", {})
        ctx_parts.append(f"\nSearch: {s['query']}")
        if r.get("answer"):
            ctx_parts.append(f"Answer: {r['answer'][:300]}")
        for res in r.get("results", [])[:3]:
            ctx_parts.append(
                f"  - {res.get('title','')}: {res.get('content','')[:200]} ({res.get('url','')})"
            )

    context = "\n".join(ctx_parts)[:3500]

    user_msg = HumanMessage(
        content=f"Research data:\n{context}\n\nWrite the JSON competitive intelligence report."
    )
    messages = [SystemMessage(content=SYSTEM_PROMPT), user_msg]

    response = llm.invoke(messages)

    meta = response.usage_metadata or {}
    in_tok = meta.get("input_tokens", 0)
    out_tok = meta.get("output_tokens", 0)

    logger.info(json.dumps({
        "event": "synthesize_node",
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }))

    return {
        "messages": messages + [response],
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "_synthesis_text": response.content,
    }



#new file given by claude 
def _extract_json(text: str) -> dict:
    """
    Try multiple strategies to extract a JSON object from LLM output.
    Logs exactly what it sees so failures are debuggable.
    """
    # Strategy 1: strip markdown fences, try direct parse
    cleaned = re.sub(r"```json\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: brace-matching (handles trailing text after closing })
    depth = 0
    start = None
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = cleaned[start : i + 1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    break

    logger.warning(json.dumps({
        "event": "json_parse_failed",
        "raw_preview": text[:400],
    }))
    return {}
# ---------------------------------------------------------------------------
# Node 4 — Write / parse report
# ---------------------------------------------------------------------------

def write_node(state: AgentState) -> dict:
    start_ms = state.get("_start_ms", time.time() * 1000)
    raw_text = state.get("_synthesis_text", "")

    # Strip ```json fences
    cleaned = re.sub(r"```json\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()

    # Extract first JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    json_str = match.group(0) if match else cleaned

    # try:
    #     report = json.loads(json_str)
    # except (json.JSONDecodeError, ValueError):
    #     report = {
    #         "company_name": state["company_url"],
    #         "overview": raw_text,
    #         "parse_error": True,
    #     }
    report = _extract_json(raw_text)

    if not report:
        report = {
            "company_name": state["company_url"],
            "overview": raw_text,
            "parse_error": True,
        }

    in_tok = state.get("input_tokens", 0)
    out_tok = state.get("output_tokens", 0)
    cost = (in_tok / 1000 * 0.00059) + (out_tok / 1000 * 0.00079)
    run_ms = int(time.time() * 1000 - start_ms)

    logger.info(json.dumps({
        "event": "run_complete",
        "token_cost_usd": round(cost, 6),
        "run_ms": run_ms,
        "error_count": len(state.get("errors", [])),
    }))

    return {
        "final_report": report,
        "token_cost_usd": round(cost, 6),
    }


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def _build_graph():
    g = StateGraph(AgentState)

    g.add_node("check_cache", check_cache_node)
    g.add_node("gather", gather_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("write", write_node)

    g.set_entry_point("check_cache")
    g.add_conditional_edges("check_cache", route_cache, {"gather": "gather", "__end__": END})
    g.add_edge("gather", "synthesize")
    g.add_edge("synthesize", "write")
    g.add_edge("write", END)

    return g.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_agent(company_url: str, force_refresh: bool = False) -> dict:
    start_ms = time.time() * 1000
    graph = _get_graph()

    initial_state: AgentState = {
        "company_url": company_url,
        "messages": [],
        "errors": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "token_cost_usd": 0.0,
        "final_report": {},
        "_gathered": {},
        "_cache_hit": False,
        "force_refresh": force_refresh,
        "_start_ms": start_ms,
        "_synthesis_text": "",
    }

    result = graph.invoke(initial_state)
    run_ms = int(time.time() * 1000 - start_ms)

    return {
        "report": result.get("final_report", {}),
        "token_cost_usd": result.get("token_cost_usd", 0.0),
        "run_ms": run_ms,
        "errors": result.get("errors", []),
    }
