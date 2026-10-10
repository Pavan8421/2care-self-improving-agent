"""LangGraph orchestration: agent ↔ tools loop with session state."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.state import AgentState, empty_agent_state, format_state_summary
from app.agent.tools import AgentToolContext, build_clinic_tools
from app.clinic.store import ClinicStore, default_store
from app.config import get_settings

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Lightweight cues for safety_flags (prompt still drives refuse / escalate).
_EMERGENCY_RE = re.compile(
    r"\b("
    r"chest pain|can'?t breathe|cannot breathe|severe bleeding|"
    r"suicid|kill myself|overdose|stroke|heart attack|unconscious"
    r")\b",
    re.I,
)
_MEDICAL_RE = re.compile(
    r"\b("
    r"dosage|dose|diagnos|is this normal|what(?:'s| is) wrong|"
    r"should i take|prescribe|medication advice|medical advice"
    r")\b",
    re.I,
)


def combine_prompt_parts(system: str, improvements: str = "") -> str:
    """Join system + improvements the same way the live agent does."""
    parts = [system.strip()]
    improvements = (improvements or "").strip()
    if improvements:
        parts.append("---\n## Applied improvements\n\n" + improvements)
    return "\n\n".join(parts)


def load_prompt_text(*, prompt_override: str | None = None) -> str:
    """Load base system.md plus improvements.md (empty at v0).

    If ``prompt_override`` is set (eval shadow trials), use it instead of files.
    """
    if prompt_override is not None and prompt_override.strip():
        return prompt_override.strip()
    system = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8").strip()
    improvements = (PROMPTS_DIR / "improvements.md").read_text(encoding="utf-8").strip()
    return combine_prompt_parts(system, improvements)


def _ctx_snapshot(ctx: AgentToolContext) -> dict[str, Any]:
    return {
        "verified_patient_id": ctx.verified_patient_id,
        "relationship": ctx.relationship,
        "pending_slot_id": ctx.pending_slot_id,
        "soft_confirmed": ctx.soft_confirmed,
        "soft_confirmed_slot_id": ctx.soft_confirmed_slot_id,
        "booking_id": ctx.booking_id,
        "escalated": ctx.escalated,
        "escalate_reason": ctx.escalate_reason,
        "safety_flags": list(ctx.safety_flags),
    }


def _merge_safety_flags(existing: list[str], *extra: str) -> list[str]:
    out = list(existing)
    for flag in extra:
        if flag and flag not in out:
            out.append(flag)
    return out


def _detect_safety_from_text(text: str) -> list[str]:
    flags: list[str] = []
    if _EMERGENCY_RE.search(text or ""):
        flags.append("emergency")
    if _MEDICAL_RE.search(text or ""):
        flags.append("medical_advice")
    return flags


def _parse_tool_payload(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _candidate_slots_from_lookup(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    if not payload.get("ok"):
        return None
    slots = payload.get("slots")
    if isinstance(slots, list):
        return slots
    return None


def build_scheduling_graph(
    ctx: AgentToolContext,
    tools: list[BaseTool] | None = None,
    *,
    checkpointer: MemorySaver | None = None,
    prompt_override: str | None = None,
):
    """Compile agent ↔ tools graph closed over one session tool context."""
    tool_list = tools if tools is not None else build_clinic_tools(ctx)
    settings = get_settings()
    model = ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openai_api_key or None,
        temperature=0.2,
    ).bind_tools(tool_list)
    tool_node = ToolNode(tool_list)
    base_prompt = load_prompt_text(prompt_override=prompt_override)

    def agent_node(state: AgentState) -> dict[str, Any]:
        summary = format_state_summary(state)
        system = SystemMessage(content=f"{base_prompt}\n\n{summary}")
        messages: list[BaseMessage] = [system, *state.get("messages", [])]
        response = model.invoke(messages)

        updates: dict[str, Any] = {"messages": [response]}

        # Cue safety_flags from the latest user turn (eval / debug).
        last_human = ""
        for msg in reversed(state.get("messages") or []):
            if isinstance(msg, HumanMessage):
                last_human = msg.content if isinstance(msg.content, str) else str(msg.content)
                break
        detected = _detect_safety_from_text(last_human)
        if detected:
            updates["safety_flags"] = _merge_safety_flags(
                list(state.get("safety_flags") or []) + list(ctx.safety_flags),
                *detected,
            )
            for flag in detected:
                if flag not in ctx.safety_flags:
                    ctx.safety_flags.append(flag)

        return updates

    def tools_with_sync(state: AgentState) -> dict[str, Any]:
        """Run tools, append tool_trace, mirror ctx → graph state."""
        raw = tool_node.invoke(state)
        messages_out = raw.get("messages") or []

        traces: list[dict[str, Any]] = []
        candidate_slots = state.get("candidate_slots") or []
        intent = state.get("intent")
        relationship = state.get("relationship") or ctx.relationship

        # Pair AI tool_calls with ToolMessage results for the trace.
        last_ai = None
        for msg in reversed(state.get("messages") or []):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                last_ai = msg
                break
        call_by_id = {
            tc["id"]: tc
            for tc in (last_ai.tool_calls if last_ai else [])
            if isinstance(tc, dict) and tc.get("id")
        }

        for msg in messages_out:
            if not isinstance(msg, ToolMessage):
                continue
            name = msg.name or call_by_id.get(msg.tool_call_id, {}).get("name") or "unknown"
            args = call_by_id.get(msg.tool_call_id, {}).get("args") or {}
            payload = _parse_tool_payload(msg.content if isinstance(msg.content, str) else "")
            traces.append(
                {
                    "tool": name,
                    "args": args,
                    "result": payload if payload is not None else {"raw": msg.content},
                }
            )
            if name == "lookup_slots" and payload:
                found = _candidate_slots_from_lookup(payload)
                if found is not None:
                    candidate_slots = found
            if name == "get_patient" and payload and payload.get("ok"):
                intent = intent or "book"
            if name == "book" and payload and payload.get("ok"):
                intent = "book"
                if isinstance(args, dict) and args.get("relationship"):
                    relationship = str(args["relationship"]).strip()
            if name in ("cancel", "reschedule") and payload and payload.get("ok"):
                intent = name

        snap = _ctx_snapshot(ctx)
        if relationship and not snap.get("relationship"):
            snap["relationship"] = relationship
            ctx.relationship = relationship

        return {
            "messages": messages_out,
            "tool_trace": traces,
            "candidate_slots": candidate_slots,
            "intent": intent,
            "relationship": snap["relationship"],
            "verified_patient_id": snap["verified_patient_id"],
            "pending_slot_id": snap["pending_slot_id"],
            "soft_confirmed": snap["soft_confirmed"],
            "soft_confirmed_slot_id": snap["soft_confirmed_slot_id"],
            "booking_id": snap["booking_id"],
            "escalated": snap["escalated"],
            "escalate_reason": snap["escalate_reason"],
            "safety_flags": snap["safety_flags"],
        }

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_with_sync)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer or MemorySaver())


@dataclass
class TurnResult:
    """One user→assistant turn result for CLI / API / eval."""

    session_id: str
    reply: str
    state: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)


class SchedulingSession:
    """Multi-turn session: one tool context + compiled graph + thread id."""

    def __init__(
        self,
        store: ClinicStore | None = None,
        session_id: str | None = None,
        *,
        use_default_store: bool = False,
        prompt_override: str | None = None,
    ) -> None:
        if use_default_store:
            clinic = store or default_store
        else:
            clinic = store or ClinicStore()
        self.session_id = session_id or str(uuid.uuid4())
        self.ctx = AgentToolContext(store=clinic)
        self.tools = build_clinic_tools(self.ctx)
        self._checkpointer = MemorySaver()
        self.prompt_override = prompt_override
        self.graph = build_scheduling_graph(
            self.ctx,
            self.tools,
            checkpointer=self._checkpointer,
            prompt_override=prompt_override,
        )

    @property
    def config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": self.session_id}}

    def reset(self) -> None:
        """Clear conversation gates; keep store. New thread id for checkpointer."""
        self.ctx.reset_session_flags()
        self.session_id = str(uuid.uuid4())

    def invoke(self, user_message: str) -> TurnResult:
        """Run one user message through the agent ↔ tools loop."""
        prior = self.graph.get_state(self.config)
        first_turn = prior.values is None or not prior.values.get("messages")
        payload: dict[str, Any] = {"messages": [HumanMessage(content=user_message)]}
        if first_turn:
            payload.update(empty_agent_state())

        result = self.graph.invoke(payload, config=self.config)
        reply = _last_ai_text(result.get("messages") or [])
        # Mirror latest ctx in case agent ended without tools this turn.
        snap = _ctx_snapshot(self.ctx)
        state_out = {
            **{k: result.get(k) for k in empty_agent_state()},
            **snap,
            "candidate_slots": result.get("candidate_slots") or [],
            "intent": result.get("intent"),
            "constraints": result.get("constraints"),
            "tool_trace": result.get("tool_trace") or [],
        }
        return TurnResult(
            session_id=self.session_id,
            reply=reply,
            state=state_out,
            tool_trace=list(state_out.get("tool_trace") or []),
        )


class SessionManager:
    """session_id → SchedulingSession (shared store for UI; eval uses own sessions)."""

    def __init__(self, store: ClinicStore | None = None) -> None:
        self.store = store or default_store
        self._sessions: dict[str, SchedulingSession] = {}

    def get_or_create(self, session_id: str | None = None) -> SchedulingSession:
        sid = session_id or str(uuid.uuid4())
        if sid not in self._sessions:
            self._sessions[sid] = SchedulingSession(
                store=self.store,
                session_id=sid,
                use_default_store=True,
            )
        return self._sessions[sid]

    def reset_session(self, session_id: str | None = None) -> SchedulingSession:
        """Drop an old session (if any) and return a brand-new session id.

        Clinic store is shared for the UI so bookings remain visible across
        conversations; only dialogue / tool gates are cleared.
        """
        if session_id:
            self._sessions.pop(session_id, None)
        return self.get_or_create(None)

    def chat(self, message: str, session_id: str | None = None) -> TurnResult:
        session = self.get_or_create(session_id)
        return session.invoke(message)


def _last_ai_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            if isinstance(content, str):
                return content
            return str(content)
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content
    return ""
