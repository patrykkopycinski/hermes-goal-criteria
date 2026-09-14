"""goal_criteria — let the agent put explicit acceptance criteria on the active /goal.

Why this exists
---------------
A bare ``/goal <text>`` runs with ``Criteria · 0``: the judge has nothing to check
against except the prose objective, so it either declares victory early or (the
observed failure) parks the loop as "unachievable" the moment the agent stops to
ask a question. Hermes already supports criteria via ``/subgoal`` and gates via
``/goal gate`` — but those are HUMAN slash commands. There is no agent-facing tool
and no CLI subcommand, so the agent cannot propose-and-attach criteria itself.

This plugin exposes the existing ``GoalManager`` mutators as one tool. It adds no
new goal semantics: ``add_subgoal``/``add_gate`` are Hermes' own methods, and the
judge already requires EVERY subgoal to be met before returning ``done``
(``goals.py`` JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE).

Safety
------
- Read-only ``show`` is always allowed.
- Mutations require an ACTIVE goal; ``GoalManager._require_goal()`` raises
  otherwise and we surface that as a normal tool error.
- Nothing here can mark a goal done, clear it, or change its status. Criteria can
  only be ADDED or listed — never silently removed — so the tool can only ever
  make the completion bar stricter, never looser.
"""

from typing import Any, Dict, List, Optional

TOOLSET = "goals"

_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["show", "add_criteria", "add_gate", "attach_plan"],
            "description": (
                "show = list the active goal's criteria and gates. "
                "add_criteria = append acceptance criteria the judge must ALL see met. "
                "add_gate = append a shell command that must exit 0 before the judge even runs. "
                "attach_plan = derive criteria from a plan file's own phase/track headers, plus "
                "a deferral-budget and coverage criterion — the plan defines the bar."
            ),
        },
        "criteria": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "For add_criteria. Each entry is ONE checkable outcome, phrased so a "
                "third party could verify it from evidence (a file, a command result, a "
                "count). Bad: 'works well'. Good: 'results for glm-5.3-flash appear as a "
                "row in target/llm_matrix_final with a non-null score'."
            ),
        },
        "command": {
            "type": "string",
            "description": "For add_gate. Shell command that must exit 0 (e.g. the repo's real test/lint gate).",
        },
        "path": {
            "type": "string",
            "description": (
                "For attach_plan. Path to the plan markdown. Its '## Phase' / "
                "'## Track' headers each become one criterion requiring a terminal "
                "disposition, so the plan artifact — not a summary of it — defines "
                "the completion bar."
            ),
        },
        "max_deferrals": {
            "type": "integer",
            "description": "For attach_plan. Deferrals above this count fail the goal (default 2).",
        },
        "timeout_seconds": {"type": "integer", "description": "Optional gate timeout."},
        "max_retries": {"type": "integer", "description": "Optional gate retry count."},
    },
    "required": ["action"],
}

_DESCRIPTION = (
    "Read or add acceptance criteria and quality gates on the ACTIVE /goal. "
    "Use right after the user sets a goal, to turn a prose objective into explicit "
    "checkable criteria the judge must ALL see met before it returns done. "
    "Criteria can only be added, never removed — the completion bar only tightens."
)

# The registry spreads this dict straight into the OpenAI function object
# (tools/registry.py: {**entry.schema, "name": ...}), so it must carry
# "description" + "parameters" — a bare JSON Schema here reaches the model as a
# tool with NO arguments and no explanation.
_SCHEMA = {
    "description": _DESCRIPTION,
    "parameters": _PARAMETERS,
}


def _manager(session_id: Optional[str]):
    """Build a GoalManager bound to THIS session. Imported lazily: hermes_cli pulls
    in config/state and must not be imported at plugin-registration time."""
    if not session_id:
        raise RuntimeError(
            "no session_id available — goal criteria are per-session state and cannot be resolved"
        )
    from hermes_cli import goals

    return goals.GoalManager(session_id=str(session_id))


def _render(mgr) -> str:
    state = getattr(mgr, "state", None)
    if state is None or not mgr.has_goal():
        return "No active goal. Criteria attach to a running /goal — start one first."
    subs: List[str] = list(getattr(state, "subgoals", []) or [])
    gates = [g.command for g in (getattr(state, "gates", []) or [])]
    lines = [f"Goal ({state.status}): {state.goal}", f"Criteria · {len(subs)}"]
    lines += [f"  {i}. {t}" for i, t in enumerate(subs, 1)] or ["  (none)"]
    lines.append(f"Gates · {len(gates)}")
    lines += [f"  {i}. {c}" for i, c in enumerate(gates, 1)] or ["  (none)"]
    return "\n".join(lines)


