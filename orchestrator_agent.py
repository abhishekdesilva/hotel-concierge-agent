"""Grand Meridian concierge orchestrator — FastAPI service exposing POST /chat.

Routes each guest message to whichever downstream agent fits —
concierge-reader-only for availability/informational requests,
concierge-full-access for anything that commits to a booking — and relays
that agent's reply back to the guest verbatim.

Implements the same WSO2 Agent Manager chat interface as the agents it
delegates to:
  Request:  {message: string, session_id: string, context: JSON}
  Response: {response: string}

Deliberately holds no MCP/tool access of its own: an LLM call decides which
agent to invoke, but the actual hotel-tools calls only ever happen inside
whichever downstream agent gets picked. That's what keeps the scope split
between those two agents meaningful — this agent's identity carries no role
assignment at the hotel-tools MCP proxy at all, so there is nothing for it
to escalate to even if it tried.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from openai import APIError, RateLimitError
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
MAX_SESSION_MESSAGES = 40
FRIENDLY_FALLBACK = (
    "I'm having trouble reaching our systems right now — could you try that again in a moment?"
)

# In-cluster chat endpoints of the two agents this orchestrator delegates
# to. Set at deploy time (env vars, not hardcoded) so this same image works
# against any pair of differently-scoped deployments.
AVAILABILITY_AGENT_URL = os.environ.get("AVAILABILITY_AGENT_URL", "")
BOOKING_AGENT_URL = os.environ.get("BOOKING_AGENT_URL", "")

SESSIONS: dict[str, list[BaseMessage]] = {}
SESSION_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)

_llm = None
_llm_lock = asyncio.Lock()

# Carries the in-flight request's session id + transcript-so-far so the two
# plain @tool functions below — which the LLM calls with just a `message`
# argument — can still build the right outbound payload to the delegate
# agent. Set right before invoking the LLM, cleared in a finally right after.
_delegate_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("delegate_ctx")


def _resolve_llm_config() -> dict[str, Any]:
    """Same governed/BYO toggle as the two downstream agents — see agent.py
    for the full rationale. Kept identical so this deploys the same way."""
    base_url = os.getenv("OPENAI_URL")
    if base_url:
        return {
            "base_url": base_url,
            "api_key": "unused",
            "default_headers": {
                "API-Key": os.getenv("OPENAI_API_KEY", ""),
                "Authorization": "",
            },
        }
    return {"api_key": os.getenv("OPENAI_API_KEY_DEFAULT")}


def _serialize_history(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """Flatten to the {role, content} shape the downstream agents' own
    context.history seeding reads — plain user/assistant turns only, same
    as what agent.py extracts on the receiving end."""
    out: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, HumanMessage) and isinstance(m.content, str):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage) and isinstance(m.content, str) and m.content:
            out.append({"role": "assistant", "content": m.content})
    return out


async def _delegate(agent_url: str, agent_label: str) -> str:
    """Shared HTTP call behind both delegate tools below. Sends the
    transcript so far as context.history — the same hand-off mechanism the
    chat widget used to drive directly — so the receiving agent can seed its
    own memory the first time it sees this session_id."""
    ctx = _delegate_ctx.get()
    if not agent_url:
        log.warning("no URL configured for %s agent", agent_label)
        return FRIENDLY_FALLBACK
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                agent_url,
                json={
                    "message": ctx["message"],
                    "session_id": ctx["session_id"],
                    "context": {"history": ctx["history"]},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response") or FRIENDLY_FALLBACK
    except Exception as e:
        log.warning("delegate to %s agent (%s) failed: %s", agent_label, agent_url, e)
        return FRIENDLY_FALLBACK


@tool
async def ask_availability_agent(message: str) -> str:
    """Delegate to the availability agent for room availability, pricing,
    the room service menu, or local recommendations — anything that is NOT
    a commitment to book a room."""
    return await _delegate(AVAILABILITY_AGENT_URL, "availability")


@tool
async def ask_booking_agent(message: str) -> str:
    """Delegate to the booking agent to actually reserve a room once the
    guest has confirmed a room type, check-in date, number of nights, and
    the name to book under — or to confirm a booking after being quoted a
    price and agreeing to proceed."""
    return await _delegate(BOOKING_AGENT_URL, "booking")


_DELEGATE_TOOLS = {t.name: t for t in (ask_availability_agent, ask_booking_agent)}

ORCHESTRATOR_PROMPT = """You are a routing dispatcher for The Grand Meridian's concierge \
service. You do not answer guest questions yourself — you have no information about rooms, \
pricing, or bookings. For every guest message, call exactly one tool:

