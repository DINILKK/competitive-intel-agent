import os
import sys

import uvicorn
from dotenv import load_dotenv

# Load .env before anything else so all env vars are available
load_dotenv(override=False)

# Validate required secrets at startup — fail fast with a clear message
_REQUIRED = ["GROQ_API_KEY", "TAVILY_API_KEY"]
_missing = [k for k in _REQUIRED if not os.getenv(k)]
if _missing:
    print(
        f"[ERROR] Missing required environment variables: {', '.join(_missing)}\n"
        "  1. Copy .env.example to .env\n"
        "  2. Fill in your API keys\n"
        "  Get Groq key:   https://console.groq.com\n"
        "  Get Tavily key: https://app.tavily.com\n",
        file=sys.stderr,
    )
    sys.exit(1)

if __name__ == "__main__":
    uvicorn.run("app.api.server:app", host="0.0.0.0", port=8080, reload=False)
