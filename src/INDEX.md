# `src/` — Source Map

**Read first:** `src/AGENTS.md`. Navigate to the most specific index; do not
open unrelated code files for orientation.

| Need to change | Read next | Source owner / public interface |
|---|---|---|
| Startup, dependency wiring, static mount | `app.py`, `api/INDEX.md` | `app` FastAPI object; app lifespan |
| HTTP endpoints, request/response schemas | `api/INDEX.md` | Route factories for chat/sessions/health |
| Agent turn events, model calls, tool approvals | `agent/INDEX.md` | `LocalAgent.stream_turn`, `ModelClient`, `ApprovalManager` |
| Session DB and saved messages | `agent/session/INDEX.md` | `SessionStore`, repository methods |
| Filesystem sandbox | `agent/tools/INDEX.md` | `WorkspaceTools`; security boundary |
| Frontend screens, interaction, stream rendering | `static/INDEX.md` | `static/app.js` bootstrap + domain modules |
| Sidebar sessions / title hover | `static/js/sessions/INDEX.md` | Session controller/list/title-scroll |
| Activity cards / chat UI | `static/js/conversation/INDEX.md` | Conversation controllers and renderers |
| Theme palette / CSS layout | `static/css/INDEX.md` | Tokens and domain stylesheets |

Runtime `src/agent/` is distinct from root `agent/` customization modules.
Keep `src/` indexes current under the policy in `src/AGENTS.md`.
