"""Entry point matching the WSO2 Agent Manager start-command convention.

Mirrors main.py exactly, for the orchestrator agent instead of the
concierge agent — see that file's docstring for why `tracing` must import
before the LangChain-using module.
"""

from __future__ import annotations

import os

import uvicorn

import tracing  # noqa: F401  must run before `import orchestrator_agent` so the LangChain instrumentor wraps BaseCallbackManager.__init__ before langchain_core loads
from orchestrator_agent import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
