# AI Web-Dev Office

A Chief Agent orchestrator for `prompt.txt`'s AI office: give it one
mission, it decomposes the work across ten specialist roles (Solution
Architect, Frontend/Backend Developer, UI/UX Designer, Security Engineer,
QA Engineer, Content/Marketing, Pricing/Proposal, Opportunity Scout,
Prospect Analyst), runs each as a real Claude API call, and synthesizes the
results into one review-ready deliverable. Every output is a draft — the
tool never executes code or deploys anything.

## Setup

```bash
python -m venv venv && source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
```

## Run

```bash
python cli.py
# or, non-interactive:
python cli.py "Build a business website with a product catalog and contact form."
```

Each run writes to `runs/<timestamp>_<mission-slug>/`:
- `deliverable.md` — the human-readable deliverable (all 6 sections from
  `prompt.txt`'s format, ending in the "Draft only" review banner)
- `run.json` — the same data structured, for auditing or feeding into
  another tool

### Mock mode (free — no API calls)

Add `--mock` (CLI) or check the "Mock mode" box (dashboard) to run the full
pipeline — decomposition, dependency ordering, concurrency, synthesis — with
canned responses instead of real Anthropic calls. Useful for testing the
orchestration itself without spending API credits:

```bash
python cli.py --mock "Build a business website with a product catalog and contact form."
python live_cli.py --mock "..."
```

It's driven by the `AI_OFFICE_MOCK` env var (`office/config.py: is_mock_mode`),
which `office/llm_client.py` checks before making any API call.

### Live terminal dashboard

```bash
python live_cli.py "Build a business website with a product catalog and contact form."
python live_cli.py --mock "..."   # combine with mock mode to test for free
```

A `rich`-powered terminal view of the same run: a live-updating table shows
every role's status (QUEUED/RUNNING/DONE/FAILED), its dependencies, and
elapsed time, while a scrolling log above it prints each hand-off as it
happens (e.g. "Frontend Developer picked up T3, using work from UI/UX
Designer").

### Web dashboard (optional)

```bash
python webapp.py
# then open http://127.0.0.1:5000
```

A local Flask dashboard: type a mission, optionally check "Mock mode", click
"Run mission", and watch each of the ten role cards flip from Standby →
Queued → Running → Done/Failed as `office/chief.py` works through the
dependency graph. It polls `GET /status` every ~1.2s and reuses
`run_mission`/`save_run` directly, so dashboard runs land in `runs/` exactly
like CLI runs.

## Test

```bash
python -m pytest tests/ -v
```

Tests mock the Anthropic client entirely, so they run with no API key and
no network access.

## How it works

1. **Decompose** (`office/chief.py: decompose_mission`) — one Chief Agent
   call, forced through a tool-call schema (not free-text JSON parsing) so
   the task breakdown is always valid: each sub-task gets an id, a
   description, a role constrained to the ten allowed values, and optional
   dependencies on other sub-task ids.
2. **Route & run** (`run_subtasks`) — sub-tasks execute in dependency
   order; anything whose dependencies are already done runs concurrently
   with its peers via a thread pool, so independent roles genuinely work in
   parallel rather than one at a time. A dependent sub-task receives its
   prerequisite's output as extra context (e.g. Frontend Developer sees the
   UI/UX Designer's wireframe).
3. **Synthesize** (`synthesize`) — a final Chief Agent call merges every
   role's draft into one coherent package and flags gaps/conflicts between
   roles.
4. **Format & persist** (`office/formatting.py`, `office/run_store.py`) —
   renders the exact 6-section deliverable format from `prompt.txt` and
   writes both a Markdown and a JSON copy per run.

## Design decisions

- **Tool-call forced decomposition, not prose JSON.** Asking Claude to
  "return JSON" in free text is fragile (extra prose, malformed brackets).
  Forcing a tool call with a JSON-schema `role` enum guarantees every
  sub-task maps to one of the ten real roles, with zero parsing logic.
- **Dependency-level concurrency.** `prompt.txt` says the Chief "decides
  sequencing and dependencies" when roles overlap — that's modeled directly
  as a `depends_on` list per sub-task, and independent roles run in
  parallel rather than strictly serially, since a real team wouldn't wait
  on unrelated work.
- **Failures degrade the deliverable, they don't abort it.** If one role's
  API call fails after retries, its section is marked `[FAILED: ...]` and
  the rest of the mission still completes — a partial draft is still
  useful, and everything here is a draft anyway.
- **No approval-workflow state machine.** Since the tool only ever writes
  files (never executes/deploys), the "human in the loop" requirement is
  satisfied structurally rather than with extra gating code — there's
  nothing for a human to "approve" past reading the output.

## Known limitations / what I'd change next

- **Anthropic-only.** `prompt.txt` mentions routing code to a different
  model (e.g. `gpt-oss-20b`); this build only varies Claude model *tier*
  per role (`office/config.py: MODEL_OVERRIDES`), not provider. Swapping in
  a second provider would mean adding a small adapter in
  `office/llm_client.py` and keying `MODEL_OVERRIDES` by provider+model.
- **No persistent mission history/search across runs.** `webapp.py` gives
  live status for the *current* run only (in-memory state, one process);
  there's still no index/search over past `runs/*/run.json` folders.
- **Single mission at a time** — no queue, so two people running missions
  concurrently just get two independent `runs/` folders; fine for solo use,
  would need a lock or a proper job queue for shared/production use.
