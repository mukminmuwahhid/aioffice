# AI Web-Dev Office

A Chief Agent orchestrator for `prompt.txt`'s AI office: give it one
mission, it decomposes the work across ten specialist roles (Solution
Architect, Frontend/Backend Developer, UI/UX Designer, Security Engineer,
QA Engineer, Content/Marketing, Pricing/Proposal, Opportunity Scout,
Prospect Analyst), runs each as a real Claude API call, and synthesizes the
results into one review-ready deliverable. Every output is a draft — the
tool never executes code or deploys anything.

## Setup

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in ANTHROPIC_API_KEY
```

`requirements.txt` pins the direct dependencies. Use `requirements.lock` when
you need to reproduce the exact dependency set used for development.

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
```

## Run

```bash
python cli.py
# or, non-interactive:
python cli.py "Build a business website with a product catalog and contact form."
```

Each run writes to `runs/<timestamp>_<unique-id>_<mission-slug>/`:
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

A local Flask workspace with four views:

- **The Office** — type a mission, run it, and watch each desk on the office
  floor flip from Standby → Queued → Running → Done/Failed as
  `office/chief.py` works through the dependency graph. Click any desk to
  read all assignments given to that agent and each completed draft as soon
  as it arrives. Live counters show elapsed
  time, API calls, tokens, and an estimated cost. **Stop** cancels a run —
  in-flight calls finish, nothing new is dispatched, and synthesis is skipped
  so a cancel doesn't cost another call.
- **Run History** — every saved run with its agent count, failures, duration
  and cost. Open one to read each agent's draft, re-run its mission, or
  delete it.
- **Team Roster** — edit any agent: display name, icon, accent colour, desk
  tagline, model, and its full system prompt. Disabling an agent removes it
  from the Chief's assignable roles, so it is never given work (fewer calls
  per mission). "Reset to default" restores the stock definition.
- **Settings** — default and Chief models, retry count, max parallel agents,
  per-call token caps, and a workspace-wide mock-mode default, plus a health
  panel (API key detected, effective models, folders) and the model pricing
  table used for cost estimates.

Edits are stored in `.office/` (gitignored) and layer on top of the code
defaults, so deleting that folder restores stock behaviour. The dashboard
reuses `run_mission`/`save_run` directly, so its runs land in `runs/` exactly
like CLI runs.

## Test

```powershell
.venv\Scripts\python -m pytest tests/ -v
```

Tests mock the Anthropic client entirely, so they run with no API key and
no network access. They cover orchestration (dependency order, concurrency,
failure capture, cancellation, dependency-graph validation, disabled-agent
filtering and live output events), Flask API validation, the settings/agent
override store, collision-resistant run persistence and its run-id
path-traversal guard, the deliverable format, and the schema.

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
  per role, not provider. Swapping in a second provider would mean adding a
  small adapter in `office/llm_client.py` and keying the model settings by
  provider+model.
- **Single mission at a time** — the dashboard holds one run's state in
  memory in one process, so there's no queue and no concurrent missions.
  Fine for solo use; shared use would need a job queue and a real datastore.
- **Cancel is cooperative.** Stopping a mission prevents new subtasks from
  being dispatched and skips synthesis, but calls already in flight run to
  completion (and are still billed).
- **Cost figures are estimates.** They come from a pricing table in
  `office/config.py` multiplied by reported token usage — useful for relative
  comparison, not a substitute for the Anthropic console.
- **No auth.** The dashboard binds to localhost and assumes a single trusted
  user; the agent-editing and delete endpoints have no access control, so
  don't expose it on a network as-is.
