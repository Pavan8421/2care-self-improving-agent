"""HTTP chat endpoints wired to the LangGraph scheduling agent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.agent.graph import SessionManager

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    debug: bool = False


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    state: dict[str, Any] | None = None
    tool_trace: list[dict[str, Any]] | None = None


class ResetRequest(BaseModel):
    session_id: str | None = None


class ResetResponse(BaseModel):
    session_id: str


def get_manager(request: Request) -> SessionManager:
    return request.app.state.sessions


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """One user message → assistant reply via the shared LangGraph agent."""
    manager = get_manager(request)
    try:
        turn = manager.chat(body.message.strip(), session_id=body.session_id)
    except Exception as exc:  # noqa: BLE001 — never crash the HTTP process on agent errors
        # Tool errors are handled inside the graph as normal assistant recovery.
        # This path covers unexpected model/runtime failures.
        sid = body.session_id or "unknown"
        return ChatResponse(
            session_id=sid,
            reply=(
                "Sorry — something went wrong on my side. "
                "Please try again, or start a new session."
            ),
            state={"error": str(exc)} if body.debug else None,
        )

    return ChatResponse(
        session_id=turn.session_id,
        reply=turn.reply or "(No reply produced — try again.)",
        state=turn.state if body.debug else None,
        tool_trace=turn.tool_trace if body.debug else None,
    )


@router.post("/session/reset", response_model=ResetResponse)
def reset_session(body: ResetRequest, request: Request) -> ResetResponse:
    """Start a fresh conversation (new session id; clinic store kept)."""
    manager = get_manager(request)
    session = manager.reset_session(body.session_id)
    return ResetResponse(session_id=session.session_id)