- ask_availability_agent: room availability, pricing, the room service menu, local \
  recommendations, or anything else that does not commit to a reservation.
- ask_booking_agent: the guest is confirming or asking you to make a booking, including a \
  bare "yes" / "go ahead" that follows being quoted a price and asked if they would like to \
  book.

Call exactly one tool per guest message, then return its result to the guest EXACTLY as \
given — do not summarize, paraphrase, translate, or add anything before or after it. The \
tool's output is the final answer, verbatim."""


async def _get_llm():
    """Lazy + cached, mirroring agent.py's _get_agent(). bind_tools with
    tool_choice="required" means this LLM call can never skip routing —
    there is no path where it answers directly instead of delegating."""
    global _llm
    if _llm is not None:
        return _llm
    async with _llm_lock:
        if _llm is None:
            base = ChatOpenAI(model=OPENAI_MODEL, **_resolve_llm_config())
            _llm = base.bind_tools(
                [ask_availability_agent, ask_booking_agent], tool_choice="required"
            )
    return _llm


def _ready_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "model": OPENAI_MODEL,
        "governed": bool(os.environ.get("OPENAI_URL")),
        "availability_agent_configured": bool(AVAILABILITY_AGENT_URL),
        "booking_agent_configured": bool(BOOKING_AGENT_URL),
        "port": int(os.environ.get("PORT", "8000")),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("READY %s", json.dumps(_ready_payload()))
    yield


app = FastAPI(title="Grand Meridian Orchestrator", lifespan=lifespan)
# CORS is for local dev only — see agent.py's identical comment.
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
    # Not part of the standard Agent Manager /chat contract — additive, for
    # demo visibility only. Which delegate tool this turn was routed to, if
    # a routing decision actually happened (absent on early-return replies).
    routed_to: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return _ready_payload()


def _truncate(history: list[BaseMessage]) -> list[BaseMessage]:
    """Same bound as agent.py's SESSIONS store — no ToolMessage plumbing is
    kept here (see chat() below), so there's no orphan-message risk; this
    just caps prompt size."""
    if len(history) <= MAX_SESSION_MESSAGES:
        return history
    return history[len(history) - MAX_SESSION_MESSAGES :]


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    started = time.perf_counter()

    if not req.message.strip():
        return ChatResponse(response="How can I help you today?")

    sid = req.session_id or "_anonymous_"
    routed_to: str | None = None

    with SESSION_LOCKS[sid]:
        history = SESSIONS.get(sid, [])
        history_for_llm = history + [HumanMessage(content=req.message)]

        try:
            llm = await _get_llm()
            token = _delegate_ctx.set(
                {
                    "message": req.message,
                    "session_id": sid,
                    # Transcript BEFORE this turn — the current message is
                    # sent separately as `message` on the delegate call.
                    "history": _serialize_history(history),
                }
            )
            try:
                decision = await llm.ainvoke(history_for_llm)
                if not decision.tool_calls:
                    log.warning("session=%s orchestrator made no tool call", sid)
                    reply = FRIENDLY_FALLBACK
                else:
                    call = decision.tool_calls[0]
                    tool_fn = _DELEGATE_TOOLS.get(call["name"])
                    if tool_fn is None:
                        log.error("session=%s unknown tool call: %s", sid, call["name"])
                        reply = FRIENDLY_FALLBACK
                    else:
                        reply = await tool_fn.ainvoke(call["args"])
                        routed_to = call["name"]
                        log.info("session=%s routed to %s", sid, routed_to)
            finally:
                _delegate_ctx.reset(token)

            # Plain conversational turns only — no tool_call/ToolMessage
            # plumbing kept here. The next routing decision only needs to
            # see what the guest asked and what they were told, and that's
            # also exactly the shape _serialize_history hands to whichever
            # agent gets delegated to next.
            history = history_for_llm + [AIMessage(content=reply)]
        except RateLimitError:
            log.warning("session=%s openai rate limit", sid)
            reply = FRIENDLY_FALLBACK
            history = history_for_llm
        except APIError as e:
            log.warning("session=%s openai api error: %s", sid, e)
            reply = FRIENDLY_FALLBACK
            history = history_for_llm
        except Exception as e:
            log.exception("session=%s unhandled error in /chat: %s", sid, e)
            reply = FRIENDLY_FALLBACK
            history = history_for_llm

        SESSIONS[sid] = _truncate(history)

    log.info(
        "session=%s reply_chars=%d elapsed_ms=%d",
        sid,
        len(reply),
        int((time.perf_counter() - started) * 1000),
    )
    return ChatResponse(response=reply, routed_to=routed_to)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("orchestrator_agent:app", host="0.0.0.0", port=port, reload=False)
