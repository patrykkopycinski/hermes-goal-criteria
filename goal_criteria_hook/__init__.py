"""goal-criteria-hook — nudge the agent to attach criteria to a bare /goal.

Why this exists
---------------
A ``/goal <prose>`` with ``Criteria · 0`` lets the judge decide "done" on vibes,
and a clarifying question mid-loop reads as "unachievable" — the goal stalls.
The ``goal-criteria-assist`` skill makes attaching criteria *probable*; this hook
makes it *unconditional* by speaking up on the goal's turns while the criterion
count is still zero.

Design constraints (each verified against source, 2026-09-06):

* **Fires on every turn of the goal**, not just kickoff —
  ``agent/conversation_loop.py:1428`` calls ``build_turn_context`` per turn, which
  runs ``pre_llm_call`` (``turn_context.py:611``). So this hook must be cheap and
  self-limiting.

* **Silent once any criterion exists.** ``invoke_hook`` has no dedup; the hook
  itself decides whether to emit. It checks ``state.subgoals`` / contract and
  returns ``None`` the moment the count is non-zero. A re-injected "call
  goal_criteria" every turn would count as a no-progress idempotent stall
  (``agent/tool_guardrails.py`` blocks at 5 repeats) — the silent-when-done rule
  is what prevents that.

* **Nudge, never auto-write.** The hook returns context telling the agent to
  *propose and attach* criteria. It does not invent criteria itself — the model
  has the goal text and the user's anti-cheat standing rules; a hook has neither.
  Auto-generating generic criteria here would be the slop this exists to prevent.

* **Reads persisted state in-process.** ``GoalManager(session_id=...)`` needs a
  working SessionDB, which is only available in the agent process (out-of-process
  construction hits the hermes-runtime circular-import trap). Hooks run in-process,
  so this is safe here.

* **Fail-open.** Any exception reading goal state returns ``None`` — a hook must
  never break a turn.
"""

from __future__ import annotations

from typing import Any, Optional


def _goal_needs_criteria(session_id: str) -> Optional[dict]:
    """Return the goal state if it's active with zero criteria, else None.

    None means "stay silent": no goal, paused/blocked/done goal, or criteria
    already present (subgoals or a contract — both feed the judge).
    """
    try:
        from hermes_cli.goals import GoalManager
    except Exception:
        return None
    try:
        mgr = GoalManager(session_id=session_id)
        state = mgr.state
    except Exception:
        return None
    if state is None or getattr(state, "status", None) != "active":
        return None
    subgoals = [s for s in (getattr(state, "subgoals", None) or []) if str(s).strip()]
    contract = getattr(state, "contract", None)
    has_contract = bool(contract is not None and not contract.is_empty())
    if subgoals or has_contract:
        return None
    return {"goal": getattr(state, "goal", ""), "turns_used": getattr(state, "turns_used", 0)}


def on_pre_llm_call(*, session_id: str = "", user_message: Any = None,
                    is_first_turn: bool = False, **_: Any) -> Optional[dict]:
    """Inject a criteria-directive while the active goal has zero criteria.

    Returns ``{"context": ...}`` so the directive lands in the user message
    (``turn_context.py`` appends ``r["context"]`` to the user turn, never the
    system prompt — so it can't poison the prompt cache).
    """
    if not session_id:
        return None
    info = _goal_needs_criteria(session_id)
    if info is None:
        return None

    goal = (info["goal"] or "").strip()
    snippet = goal[:280] + ("…" if len(goal) > 280 else "")
    return {
        "context": (
            "[goal-criteria] This /goal has ZERO acceptance criteria, so the judge "
            "would decide 'done' on vibes and a clarifying question could park it as "
            "'unachievable'. Before diving into the work, attach evidence-phrased "
            "criteria with the goal_criteria tool (action=\"add_criteria\"), then a "
            "gate if a real command decides it (action=\"add_gate\").\n\n"
            "Goal: " + snippet + "\n\n"
            "Rules for the criteria:\n"
            "1. 3–7 items, each observable evidence (command exits 0, file exists, "
            "row appears in the matrix) — never 'looks done'.\n"
            "2. Include at least one ANTI-CHEAT criterion naming the shortcut that "
            "would fake completion (skip/retry/relaxed assertion) and forbidding it.\n"
            "3. If the goal is underspecified, put the unknown IN a criterion "
            "(\"suite path identified and named\") instead of pausing to ask.\n"
            "4. Show the criteria to the user before attaching when a wrong choice "
            "is costly; attach directly when the derivation is obvious.\n"
            "5. Gates must be LOCAL deterministic commands — an unchanged workspace "
            "replays a stale gate failure and pauses the goal, so never gate on "
            "remote state; for CI use ~/.hermes/scripts/ci-gate.sh.\n\n"
            "See skill goal-criteria-assist for the full pattern."
        )
    }


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
