# Optional agent customizations (`agent/`)

This top-level directory is reserved for optional, shareable or personal
agent skills/configuration adapters (for example Obsidian or research skills).
It is **not** the application's Python agent runtime and is not the default
location for app feature development.

To change the running agent framework, model integration, tool authorization,
chat UI, sessions, or API, begin at root `AGENTS.md`, then `src/AGENTS.md` and
`src/INDEX.md`. The runtime implementation is under `src/agent/`.

Keep private credentials in `local/` and personal knowledge in `vault/`, not
in tracked customization modules. Nothing in this directory is automatically
loaded into the runtime unless a future explicit integration enables it.
