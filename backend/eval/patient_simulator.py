"""LLM-as-patient simulator driven by scenario goals and constraints."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings

DONE_TOKEN = "DONE"


def _build_system_prompt(patient: dict[str, Any], scenario_id: str) -> str:
    constraints = patient.get("constraints") or []
    constraint_block = "\n".join(f"- {c}" for c in constraints) or "- (none)"
    clarify = ""
    if patient.get("clarify_gate"):
        detail = patient.get("after_clarify_detail") or "A weekday afternoon is fine."
        clarify = (
            "\n## Timing reveal rule\n"
            "Your opening preference is vague. Only after the agent asks a clarifying "
            "timing question (day, morning/afternoon, etc.), reveal this preference: "
            f'"{detail}"\n'
        )

    return f"""You are role-playing a clinic PATIENT in an automated evaluation.
Scenario id: {scenario_id}

## Your identity (use when asked)
- Name: {patient.get("name")}
- DOB: {patient.get("dob")} (YYYY-MM-DD)
- Relationship for booking: {patient.get("relationship", "self")}

## Persona
{patient.get("persona", "Cooperative patient.")}

## Goal
{patient.get("goal", "Complete the scheduling task.")}

## Constraints
{constraint_block}
{clarify}
## Output rules
- Reply as the patient in 1–3 short sentences. No stage directions.
- Never call tools. Never invent clinic slot IDs the agent did not offer.
- When the conversation goal is met OR the agent clearly finished with an outcome
  you accept, reply with exactly: {DONE_TOKEN}
- If stuck after many turns, reply {DONE_TOKEN}.
"""


class PatientSimulator:
    """Produces the next patient utterance given the dialogue so far."""

    def __init__(self, model: str | None = None, temperature: float = 0.3) -> None:
        settings = get_settings()
        self._llm = ChatOpenAI(
            model=model or settings.patient_model,
            api_key=settings.openai_api_key or None,
            temperature=temperature,
        )

    def opening(self, patient: dict[str, Any]) -> str:
        return (patient.get("opening") or "Hello.").strip()

    def next_message(
        self,
        *,
        scenario_id: str,
        patient: dict[str, Any],
        transcript: list[dict[str, str]],
    ) -> str:
        """Return next patient line, or DONE_TOKEN when finished."""
        system = _build_system_prompt(patient, scenario_id)
        lines = ["Conversation so far:"]
        for turn in transcript:
            role = turn.get("role", "?")
            content = turn.get("content", "")
            label = "Patient" if role == "patient" else "Agent"
            lines.append(f"{label}: {content}")
        lines.append(
            "Write the patient's next message only "
            f"(or {DONE_TOKEN} if finished)."
        )
        resp = self._llm.invoke(
            [
                SystemMessage(content=system),
                HumanMessage(content="\n".join(lines)),
            ]
        )
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        return text.strip()

    @staticmethod
    def is_done(message: str) -> bool:
        cleaned = (message or "").strip()
        if cleaned.upper() == DONE_TOKEN:
            return True
        # Allow "DONE" on its own line at the end
        lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
        return bool(lines) and lines[-1].upper() == DONE_TOKEN
