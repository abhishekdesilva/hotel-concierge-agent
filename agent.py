"""Hotel concierge agent — FastAPI service exposing POST /chat.

Implements the WSO2 Agent Manager standard chat interface:
  Request:  {message: string, session_id: string, context: JSON}
  Response: {response: string}

Conversation state is tracked server-side, keyed by session_id. The client
sends one user message per turn. Tool-calling runs through LangGraph's
prebuilt create_react_agent so each LLM call and tool call is a discrete
OTEL GenAI semconv span in Agent Manager's trace panel.
Defensive at every layer: rate limits, recursion-limit exhaustion, and
unhandled exceptions return a friendly fallback rather than 500-ing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import httpx
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from openai import APIError, RateLimitError
from pydantic import BaseModel

from system_prompt import SYSTEM_PROMPT
from tools import get_local_recommendations, get_room_service_menu

# Local (non-MCP) tools — always available regardless of MCP proxy config.
_LOCAL_TOOLS = [tool(get_room_service_menu), tool(get_local_recommendations)]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("concierge")

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
# Cap per-session message history to bound prompt size. A single turn can add
# 2-4 entries (user, optional AI tool_calls, tool results, final reply),
# so 40 covers ~10 turns comfortably.
MAX_SESSION_MESSAGES = 40
FRIENDLY_FALLBACK = (
    "I'm having trouble reaching our systems right now — could you try that again in a moment?"
)
# Surfaced when a tool call comes back 401/403 — i.e. this agent's identity
# isn't scoped for the action, not a transient outage. Distinct from
# FRIENDLY_FALLBACK so a permission gap doesn't read as "try again later".
ACCESS_DENIED_FALLBACK = (
    "I'm unable to facilitate this request. Please get in touch with the front desk for help."
)

# In-memory session store. Single-process scope. Multi-replica deploys would
# need Redis or whatever Agent Manager exposes for shared state.
SESSIONS: dict[str, list[BaseMessage]] = {}
SESSION_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)

_agent = None
_agent_lock = asyncio.Lock()

# Env var name for the hotel-tools MCP proxy's URL, injected by Agent Manager
# once the Tool Configuration is attached (see docs/guides/configure-agent-mcp-proxies).
# Fixed AMP_AGENTID_* vars are injected alongside it automatically.
HOTEL_TOOLS_MCP_URL_VAR = "HOTEL_TOOLS_URL"


def _mint_mcp_token(mcp_server_url: str) -> str | None:
    """Client-credentials grant against this agent's AgentID identity, scoped
    to mcp_server_url via RFC 8707's `resource` param. The token's scopes are
    filtered server-side to whatever roles are assigned to this agent's
    identity — a different agent hitting the same proxy can get a token with
    fewer scopes, which is the whole point of the demo.

    Returns None (not raises) on any failure so a missing/misconfigured proxy
    degrades to "no MCP tools available" rather than crashing agent startup.
    """
    client_id = os.environ.get("AMP_AGENTID_CLIENT_ID")
    client_secret = os.environ.get("AMP_AGENTID_CLIENT_SECRET")
    token_endpoint = os.environ.get("AMP_AGENTID_TOKEN_ENDPOINT")
    scopes = os.environ.get("AMP_AGENTID_SCOPES", "")
    if not (client_id and client_secret and token_endpoint):
        log.warning("AgentID env vars not set — skipping MCP tool discovery")
        return None
    try:
        resp = requests.post(
            token_endpoint,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": scopes, "resource": mcp_server_url},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except Exception as e:
        log.warning("failed to mint AgentID token for MCP proxy: %s", e)
        return None


async def _get_mcp_tools() -> list[Any]:
    """Fetch check_room_availability + book_room from the hotel-tools MCP
    proxy, authenticated as this agent's identity. Returns [] (never raises)
    if the proxy isn't configured for this environment/agent — the agent
    still runs, just without room lookup/booking."""
    mcp_url = os.environ.get(HOTEL_TOOLS_MCP_URL_VAR, "").strip()
    if not mcp_url:
        log.warning("%s not set — MCP tools unavailable", HOTEL_TOOLS_MCP_URL_VAR)
        return []
    token = _mint_mcp_token(mcp_url)
    if not token:
        return []
    try:
        client = MultiServerMCPClient(
            {
                "hotel_tools": {
                    "url": mcp_url,
                    "transport": "streamable_http",
                    "headers": {"Authorization": f"Bearer {token}"},
                }
            }
        )
        return await client.get_tools()
    except Exception as e:
        log.warning("failed to fetch MCP tools from %s: %s", mcp_url, e)
        return []


def _resolve_llm_config() -> dict[str, Any]:
    """OPENAI_URL presence is the mode gate. In governed mode, the AM gateway
    expects the API key on a custom `API-Key` header (not `Authorization: Bearer`),
    so we suppress the SDK's default Authorization header and set API-Key
    explicitly — this matches Agent Manager's documented sample. In BYO mode,
    we use OPENAI_API_KEY_DEFAULT against OpenAI directly."""
    base_url = os.getenv("OPENAI_URL")
    if base_url:
        # openai>=1.50 rejects an empty api_key as "missing credentials" before
        # default_headers can override Authorization. Pass a non-empty sentinel
        # so the constructor accepts it; default_headers blanks Authorization
        # so the sentinel never reaches the wire — only API-Key does.
        return {
            "base_url": base_url,
            "api_key": "unused",
            "default_headers": {
                "API-Key": os.getenv("OPENAI_API_KEY", ""),
                "Authorization": "",
            },
        }
    return {"api_key": os.getenv("OPENAI_API_KEY_DEFAULT")}


def _debug_http_client():
    """When DUMP_LLM_PAYLOAD is truthy, return an httpx.Client that logs the raw
    response the gateway sends for /chat/completions *before* the OpenAI SDK
    parses it — this is where the `Unterminated string` JSONDecodeError fires.

    Reading the body inside a response event hook is safe: httpx caches the
    bytes, so the SDK still parses the same buffer (no stream consumption).
    We log the header-declared length vs the actual byte count, which pinpoints
    whether the gateway truncated the body or sent malformed JSON. Off by
    default so normal runs don't dump response content to the logs."""
    if not os.environ.get("DUMP_LLM_PAYLOAD"):
        return None

    import httpx

    def _log_response(response: httpx.Response) -> None:
        if "/chat/completions" not in str(response.url):
            return
        try:
            response.read()  # cached; SDK re-reads from the same buffer
            raw = response.content
        except Exception as e:
            # A truncated wire body surfaces here as RemoteProtocolError whose
            # message reports received-vs-expected bytes — itself diagnostic.
            log.warning("payload-dump read failed url=%s err=%s", response.url, e)
            return
        log.warning(
            "payload-dump status=%s content-type=%s header_len=%s actual_len=%d body=%r",
            response.status_code,
            response.headers.get("content-type"),
            response.headers.get("content-length"),
            len(raw),
            raw[:4000],
        )

    return httpx.Client(event_hooks={"response": [_log_response]}, timeout=60.0)


