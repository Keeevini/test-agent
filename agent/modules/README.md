# Agent customization modules

Drop optional, shareable agent customizations here.

This is where **per-agent behavior** that you want to keep in the repository lives — system-prompt
variants, extra tooling, and helper modules — as opposed to personal data (which belongs in
`vault/`) or per-machine secrets (which belong in `local/`).

Each module is self-contained. The core agent lives in `src/agent/`; anything in this folder is
additive and optional.