def _plan_headers(path: str) -> List[str]:
    """Phase/Track headers of a plan markdown. The artifact is read here, on
    purpose, rather than trusting the caller's summary of it — summary drift is
    how a partially-executed plan satisfies a goal's letter."""
    import re
    from pathlib import Path

    text_path = Path(path).expanduser()
    if not text_path.is_file():
        raise RuntimeError(
            f"{path} does not exist — attach_plan reads the plan artifact itself, "
            "so the path must resolve. Relative paths resolve against the cwd of "
            "the Hermes process; use an absolute path if unsure."
        )
    text = text_path.read_text(encoding="utf-8")
    # Work-item section shapes seen in real plans: Phase/Track (kibana-test-health
    # v3), Step, Milestone, Part. Anything else falls through to the refusal and
    # the caller falls back to per-item add_criteria.
    headers = re.findall(
        r"^#{2,3} (Phase \d+|Track [A-Z]\b|Step \d+|Milestone \d+|Part \d+)[^\n]*",
        text,
        re.M,
    )
    if not headers:
        raise RuntimeError(
            f"{path} has no '## Phase N' or '## Track X' headers — nothing to "
            "anchor criteria to. Either the path is wrong or the plan uses a "
            "different shape; use add_criteria instead."
        )
    # '## Implementation log' and similar trailing sections are not work items
    return headers


def _attach_plan(path: str, max_deferrals: int, mgr) -> str:
    headers = _plan_headers(path)
    items = []
    for header in headers:
        items.append(
            f'"{header}" (section in {path}) has a terminal disposition: implemented '
            "with command-level evidence in the final report, OR deferred with a "
            "named external blocker (credential, missing endpoint, human decision) "
            "and the deferral stated in the final report — sequencing preference "
            "alone is not a deferral."
        )
    items.append(
        f"Deferrals across the plan total at most {max_deferrals}; each deferral "
        "names its external blocker and its retry condition. A section left "
        "silent counts as not done, not as deferred."
    )
    items.append(
        "Every file created or modified by this run is covered by a test added or "
        "updated in the same run, or explicitly exempted with a reason in the "
        "final report."
    )
    for text in items:
        mgr.add_subgoal(text)
    return (
        f"Attached {len(items)} criteria derived from {len(headers)} work-item "
        f"sections of {path}. The plan artifact, not a summary of it, defines the "
        "completion bar.\n\n" + _render(mgr)
    )


def goal_criteria(
    action: str = "show",
    criteria: Optional[List[str]] = None,
    command: str = "",
    path: str = "",
    max_deferrals: int = 2,
    timeout_seconds: Optional[int] = None,
    max_retries: Optional[int] = None,
    session_id: Optional[str] = None,
    **_ignored: Any,
) -> str:
    mgr = _manager(session_id)

    if action == "show":
        return _render(mgr)

    if action == "add_criteria":
        items = [c.strip() for c in (criteria or []) if c and c.strip()]
        if not items:
            return "Error: 'criteria' must contain at least one non-empty string."
        added = []
        for text in items:
            # add_subgoal raises RuntimeError without an active goal — let it surface.
            added.append(mgr.add_subgoal(text))
        return (
            f"Added {len(added)} criterion/criteria to the active goal. "
            "The judge now requires ALL of them met before it returns done.\n\n" + _render(mgr)
        )

    if action == "add_gate":
        cmd = (command or "").strip()
        if not cmd:
            return "Error: 'command' is required for add_gate."
        gate = mgr.add_gate(cmd, timeout_seconds=timeout_seconds, max_retries=max_retries)
        return (
            f"Gate added: {gate.command} (timeout {gate.timeout_seconds}s, "
            f"max_retries {gate.max_retries}). A red gate short-circuits the judge.\n\n" + _render(mgr)
        )

    if action == "attach_plan":
        plan_path = (path or "").strip()
        if not plan_path:
            return "Error: 'path' is required for attach_plan."
        return _attach_plan(plan_path, int(max_deferrals), mgr)

    return f"Error: unknown action '{action}'. Use show | add_criteria | add_gate | attach_plan."


def _handler(args: Dict[str, Any], **kw: Any) -> str:
    """Registry dispatch: model args in ``args``, runtime context in ``kw``
    (mirrors tools/cronjob_tools.py::_cronjob_handler)."""
    try:
        return goal_criteria(
            action=args.get("action", "show"),
            criteria=args.get("criteria"),
            command=args.get("command", ""),
            path=args.get("path", ""),
            max_deferrals=args.get("max_deferrals", 2),
            timeout_seconds=args.get("timeout_seconds"),
            max_retries=args.get("max_retries"),
            session_id=kw.get("session_id"),
        )
    except Exception as exc:
        return f"goal_criteria error: {type(exc).__name__}: {exc}"


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="goal_criteria",
        toolset=TOOLSET,
        schema=_SCHEMA,
        handler=_handler,
        description=_DESCRIPTION,
        emoji="\u25ce",
    )
