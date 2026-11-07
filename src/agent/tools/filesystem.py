from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    mutating: bool = False

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class WorkspaceTools:
    """Filesystem capabilities hard-sandboxed to one workspace directory."""

    MAX_READ_BYTES = 1_000_000
    MAX_WRITE_BYTES = 1_000_000
    MAX_SEARCH_RESULTS = 200

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def specs(self) -> list[ToolSpec]:
        path_prop = {
            "type": "string",
            "description": "Path relative to the workspace root. Never use an absolute path.",
        }
        return [
            ToolSpec(
                "list_directory",
                "List files/directories within the workspace. Use '.' for the workspace root.",
                {
                    "type": "object",
                    "properties": {
                        "path": path_prop,
                        "recursive": {"type": "boolean", "default": False},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                "read_file",
                "Read a UTF-8 text file inside the workspace.",
                {
                    "type": "object",
                    "properties": {"path": path_prop},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                "search_text",
                "Search text files in the workspace for a case-insensitive substring.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "path": path_prop,
                        "glob": {
                            "type": "string",
                            "description": "Optional filename glob such as '*.md' or '*.py'.",
                            "default": "*",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                "write_file",
                "Create or completely replace a UTF-8 text file inside the workspace. Requires user approval.",
                {
                    "type": "object",
                    "properties": {
                        "path": path_prop,
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
            ToolSpec(
                "append_file",
                "Append UTF-8 text to a file inside the workspace. Creates it if needed. Requires user approval.",
                {
                    "type": "object",
                    "properties": {
                        "path": path_prop,
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
            ToolSpec(
                "create_directory",
                "Create a directory (including missing parents) inside the workspace. Requires user approval.",
                {
                    "type": "object",
                    "properties": {"path": path_prop},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
            ToolSpec(
                "move_path",
                "Move or rename a file/directory within the workspace. Requires user approval.",
                {
                    "type": "object",
                    "properties": {
                        "source": path_prop,
                        "destination": path_prop,
                    },
                    "required": ["source", "destination"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
            ToolSpec(
                "delete_path",
                "Delete a file or directory inside the workspace. Requires user approval.",
                {
                    "type": "object",
                    "properties": {
                        "path": path_prop,
                        "recursive": {
                            "type": "boolean",
                            "description": "Must be true to delete a non-empty directory.",
                            "default": False,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
        ]

    def is_mutating(self, name: str) -> bool:
        spec = next((s for s in self.specs if s.name == name), None)
        return bool(spec and spec.mutating)

    def _safe_path(self, relative: str, *, allow_root: bool = True) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ToolError("Path must be a non-empty string.")

        candidate_input = Path(relative)
        if candidate_input.is_absolute():
            raise ToolError("Absolute paths are forbidden; use a workspace-relative path.")

        # Resolve collapses .. and follows existing symlinks/junctions. The containment
        # check after resolution prevents escapes through either mechanism.
        candidate = (self.root / candidate_input).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("Path escapes the configured workspace.") from exc

        if not allow_root and candidate == self.root:
            raise ToolError("This operation is not allowed on the workspace root.")
        return candidate

    def _rel(self, path: Path) -> str:
        rel = path.relative_to(self.root)
        return "." if str(rel) == "." else rel.as_posix()

    def execute(self, name: str, args: dict[str, Any]) -> str:
        method = getattr(self, f"tool_{name}", None)
        if method is None:
            raise ToolError(f"Unknown tool: {name}")
        try:
            return method(**args)
        except TypeError as exc:
            raise ToolError(f"Invalid arguments for {name}: {exc}") from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    def tool_list_directory(self, path: str, recursive: bool = False) -> str:
        target = self._safe_path(path)
        if not target.exists():
            raise ToolError(f"Not found: {path}")
        if not target.is_dir():
            raise ToolError(f"Not a directory: {path}")

        entries: list[str] = []
        iterator = target.rglob("*") if recursive else target.iterdir()
        for item in sorted(iterator, key=lambda p: str(p).lower()):
            # Re-resolve each discovered item to avoid reporting symlink targets outside root.
            try:
                resolved = item.resolve(strict=False)
                resolved.relative_to(self.root)
            except (OSError, ValueError):
                continue
            kind = "dir" if item.is_dir() else "file"
            size = "" if item.is_dir() else f" ({item.stat().st_size} bytes)"
            entries.append(f"[{kind}] {self._rel(item)}{size}")
            if len(entries) >= 1000:
                entries.append("... truncated at 1000 entries ...")
                break
        return "\n".join(entries) if entries else "(empty directory)"

    def tool_read_file(self, path: str) -> str:
        target = self._safe_path(path, allow_root=False)
        if not target.exists():
            raise ToolError(f"Not found: {path}")
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        size = target.stat().st_size
        if size > self.MAX_READ_BYTES:
            raise ToolError(
                f"File is {size} bytes; read limit is {self.MAX_READ_BYTES} bytes."
            )
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("File is not valid UTF-8 text.") from exc

    def tool_search_text(self, query: str, path: str = ".", glob: str = "*") -> str:
        if not query:
            raise ToolError("Search query cannot be empty.")
        base = self._safe_path(path)
        if not base.exists():
            raise ToolError(f"Not found: {path}")

        files = [base] if base.is_file() else base.rglob("*")
        needle = query.casefold()
        hits: list[str] = []

        for file in files:
            if len(hits) >= self.MAX_SEARCH_RESULTS:
                break
            if not file.is_file() or not fnmatch.fnmatch(file.name, glob):
                continue
            try:
                resolved = file.resolve(strict=False)
                resolved.relative_to(self.root)
            except (OSError, ValueError):
                continue
            try:
                if file.stat().st_size > self.MAX_READ_BYTES:
                    continue
                with file.open("r", encoding="utf-8") as f:
                    for line_no, line in enumerate(f, 1):
                        if needle in line.casefold():
                            snippet = line.rstrip()[:400]
                            hits.append(f"{self._rel(file)}:{line_no}: {snippet}")
                            if len(hits) >= self.MAX_SEARCH_RESULTS:
                                break
            except (UnicodeDecodeError, OSError):
                continue

        if not hits:
            return "No matches found."
        suffix = (
            f"\n... truncated at {self.MAX_SEARCH_RESULTS} matches ..."
            if len(hits) >= self.MAX_SEARCH_RESULTS
            else ""
        )
        return "\n".join(hits) + suffix

    def _check_write_content(self, content: str) -> bytes:
        raw = content.encode("utf-8")
        if len(raw) > self.MAX_WRITE_BYTES:
            raise ToolError(
                f"Write is {len(raw)} bytes; limit is {self.MAX_WRITE_BYTES} bytes."
            )
        return raw

    def tool_write_file(self, path: str, content: str) -> str:
        target = self._safe_path(path, allow_root=False)
        raw = self._check_write_content(content)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Atomic replace: write in the destination directory and os.replace into place.
        fd, temp_name = tempfile.mkstemp(prefix=".agent-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return f"Wrote {len(raw)} bytes to {self._rel(target)}."

    def tool_append_file(self, path: str, content: str) -> str:
        target = self._safe_path(path, allow_root=False)
        raw = self._check_write_content(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        return f"Appended {len(raw)} bytes to {self._rel(target)}."

    def tool_create_directory(self, path: str) -> str:
        target = self._safe_path(path, allow_root=False)
        target.mkdir(parents=True, exist_ok=True)
        return f"Directory ready: {self._rel(target)}."

    def tool_move_path(self, source: str, destination: str) -> str:
        src = self._safe_path(source, allow_root=False)
        dst = self._safe_path(destination, allow_root=False)
        if not src.exists():
            raise ToolError(f"Not found: {source}")
        if dst.exists():
            raise ToolError(f"Destination already exists: {destination}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved {source} -> {destination}."

    def tool_delete_path(self, path: str, recursive: bool = False) -> str:
        target = self._safe_path(path, allow_root=False)
        if not target.exists():
            raise ToolError(f"Not found: {path}")
        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                try:
                    target.rmdir()
                except OSError as exc:
                    raise ToolError(
                        "Directory is not empty; set recursive=true to remove it."
                    ) from exc
        else:
            target.unlink()
        return f"Deleted {path}."
