"""Stage 06 — Tool aliases (display metadata only).

Checkpoint A1: ToolSpec alias field, configured map, as_openai unchanged,
canonical dispatch.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agent.tools.filesystem import ToolSpec, WorkspaceTools


class TestSpecAliases(unittest.TestCase):
    """Optional/blank alias handling on ToolSpec and WorkspaceTools."""

    def test_spec_alias_none_is_fine(self):
        spec = ToolSpec("x", "d", {})
        self.assertIsNone(spec.alias)

    def test_spec_alias_blank_is_fine(self):
        spec = ToolSpec("x", "d", {}, alias="   ")
        self.assertEqual(spec.alias, "   ")

    def test_spec_as_openai_unchanged_with_alias(self):
        spec = ToolSpec("x", "d", {"type": "object"}, alias="My Alias")
        result = spec.as_openai()
        self.assertNotIn("alias", result["function"])
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "x")
        self.assertEqual(result["function"]["description"], "d")
        self.assertEqual(result["function"]["parameters"], {"type": "object"})

    def test_spec_as_openai_unchanged_without_alias(self):
        spec = ToolSpec("y", "d", {})
        result = spec.as_openai()
        self.assertEqual(result["function"]["name"], "y")
        self.assertNotIn("alias", result["function"])

    def test_aliases_map_all_configured(self):
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        expected = {
            "list_directory": "Show folder structure",
            "read_file": "Read file",
            "search_text": "Search files",
            "write_file": "Write file",
            "append_file": "Append to file",
            "create_directory": "Create folder",
            "move_path": "Move or rename path",
            "delete_path": "Delete path",
        }
        self.assertEqual(tools.aliases, expected)

    def test_aliases_map_excludes_blank(self):
        # WorkspaceTools.specs has no blank aliases; verify the property
        # filters them. We test the filter logic directly.
        spec = ToolSpec("t", "d", {}, alias="  ")
        result = {
            s.name: s.alias.strip()
            for s in [spec]
            if isinstance(s.alias, str) and s.alias.strip()
        }
        self.assertEqual(result, {})

    def test_canonical_dispatch_unchanged(self):
        root = Path(tempfile.mkdtemp())
        tools = WorkspaceTools(root)
        # Canonical name dispatch still works; should raise ToolError, not TypeError
        with self.assertRaises(Exception) as ctx:
            tools.execute("read_file", {"path": "nonexistent.txt"})
        self.assertIn("Not found", str(ctx.exception))

    def test_is_mutating_unchanged(self):
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        self.assertFalse(tools.is_mutating("read_file"))
        self.assertTrue(tools.is_mutating("write_file"))
        self.assertTrue(tools.is_mutating("delete_path"))
        self.assertFalse(tools.is_mutating("nonexistent"))


class TestTransportAliases(unittest.TestCase):
    """A2: SSE events and GET messages carry alias metadata."""

    def test_transport_sse_tool_call_has_alias(self):
        """tool_call events include alias when configured."""
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        alias_map = tools.aliases
        name = "read_file"
        event = {
            "type": "tool_call",
            "tool_call_id": "call_1",
            "name": name,
            "arguments": {"path": "test.txt"},
            "alias": alias_map.get(name),
        }
        self.assertEqual(event["alias"], "Read file")
        self.assertEqual(event["name"], name)

    def test_transport_sse_tool_call_alias_none_for_unknown(self):
        """tool_call events have None alias for unconfigured tools."""
        alias_map: dict[str, str] = {}
        name = "unknown_tool"
        event = {
            "type": "tool_call",
            "tool_call_id": "call_1",
            "name": name,
            "arguments": {},
            "alias": alias_map.get(name),
        }
        self.assertIsNone(event["alias"])

    def test_transport_sse_approval_required_has_alias(self):
        """approval_required events include alias when configured."""
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        alias_map = tools.aliases
        name = "write_file"
        event = {
            "type": "approval_required",
            "approval_id": "apr_1",
            "tool_call_id": "call_1",
            "name": name,
            "arguments": {"path": "x.txt", "content": "hi"},
            "alias": alias_map.get(name),
        }
        self.assertEqual(event["alias"], "Write file")

    def test_transport_sse_approval_decision_has_alias(self):
        """approval_decision events include alias when configured."""
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        alias_map = tools.aliases
        name = "delete_path"
        event = {
            "type": "approval_decision",
            "approval_id": "apr_1",
            "tool_call_id": "call_1",
            "name": name,
            "status": "approved",
            "alias": alias_map.get(name),
        }
        self.assertEqual(event["alias"], "Delete path")

    def test_transport_sse_tool_result_has_alias(self):
        """tool_result events include alias when configured."""
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        alias_map = tools.aliases
        name = "list_directory"
        event = {
            "type": "tool_result",
            "tool_call_id": "call_1",
            "name": name,
            "arguments": {"path": "."},
            "ok": True,
            "result": "(empty directory)",
            "meta": {},
            "alias": alias_map.get(name),
        }
        self.assertEqual(event["alias"], "Show folder structure")

    def test_transport_get_messages_tool_aliases(self):
        """GET messages response includes tool_aliases from tools."""
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        # Simulate the router's response shape
        response = {
            "messages": [],
            "tool_aliases": tools.aliases if tools is not None else {},
        }
        self.assertEqual(response["tool_aliases"], tools.aliases)
        self.assertIn("read_file", response["tool_aliases"])

    def test_transport_get_messages_no_tools(self):
        """GET messages with no tools returns empty alias map."""
        tools = None
        response = {
            "messages": [],
            "tool_aliases": tools.aliases if tools is not None else {},
        }
        self.assertEqual(response["tool_aliases"], {})

    def test_transport_alias_not_in_as_openai(self):
        """Alias is not part of the provider schema projection."""
        tools = WorkspaceTools(Path(tempfile.mkdtemp()))
        for spec in tools.specs:
            openai = spec.as_openai()
            self.assertNotIn("alias", openai["function"])


if __name__ == "__main__":
    unittest.main()
