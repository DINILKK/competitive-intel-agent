# Competitive Intelligence Agent

A multi-step AI agent that researches any company and returns a structured competitive report in ~60 seconds — built to demonstrate production MLOps patterns, not just LLM wrappers.

```bash
curl -X POST https://competitive-intel-agent.fly.dev/analyze \
  -H "Content-Type: application/json" \
  -d '{"company_url":"https://linear.app"}'
```

```json
{
  "company_name": "Linear",
  "overview": "Linear is a project management tool built for high-performance engineering teams...",
  "competitors": ["Jira", "Asana", "Height"],
  "recent_news": "Raised $35M Series B, launched Asks feature for cross-team requests...",
  "strengths": ["Fast keyboard-driven UX", "Strong engineering culture following"],
  "weaknesses": ["Limited enterprise features vs Jira", "Small team size"],
  "sources": ["https://linear.app", "https://techcrunch.com/..."]
}
```

---

## What makes this production-grade

**Explicit agent graph** — built with LangGraph `StateGraph`, not a chain. Every node, edge, and routing decision is visible and testable. The graph separates concerns: `check_cache → gather → synthesize → write`.

**Cost tracking per run** — every report stores `token_cost_usd` calculated from actual input/output token counts. A budget guard kills the run if cost exceeds $0.10, preventing runaway spend.

**24-hour idempotent caching** — before running any LLM calls, the agent checks SQLite for a recent report on the same URL. Duplicate requests return instantly at $0.00.

**Async background processing** — `POST /analyze` returns `202` immediately with a `report_id`. The agent runs in a background task. Poll `GET /reports/{id}` for results. No blocking, no timeouts.

**Structured JSON logging** — every tool call logs `event`, `latency_ms`, and success/failure in JSON. Failures are visible in `/metrics` without digging through unstructured logs.

**Graceful degradation** — if a tool fails, the error is accumulated in state and the agent continues with what it has. If JSON parsing fails on the LLM output, the raw text is preserved rather than losing the run entirely.

---

## Architecture

```
POST /analyze
      │
      ▼
┌─────────────┐   cache hit?   ┌──────────────────────────────┐
│ check_cache │ ──── yes ─────►│ return existing report ($0)  │
└──────┬──────┘                └──────────────────────────────┘
       │ miss
       ▼
┌─────────────┐   3 parallel tool calls:
│   gather    │ ── get_company_metadata (Clearbit)
└──────┬──────┘ ── search_web × 3 (Tavily: overview, competitors, news)
       │
       ▼
┌─────────────┐   LLM synthesis on gathered context (~1000 tokens in)
│  synthesize │ ── Groq llama-3.3-70b → structured JSON out
└──────┬──────┘
       │
       ▼
┌─────────────┐   brace-matched JSON extraction → SQLite persist
│    write    │ ── token cost calculated → run_complete logged
└─────────────┘
```

---

## Stack

| Layer | Choice | Reason |
|---|---|---|
| Agent | LangGraph StateGraph | Explicit graph; nodes are unit-testable in isolation |
| LLM | Groq llama-3.3-70b | Free tier, 10× cheaper than GPT-4, fast enough for sync UX |
| Search | Tavily | Built for agents; returns clean snippets not raw HTML |
| Metadata | Clearbit autocomplete | Free, no key needed, returns structured company data |
| Database | SQLite | Zero ops overhead; persistent volume on Fly.io |
| API | FastAPI | Async, auto-docs at `/docs`, Pydantic validation |
| Deploy | Fly.io (Frankfurt) | Close to EU users; free hobby tier; persistent volumes |
| CI | GitHub Actions | Tests on every PR; Docker build on merge to main |

---

## Quick start

```bash
git clone https://github.com/DINILKK/competitive-intel-agent.git
cd competitive-intel-agent
cp .env.example .env        # add your two API keys
pip install -r requirements.txt
python main.py
```

Get free API keys (no credit card, 2 minutes):
- **Groq** — [console.groq.com](https://console.groq.com) — 14,400 req/day free
- **Tavily** — [app.tavily.com](https://app.tavily.com) — 1,000 searches/month free

Trigger an analysis:
```bash
# Start a run (returns immediately)
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{"company_url":"https://stripe.com"}'

# Poll for result (ready in ~60s)
curl http://localhost:8080/reports/1
```

---

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/analyze` | Start analysis — returns `202` + `report_id` immediately |
| `GET` | `/reports/{id}` | Fetch report by ID — includes `token_cost_usd` and `run_ms` |
| `GET` | `/reports` | List all past reports |
| `GET` | `/health` | Uptime + run counters (total / success / error) |
| `GET` | `/metrics` | Prometheus-format counters |

```json
POST /analyze
{
  "company_url": "https://linear.app",
  "force_refresh": false
}
```

---

## Deploy to Fly.io

```bash
fly auth login
fly apps create competitive-intel-agent
fly volumes create data --size 1 --region fra
fly secrets set GROQ_API_KEY=gsk_... TAVILY_API_KEY=tvly-...
fly deploy
```

Live at `https://competitive-intel-agent.fly.dev`.

---

## Tests

```bash
DB_PATH=:memory: pytest tests/ -v
```

13 tests, zero real API calls — all external dependencies mocked. Tests cover the DB layer, each tool, all 5 API routes, cache hit/miss logic, and background task triggering.

---

## Monitoring

Each report stores exact token spend:

```json
{
  "token_cost_usd": 0.000815,
  "run_ms": 11529
}
```

`/health` shows live run counters. `/metrics` is Prometheus-compatible — wire to Grafana if needed. Every tool call logs `latency_ms` and `event: tool_success | tool_failure` in structured JSON.

---

## License

MIT
