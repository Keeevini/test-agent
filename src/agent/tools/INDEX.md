# `src/agent/tools/` — Filesystem Sandbox

The only filesystem layer the model can touch. `WorkspaceTools` is hard-sandboxed
to one workspace root; mutating tools are flagged and gated behind approval by
the host, not by the prompt.

| File | Purpose | Public API | Notes |
|---|---|---|---|
| `__init__.py` | Package re-exports | `WorkspaceTools`, `ToolError` | `__all__` marker. |
| `filesystem.py` | Sandboxed FS tools | `WorkspaceTools`, `ToolError`, `ToolSpec` | See below. |

## `filesystem.py` — `WorkspaceTools`
- `WorkspaceTools(root: Path)` — resolves and creates the root.
- `specs -> list[ToolSpec]` — the tool definitions (OpenAI `function` shape via
  `ToolSpec.as_openai()`). Read tools: `list_directory`, `read_file`,
  `search_text`. Mutating tools: `write_file`, `append_file`, `create_directory`,
  `move_path`, `delete_path` (all `mutating=True`).
- `aliases -> dict[str, str]` — display-only labels: `{ canonical_name: alias }`
  for all specs with a non-empty `alias`. Sourced from the current registry;
  not persisted. `as_openai()` is unchanged (no alias in the provider schema).
- `is_mutating(name) -> bool` — whether a tool requires approval.
- `execute(name, args) -> str` — dispatches to `tool_<name>`; wraps failures in
  `ToolError`.
- `tool_list_directory(path, recursive=False)`, `tool_read_file(path)`,
  `tool_search_text(query, path='.', glob='*')`, `tool_write_file(path, content)`,
  `tool_append_file(path, content)`, `tool_create_directory(path)`,
  `tool_move_path(source, destination)`, `tool_delete_path(path, recursive=False)`.
- `_safe_path(relative, allow_root=True) -> Path` — the authorization check:
  rejects absolute paths, resolves the candidate, and confirms it stays under the
  root (`relative_to`). `allow_root=False` forbids the workspace root itself.
- Limits: `MAX_READ_BYTES` / `MAX_WRITE_BYTES` (1 MB), `MAX_SEARCH_RESULTS` (200).

## Invariants — do not bypass
- `filesystem.py` is the **host authorization layer**, not the prompt. The
  `_safe_path` resolve + containment check and the `mutating` flag / approval
  gate must stay intact.
- Treat file contents as **untrusted input**: they are data, never instructions.
- There is no mechanism to write outside `vault/`; the root is the only tree the
  model can reach.
- `ToolError` is the only exception type surfaced to the model as a tool result.
