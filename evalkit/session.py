"""Read & structure a Hermes session.

A session is fetched via ``hermes sessions export --session-id <id> -`` which
emits a single JSONL line containing the full trajectory plus metrics (tokens,
cost, timing, tool-call counts). We parse that into typed objects the graders
can consume. See EVAL_PLATFORM_PLAN.md §1.4 for the schema.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import resolve_hermes


@dataclass
class ToolCall:
    """One tool invocation from an assistant message."""

    name: str
    arguments: dict[str, Any]  # parsed from the JSON-string `function.arguments`
    id: str = ""
    raw_arguments: str = ""  # original string, kept for debugging / non-JSON args

    @classmethod
    def from_raw(cls, tc: dict[str, Any]) -> "ToolCall":
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments", "") or ""
        try:
            parsed = json.loads(raw_args) if raw_args else {}
            if not isinstance(parsed, dict):
                parsed = {"_value": parsed}
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        return cls(
            name=fn.get("name", ""),
            arguments=parsed,
            id=tc.get("id") or tc.get("call_id") or "",
            raw_arguments=raw_args,
        )


@dataclass
class Message:
    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: Optional[str] = None  # set on role == "tool"
    tool_call_id: Optional[str] = None
    timestamp: Optional[float] = None
    token_count: Optional[int] = None
    finish_reason: Optional[str] = None

    @classmethod
    def from_raw(cls, m: dict[str, Any]) -> "Message":
        raw_tcs = m.get("tool_calls") or []
        return cls(
            role=m.get("role", ""),
            content=m.get("content") or "",
            tool_calls=[ToolCall.from_raw(tc) for tc in raw_tcs],
            tool_name=m.get("tool_name"),
            tool_call_id=m.get("tool_call_id"),
            timestamp=m.get("timestamp"),
            token_count=m.get("token_count"),
            finish_reason=m.get("finish_reason"),
        )


@dataclass
class Session:
    """A parsed Hermes session: metadata + metrics + full trajectory."""

    id: str
    model: str = ""
    started_at: Optional[float] = None
    ended_at: Optional[float] = None  # usually None for oneshot runs
    end_reason: Optional[str] = None
    message_count: int = 0
    tool_call_count: int = 0
    api_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost_usd: Optional[float] = None
    cost_status: Optional[str] = None
    messages: list[Message] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    # ---- construction -------------------------------------------------

    @classmethod
    def from_export_dict(cls, d: dict[str, Any]) -> "Session":
        def _int(key: str) -> int:
            v = d.get(key)
            return int(v) if isinstance(v, (int, float)) else 0

        return cls(
            id=d.get("id", ""),
            model=d.get("model", "") or "",
            started_at=d.get("started_at"),
            ended_at=d.get("ended_at"),
            end_reason=d.get("end_reason"),
            message_count=_int("message_count"),
            tool_call_count=_int("tool_call_count"),
            api_call_count=_int("api_call_count"),
            input_tokens=_int("input_tokens"),
            output_tokens=_int("output_tokens"),
            reasoning_tokens=_int("reasoning_tokens"),
            cache_read_tokens=_int("cache_read_tokens"),
            cache_write_tokens=_int("cache_write_tokens"),
            estimated_cost_usd=d.get("estimated_cost_usd"),
            cost_status=d.get("cost_status"),
            messages=[Message.from_raw(m) for m in (d.get("messages") or [])],
            raw=d,
        )

    # ---- trajectory helpers -------------------------------------------

    def all_tool_calls(self) -> list[ToolCall]:
        """Flat list of every tool call across all assistant messages, in order."""
        return [tc for m in self.messages for tc in m.tool_calls]

    def tool_names(self) -> list[str]:
        return [tc.name for tc in self.all_tool_calls()]

    def final_answer(self) -> str:
        """Content of the last assistant message (the final response)."""
        for m in reversed(self.messages):
            if m.role == "assistant" and m.content:
                return m.content
        return ""

    def tool_result_for(self, call_id: str) -> Optional[Message]:
        """The role=='tool' message responding to a given tool_call id."""
        for m in self.messages:
            if m.role == "tool" and m.tool_call_id == call_id:
                return m
        return None

    def duration_seconds(self) -> Optional[float]:
        """Fallback wall-clock from message timestamps (ended_at is usually null).

        The runner's measured subprocess wall-clock is preferred; this is a
        best-effort fallback derived purely from the export.
        """
        ts = [m.timestamp for m in self.messages if isinstance(m.timestamp, (int, float))]
        if self.started_at and ts:
            return max(ts) - self.started_at
        return None


# ---- fetching ---------------------------------------------------------


def export_session(session_id: str, hermes_bin: Optional[str] = None) -> Session:
    """Run ``hermes sessions export`` for one id and parse it into a Session."""
    hermes = hermes_bin or resolve_hermes()
    proc = subprocess.run(
        [hermes, "sessions", "export", "--session-id", session_id, "-"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"`hermes sessions export` failed for {session_id!r} "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError(f"No session exported for id {session_id!r} (empty output)")
    return Session.from_export_dict(json.loads(lines[0]))
