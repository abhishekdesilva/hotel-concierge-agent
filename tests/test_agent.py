"""Deterministic tests for the LangGraph agent surface.

These cover the framework swap's new failure modes:
- @tool registration (wrong type hint -> wrong schema -> silent wrong calls)
- _truncate() porting from dict-shaped history to BaseMessage objects
- Lazy agent init so the module imports without OPENAI_API_KEY

No LLM calls. Fast. Run alongside test_tools.py before every rehearsal.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


class TestLangchainToolsRegistered:
    """Each @tool wrapper exposes the expected name and accepts the expected
    arg names. If type hints or names drift, the LLM ends up calling tools
    with wrong arguments and silently producing wrong answers."""

    def test_tool_count(self) -> None:
        from tools import LANGCHAIN_TOOLS

        assert len(LANGCHAIN_TOOLS) == 3

    def test_tool_names(self) -> None:
        from tools import LANGCHAIN_TOOLS

        names = {t.name for t in LANGCHAIN_TOOLS}
        assert names == {
            "check_room_availability",
            "get_room_service_menu",
            "get_local_recommendations",
        }

    def test_check_room_availability_args(self) -> None:
        from tools import LANGCHAIN_TOOLS

        t = next(t for t in LANGCHAIN_TOOLS if t.name == "check_room_availability")
        schema = t.args
        assert "room_type" in schema
        assert "check_in" in schema
        assert "nights" in schema

    def test_get_room_service_menu_args(self) -> None:
        from tools import LANGCHAIN_TOOLS

        t = next(t for t in LANGCHAIN_TOOLS if t.name == "get_room_service_menu")
        assert "vegetarian_only" in t.args

    def test_get_local_recommendations_args(self) -> None:
        from tools import LANGCHAIN_TOOLS

        t = next(t for t in LANGCHAIN_TOOLS if t.name == "get_local_recommendations")
        assert "category" in t.args

    def test_tool_descriptions_are_nonempty(self) -> None:
        from tools import LANGCHAIN_TOOLS

        for t in LANGCHAIN_TOOLS:
            assert t.description, f"{t.name} is missing a description"
            assert len(t.description) > 20, f"{t.name} description too thin"

    def test_tool_invokes_underlying_function(self) -> None:
        """A direct .invoke() should hit the real implementation and return
        the same shape as calling the function directly. Catches schema drift
        between the @tool wrapper and the underlying function signature."""
        from tools import LANGCHAIN_TOOLS

        t = next(t for t in LANGCHAIN_TOOLS if t.name == "check_room_availability")
        result = t.invoke({"room_type": "junior"})
        assert result["available"] is True
        assert result["name"] == "Junior Suite"


class TestTruncatePreservesAiToolMessagePair:
    """The history truncation invariant: never start a slice on a ToolMessage
    (would be orphaned from its preceding AIMessage tool_calls and produce an
    invalid LangChain prompt on the next turn)."""

    def test_no_truncation_when_under_cap(self) -> None:
        from agent import MAX_SESSION_MESSAGES, _truncate

        history = [HumanMessage(content=f"turn {i}") for i in range(5)]
        assert _truncate(history) is history
        assert len(_truncate(history)) == 5
        assert MAX_SESSION_MESSAGES == 40

    def test_truncates_to_cap(self) -> None:
        from agent import MAX_SESSION_MESSAGES, _truncate

        history = [HumanMessage(content=f"turn {i}") for i in range(MAX_SESSION_MESSAGES + 10)]
        result = _truncate(history)
        assert len(result) == MAX_SESSION_MESSAGES

    def test_skips_leading_tool_messages_after_cut(self) -> None:
        """Construct a history where a naive slice would land on a ToolMessage.
        Truncate must advance past it."""
        from agent import MAX_SESSION_MESSAGES, _truncate

        # Fill to MAX_SESSION_MESSAGES with HumanMessages, then add an
        # AI(tool_calls) + 2 ToolMessage block at the END. Adding 3 more
        # messages forces a cut of 3, which would otherwise land on the
        # first ToolMessage.
        history: list = [HumanMessage(content=f"h{i}") for i in range(MAX_SESSION_MESSAGES)]
        # Replace last 3 with the tool-call block:
        history[-3] = AIMessage(
            content="",
            tool_calls=[
                {"name": "check_room_availability", "args": {"room_type": "junior"}, "id": "c1"},
            ],
        )
        history[-2] = ToolMessage(content='{"ok": true}', tool_call_id="c1")
        history[-1] = ToolMessage(content='{"ok": true}', tool_call_id="c1")
        # Now prepend 3 more so cut = 3. cut(3) lands on a HumanMessage
        # in this construction, which is fine. Build a different scenario:
        # put the AI(tool_calls)+tool block at the START so cut would land
        # in the middle of it.
        ai_tool_block = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "check_room_availability", "args": {"room_type": "junior"}, "id": "c1"},
                ],
            ),
            ToolMessage(content='{"ok": true}', tool_call_id="c1"),
            ToolMessage(content='{"ok": true}', tool_call_id="c1"),
        ]
        rest = [HumanMessage(content=f"h{i}") for i in range(MAX_SESSION_MESSAGES)]
        history = ai_tool_block + rest
        # len = 43, cap = 40, naive cut = 3 -> first survivor is ToolMessage[2].
        # _truncate should advance past the ToolMessages and land on the
        # first HumanMessage of `rest`.
        result = _truncate(history)
        assert len(result) <= MAX_SESSION_MESSAGES
        assert not isinstance(result[0], ToolMessage), (
            "truncate left a ToolMessage at index 0 — would invalidate the next prompt"
        )
        assert isinstance(result[0], HumanMessage)


class TestAgentModuleImportsWithoutApiKey:
    """The lazy _get_agent() pattern must let the module import cleanly so CI
    and /health smoke tests work without any LLM credentials in the env.
    Deterministic: clears all three env vars the resolver consults so the
    test passes for the right reason regardless of operator shell state."""

    def _clear_llm_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY_DEFAULT", raising=False)

    def test_imports_with_api_key_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_llm_env(monkeypatch)
        # Force a fresh import so we exercise the import path, not a cached module.
        sys.modules.pop("agent", None)
        try:
            agent_module = importlib.import_module("agent")
        finally:
            # Restore for any subsequent tests that may need it.
            sys.modules.pop("agent", None)
        assert hasattr(agent_module, "app")
        assert hasattr(agent_module, "_get_agent")
        # Confirm the lazy guard hasn't fired yet.
        assert agent_module._agent is None

    def test_health_works_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_llm_env(monkeypatch)
        sys.modules.pop("agent", None)
        try:
            agent_module = importlib.import_module("agent")
            result = agent_module.health()
        finally:
            sys.modules.pop("agent", None)
        assert result["ok"] is True
        assert "model" in result
        assert result["governed"] is False


class TestResolveLlmConfig:
    """OPENAI_URL presence is the strict mode gate. Governed mode sends the
    AM-minted key on a custom `API-Key` header and blanks `Authorization` to
    suppress the SDK's default Bearer; BYO mode uses OPENAI_API_KEY_DEFAULT
    against OpenAI directly. The two slots have distinct purposes — no
    cross-mode fallback."""

    def _clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY_DEFAULT", raising=False)

    def test_governed_mode_uses_api_key_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import _resolve_llm_config

        self._clear(monkeypatch)
        monkeypatch.setenv("OPENAI_URL", "https://gw.example/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "am-key")
        monkeypatch.setenv("OPENAI_API_KEY_DEFAULT", "byo-key")
        cfg = _resolve_llm_config()
        assert cfg["base_url"] == "https://gw.example/v1"
        # Non-empty sentinel — openai>=1.50 rejects empty string. The
        # Authorization header is blanked in default_headers so the sentinel
        # never reaches the wire.
        assert cfg["api_key"] == "unused"
        assert cfg["default_headers"] == {"API-Key": "am-key", "Authorization": ""}

    def test_byo_mode_uses_default_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import _resolve_llm_config

        self._clear(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY_DEFAULT", "byo-key")
        cfg = _resolve_llm_config()
        assert cfg == {"api_key": "byo-key"}
        assert "default_headers" not in cfg
        assert "base_url" not in cfg

    def test_misconfig_url_set_am_key_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """URL set but AM key missing: send empty API-Key header. Gateway
        will 401 — failure is loud, no silent fallback to the BYO key."""
        from agent import _resolve_llm_config

        self._clear(monkeypatch)
        monkeypatch.setenv("OPENAI_URL", "https://gw.example/v1")
        monkeypatch.setenv("OPENAI_API_KEY_DEFAULT", "byo-key")
        cfg = _resolve_llm_config()
        assert cfg["base_url"] == "https://gw.example/v1"
        assert cfg["default_headers"]["API-Key"] == ""

    def test_nothing_set_returns_byo_with_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import _resolve_llm_config

        self._clear(monkeypatch)
        cfg = _resolve_llm_config()
        assert cfg == {"api_key": None}


class TestReadyPayload:
    """The /health response and the startup READY log share one source of
    truth — _ready_payload(). This is the signal callers grep for in
    platform logs to confirm the agent is listening before invoking
    (Agent Manager doesn't expose readiness probes — see CLAUDE.md)."""

    def test_governed_true_when_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import _ready_payload

        monkeypatch.setenv("OPENAI_URL", "https://gw.example/v1")
        result = _ready_payload()
        assert result["governed"] is True
        assert result["ok"] is True
        assert "model" in result
        assert "port" in result

    def test_governed_false_when_url_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import _ready_payload

        monkeypatch.delenv("OPENAI_URL", raising=False)
        result = _ready_payload()
        assert result["governed"] is False

    def test_health_endpoint_returns_same_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/health and the startup log must agree — same shape, same fields."""
        from agent import _ready_payload, health

        monkeypatch.setenv("OPENAI_URL", "https://gw.example/v1")
        assert health() == _ready_payload()


class TestStartupLifespanLogsReady:
    """Lifespan startup must emit a READY <json> line. This is the only
    in-band signal that the agent has reached the listening state, since
    Agent Manager's Workload CRD does not expose a readinessProbe."""

    def test_lifespan_emits_ready_with_governed_flag(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import asyncio

        from agent import app, lifespan

        monkeypatch.setenv("OPENAI_URL", "https://gw.example/v1")

        async def _drive() -> None:
            async with lifespan(app):
                pass

        with caplog.at_level("INFO", logger="concierge"):
            asyncio.run(_drive())

        ready_lines = [r for r in caplog.records if r.message.startswith("READY ")]
        assert ready_lines, "lifespan did not emit a READY log line"
        # The JSON payload must include the governed flag — that's the
        # whole point of this signal during the AM-restart demo beat.
        assert '"governed": true' in ready_lines[-1].message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestAgentIDAuthRefreshesToken:
    """The MCP tools are built once per pod, so the bearer token must be
    refreshed by the auth object itself. Without this, the token minted at
    startup expires and every tool call 401s until the pod restarts — which
    reads to guests as a permission denial on every request."""

    @staticmethod
    def _client(auth, statuses: list[int]):
        """AsyncClient whose transport answers with `statuses` in order and
        records the bearer token each request carried."""
        import httpx

        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["Authorization"])
            return httpx.Response(statuses[len(seen) - 1])

        return httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(handler)), seen

    @staticmethod
    def _minter(monkeypatch: pytest.MonkeyPatch, tokens: list[tuple[str, int]]) -> list[int]:
        import agent as agent_module

        calls: list[int] = []

        def fake_mint(url: str):
            calls.append(1)
            return tokens[len(calls) - 1]

        monkeypatch.setattr(agent_module, "_mint_mcp_token", fake_mint)
        return calls

    @pytest.mark.anyio
    async def test_reuses_token_until_near_expiry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent as agent_module

        calls = self._minter(monkeypatch, [("t2", 3600)])
        auth = agent_module._AgentIDAuth("https://gw/mcp", "t1", 3600)
        client, seen = self._client(auth, [200, 200])
        await client.post("https://gw/mcp")
        await client.post("https://gw/mcp")
        assert seen == ["Bearer t1", "Bearer t1"]
        assert calls == []

    @pytest.mark.anyio
    async def test_remints_when_token_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent as agent_module

        calls = self._minter(monkeypatch, [("t2", 3600)])
        # expires_in below the refresh skew -> already due for refresh
        auth = agent_module._AgentIDAuth("https://gw/mcp", "t1", 1)
        client, seen = self._client(auth, [200])
        await client.post("https://gw/mcp")
        assert seen == ["Bearer t2"]
        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_401_forces_one_remint_and_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent as agent_module

        self._minter(monkeypatch, [("t2", 3600)])
        auth = agent_module._AgentIDAuth("https://gw/mcp", "t1", 3600)
        client, seen = self._client(auth, [401, 200])
        resp = await client.post("https://gw/mcp")
        assert resp.status_code == 200
        assert seen == ["Bearer t1", "Bearer t2"]

    @pytest.mark.anyio
    async def test_persistent_401_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A genuine permission denial must still surface as 401 (not loop),
        so chat() maps it to ACCESS_DENIED_FALLBACK."""
        import agent as agent_module

        self._minter(monkeypatch, [("t2", 3600)])
        auth = agent_module._AgentIDAuth("https://gw/mcp", "t1", 3600)
        client, seen = self._client(auth, [401, 401])
        resp = await client.post("https://gw/mcp")
        assert resp.status_code == 401
        assert len(seen) == 2

    @pytest.mark.anyio
    async def test_failed_remint_keeps_old_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent as agent_module

        monkeypatch.setattr(agent_module, "_mint_mcp_token", lambda url: None)
        auth = agent_module._AgentIDAuth("https://gw/mcp", "t1", 1)
        client, seen = self._client(auth, [200])
        await client.post("https://gw/mcp")
        assert seen == ["Bearer t1"]
