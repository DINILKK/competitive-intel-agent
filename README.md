# Competitive Intelligence Agent

> LangGraph-powered API that researches any company and returns a structured competitive intelligence report in ~60 seconds.

---

## What it does

- **Researches any company in ~60 seconds** using live web search (Tavily) + public APIs
- **Tracks token cost per run** — typically $0.001–$0.005 per report using Groq's free LLM tier
- **Caches results 24 hours** — avoids duplicate API spend on repeated queries for the same company

---

## Tech Stack

| Layer      | Technology                  | Why                                                     |
|------------|-----------------------------|---------------------------------------------------------|
| Agent      | LangGraph StateGraph        | Explicit graph topology; easy to extend with new nodes  |
| LLM        | Groq llama-3.3-70b-versatile (free) | ~10× cheaper than GPT-4            |
| Search     | Tavily                      | Purpose-built for LLM agents; returns clean snippets    |
| Database   | SQLite                      | Zero-dependency persistence; perfect for single-instance|
| API        | FastAPI                     | Async, auto-docs, Pydantic validation out of the box    |
| Deploy     | Fly.io                      | European region (Frankfurt); free hobby tier available  |
| CI         | GitHub Actions              | Runs tests + builds Docker image on every push to main  |

---

## Architecture

```
POST /analyze
      │
      ▼
  ┌─────────┐     tool_calls?     ┌───────────┐
  │  plan   │ ─── yes ──────────► │   tools   │
  └─────────┘                     └─────┬─────┘
      │ no                              │
      │                      budget ok? │
      │                     ┌── yes ────┘
      ▼                     ▼
  ┌─────────┐     tool_calls?     ┌───────────┐
  │  write  │ ◄── no ─────────── │  reflect  │
  └─────────┘                     └───────────┘
      │
      ▼
  JSON report saved to SQLite → returned via GET /reports/{id}
```

**Graph flow:** `plan → [tools] → reflect → [tools] → write → response`

Budget guard: if cumulative token cost exceeds $0.10 or errors ≥ 3, the agent skips reflect and goes straight to write.

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/your-username/competitive-intel-agent.git
cd competitive-intel-agent

# 2. Copy env template and fill in your keys
cp .env.example .env
# Edit .env — add GROQ_API_KEY and TAVILY_API_KEY

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the server
python main.py

# 5. Trigger an analysis
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{"company_url":"https://linear.app"}'

# Poll for the result
curl http://localhost:8080/reports/1
```

---

## Deploy to Fly.io

```bash
# 1. Authenticate
fly auth login

# 2. Create the app (one-time)
fly apps create competitive-intel-agent

# 3. Set secrets
fly secrets set GROQ_API_KEY=gsk_... TAVILY_API_KEY=tvly-...

# 4. Deploy
fly deploy
```

The app will be live at `https://competitive-intel-agent.fly.dev`.

---

## API Reference

| Method | Path                  | Description                                              |
|--------|-----------------------|----------------------------------------------------------|
| POST   | `/analyze`            | Start an analysis (returns 202 + report_id immediately)  |
| GET    | `/reports`            | List all reports (latest first, `?limit=20`)             |
| GET    | `/reports/{id}`       | Fetch a single report by ID (404 if not found)           |
| GET    | `/health`             | Service health + uptime + run counters                   |
| GET    | `/metrics`            | Prometheus-format counters for scraping                  |

**POST `/analyze` request body:**
```json
{
  "company_url": "https://linear.app",
  "force_refresh": false
}
```
Set `force_refresh: true` to bypass the 24-hour cache.

---

## Monitoring

**`/health`** returns:
```json
{
  "status": "ok",
  "uptime_seconds": 142.3,
  "runs": {"total": 5, "success": 4, "error": 1}
}
```

**`/metrics`** returns Prometheus text format — wire it to Grafana or any Prometheus-compatible scraper.

**`token_cost_usd`** in each report shows the exact LLM spend for that run, calculated from:
- Input tokens × $0.00059 / 1K
- Output tokens × $0.00079 / 1K

Typical cost: **$0.001–$0.005 per report**.

---

## Free API Keys

| Service       | URL                        | Free Tier                     |
|---------------|----------------------------|-------------------------------|
| Groq (LLM)    | https://console.groq.com   | 14,400 requests/day, no card  |
| Tavily (search)| https://app.tavily.com    | 1,000 searches/month, no card |

Both are instant signup — no credit card required.

---

## Running Tests

```bash
DB_PATH=:memory: pytest tests/ -v
```

All 13 tests run with zero real API keys — every external call is mocked.

---

## License

MIT
