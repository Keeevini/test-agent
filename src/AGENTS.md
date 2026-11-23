# Source development instructions

Read `INDEX.md` in this directory first, then follow the nearest feature index.
Indexes describe **interfaces and ownership**, not implementation details.
Do not scan the whole source tree when a module's owner is already identified.

## Architecture
- `app.py` composes FastAPI, dependency objects, lifespan, and static mounting.
- `api/` owns HTTP routes and transport schemas; business logic remains in
  `agent/`, never duplicated in endpoints.
- `agent/` is the **runtime** orchestration, model, tool, and persistence code.
- `static/js/conversation/` owns transcripts, activity cards, and chat streaming.
- `static/js/sessions/` owns active session state and sidebar session history.
- `static/js/shell/` owns shell-level status, dialogs, and responsive drawer.
- `static/css/` owns styles and theme variables by domain.
- Root-level `agent/` (outside `src/`) is optional customization, NOT runtime code.

## Boundary rules
- Keep browser modules as explicit ES modules; no circular imports or
  globally writable feature state.
- Keep session and active-turn state owned by one controller each.
- Keep routes, event shapes, DOM IDs/classes, SQLite records, and approvals
  compatible unless a task explicitly changes their contracts.
- Do not weaken `agent/tools/filesystem.py` path containment or mutation approval.
- Keep secrets in `local/`, user workspace content in `vault/`.
- Run the deployed application only through the project's Docker Compose setup.

## Maintaining indexes
Every `src/` folder with >3 direct code files (`.py`, `.js`, `.css`, `.html`,
including `__init__.py`) requires `INDEX.md`; add one for smaller folders
when it reduces discovery cost. When a new file/function/public contract appears:
1. Update that folder's index in the same change.
2. Include each file's responsibility, exported names/signatures, inputs,
   outputs, side effects, and important invariants.
3. Update parent indexes only when structure/ownership/navigation changes.
4. Prefer concise, navigable entries over pasted code, implementation prose,
   or duplicated details from parent indexes.
5. Keep indexes consistent with source; never claim a test was run unless it was.

## Inline comments
Keep comments for non-obvious algorithmic decisions, security constraints,
async timing, browser workarounds, or why code must remain ordered.
Put discoverability/documentation of public entrypoints in the nearest index.
Avoid comments that merely narrate what the next line of code already says.
