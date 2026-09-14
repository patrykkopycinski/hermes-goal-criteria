# goal-criteria

Two Hermes Agent plugins that make `/goal` loops run against explicit,
checkable criteria instead of vibes — and, when the goal is "execute this
plan", against the **plan artifact itself**.

* **`goal-criteria`** — registers the `goal_criteria` tool:
  * `show` — list the active goal's criteria and gates
  * `add_criteria` — append acceptance criteria the judge must ALL see met
  * `add_gate` — append a shell command that must exit 0 before the judge runs
  * `attach_plan` — **derive criteria from a plan file's own work-item
    sections** (`## Phase N`, `## Track X`, `### Step N`, `## Milestone N`,
    `## Part N`), plus a deferral budget and a test-coverage criterion
* **`goal-criteria-hook`** — `pre_llm_call` hook that nudges the agent to
  attach criteria on every turn of a goal that still has zero, and goes silent
  the moment one exists.

## Why `attach_plan`

Before it existed, a goal like "take plan.md and execute it" could be satisfied
by criteria written from a *summary* of the plan: the run shipped Track A and
two phases, four sections went silently unfinished, and the goal completed
"legitimately." Summary drift is the failure mode. `attach_plan` reads the plan
markdown itself and derives one terminal-disposition criterion per work-item
section:

> implemented with command-level evidence, OR deferred with a named external
> blocker (credential, missing endpoint, human decision) — sequencing
> preference alone is not a deferral.

Plus two cross-cutting criteria: a deferral budget (`max_deferrals`, default 2)
and "every file created/modified is test-covered or explicitly exempted."

## Install

```bash
hermes plugins install patrykkopycinski/hermes-goal-criteria
```

or manually: clone into `~/.hermes/plugins/` (each of `goal_criteria/` and
`goal_criteria_hook/` is a self-contained plugin directory; rename if you
prefer the hyphenated names the local copies use — the loader reads
`plugin.yaml`, not the directory name).

## Usage

```
goal_criteria(action="attach_plan", path="/abs/path/plan.md", max_deferrals=2)
```

Refusals (by design): missing `path`, nonexistent file, no recognizable
work-item headers — one-sentence errors, never defaults.

## Verification

End-to-end tested against a real 7-section plan in the Hermes venv
(`GoalManager.set` → `attach_plan` → 9 criteria persisted across `show`),
plus refusal paths and a multi-shape synthetic plan (Step/Milestone/Part).

## License

MIT
