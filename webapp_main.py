"""Static-file host for the chat widget — deployed as its own Agent Manager
component, co-located on the same gateway domain as the agents it talks to.

Same-origin means the browser never sends a cross-origin request to the
orchestrator at all, sidestepping the gateway's CORS policy (which only
allowlists the console's own origin) entirely — no infra access needed.

Not an agent in any real sense; just a plain static-file server that
happens to deploy through the same buildpack pipeline as the other three.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Grand Meridian Chat Widget")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


# html=True serves index.html for "/" and lets client-relative asset paths
# (widget.js, etc.) resolve normally regardless of whatever path prefix the
# gateway routes this component under.
app.mount("/", StaticFiles(directory="web", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