async def _get_agent():
    """Lazy + cached so the module imports cleanly with no keys set (CI,
    linters, /health smoke tests), and so MCP tool discovery (a network call)
    happens once rather than per-request. ChatOpenAI reads credentials on
    first instantiation, not at import time."""
    global _agent
    if _agent is not None:
        return _agent
    async with _agent_lock:
        if _agent is None:  # re-check: another request may have won the race
            cfg = _resolve_llm_config()
            if (client := _debug_http_client()) is not None:
                cfg["http_client"] = client
            llm = ChatOpenAI(model=OPENAI_MODEL, **cfg)
            mcp_tools = await _get_mcp_tools()
            all_tools = _LOCAL_TOOLS + mcp_tools
            log.info("agent tools loaded: %s", [t.name for t in all_tools])
            _agent = create_react_agent(llm, tools=all_tools, prompt=SYSTEM_PROMPT)
    return _agent


def _ready_payload() -> dict[str, Any]:
    """Single source of truth for /health and the startup log line. The
    `governed` flag makes the live LLM mode visible without reading the
    trace — useful for /health and as a startup signal in platform logs."""
    return {
        "ok": True,
        "model": OPENAI_MODEL,
        "governed": bool(os.environ.get("OPENAI_URL")),
        "port": int(os.environ.get("PORT", "8000")),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Emit a recognizable startup line so callers can grep platform logs to
    confirm the agent is listening before invoking. Agent Manager's Workload
    schema does not expose readiness probes (verified against ComponentType
    `agent-api` and the available Traits), so this log line is the only
    in-band readiness signal during the cold-start window."""
    log.info("READY %s", json.dumps(_ready_payload()))
    yield


app = FastAPI(title="Grand Meridian Concierge", lifespan=lifespan)
# CORS is for local dev only (widget on :5500 → agent on :8000). In Agent
# Manager deploys, the Envoy gateway in front handles CORS — this middleware
# is redundant on that path but harmless. Permissive defaults match the
# gateway's posture and the demo's no-auth scope.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


class ChatRequest(BaseModel):
    message: str
    session_id: str
    context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    response: str


@app.get("/health")
def health() -> dict[str, Any]:
    return _ready_payload()


def _find_http_status_error(exc: BaseException) -> httpx.HTTPStatusError | None:
    """A denied MCP tool call surfaces as httpx.HTTPStatusError, but anyio's
    task groups (inside the streamable-HTTP client) wrap it in a
    BaseExceptionGroup before it reaches here — search recursively."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = _find_http_status_error(sub)
            if found is not None:
                return found
    return None


def _truncate(history: list[BaseMessage]) -> list[BaseMessage]:
    """Keep the most recent messages, but never start the slice on a
    ToolMessage (would be orphaned without its preceding AIMessage tool_calls
    and produce an invalid LangChain prompt)."""
    if len(history) <= MAX_SESSION_MESSAGES:
        return history
    cut = len(history) - MAX_SESSION_MESSAGES
    while cut < len(history) and isinstance(history[cut], ToolMessage):
        cut += 1
    return history[cut:]


def _final_text(messages: list[BaseMessage]) -> str:
    """Pull the last AIMessage content from the agent's returned message list."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                return content.strip()
            # content can be a list of content blocks for some providers; flatten.
            if isinstance(content, list):
                parts = [
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                ]
                return "".join(parts).strip()
    return ""


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    started = time.perf_counter()

    if not req.message.strip():
        return ChatResponse(response="How can I help you today?")
    if not req.session_id:
        log.warning("empty session_id; conversation continuity disabled for this turn")

    sid = req.session_id or "_anonymous_"

    with SESSION_LOCKS[sid]:
        history = SESSIONS.get(sid, [])

        # A multi-agent frontend (e.g. one agent for availability, another for
        # booking) hands a session off mid-conversation by resending the
        # transcript so far as context.history. Seed from it only the first
        # time *this* backend sees the session — its own SESSIONS store is
        # authoritative once that happens.
        if not history and req.context:
            seed = req.context.get("history")
            if isinstance(seed, list):
                for turn in seed:
                    if not isinstance(turn, dict):
                        continue
                    content = turn.get("content")
                    if not content:
                        continue
                    if turn.get("role") == "user":
                        history.append(HumanMessage(content=content))
                    elif turn.get("role") == "assistant":
                        history.append(AIMessage(content=content))
                if history:
                    log.info("session=%s seeded %d messages from context.history", sid, len(history))

        history = history + [HumanMessage(content=req.message)]

        if req.context:
            log.info("session=%s context keys=%s", sid, list(req.context.keys()))

        try:
            agent = await _get_agent()
            result = await agent.ainvoke(
                {"messages": history},
                config={
                    "configurable": {"thread_id": sid},
                    "metadata": {"session_id": sid},
                },
            )
            history = result["messages"]
            reply = _final_text(history) or FRIENDLY_FALLBACK
        except GraphRecursionError:
            log.warning("session=%s langgraph recursion limit exceeded", sid)
            reply = "I'm still working that out — could you give me a moment and ask again?"
        except RateLimitError:
            log.warning("session=%s openai rate limit", sid)
            reply = FRIENDLY_FALLBACK
        except APIError as e:
            log.warning("session=%s openai api error: %s", sid, e)
            reply = FRIENDLY_FALLBACK
        except Exception as e:
            denied = _find_http_status_error(e)
            if denied is not None and denied.response.status_code in (401, 403):
                log.warning("session=%s tool call denied by gateway: %s", sid, denied)
                reply = ACCESS_DENIED_FALLBACK
            else:
                log.exception("session=%s unhandled error in /chat: %s", sid, e)
                reply = FRIENDLY_FALLBACK

        SESSIONS[sid] = _truncate(history)

    log.info(
        "session=%s reply_chars=%d elapsed_ms=%d",
        sid,
        len(reply),
        int((time.perf_counter() - started) * 1000),
    )
    return ChatResponse(response=reply)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("agent:app", host="0.0.0.0", port=port, reload=False)
