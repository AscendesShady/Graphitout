"""Backend, stdio protocol, and cross-process revision checks using disposable projects."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from graphitout.model import ConflictError, GraphStore, changes, validate_graph
from graphitout.scan import identifier, inventory, merge_mapping, read_source, scan_project

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / 'qa' / 'output'
OUTPUT.mkdir(exist_ok=True)


def fixture(root: Path):
    sources = {
        "app.py": "from package.service import process, Service\nimport package.storage as storage\n\ndef run():\n    process()\n    storage.write_record('x')\n    s = Service()\n    return s.handle()\n\ndef indirect(process):\n    return process()\n",
        "package/__init__.py": '"""Example package."""\n',
        "package/service.py": "from .storage import write_record\n\ndef process():\n    return write_record('order')\n\nclass Service:\n    def handle(self):\n        return self.retry()\n\n    def retry(self):\n        return write_record('retry')\n",
        "package/storage.py": '"""Storage responsibilities."""\ndef write_record(value):\n    return value\n',
        "client.js": "import { endpoint } from './api.js';\nconst description = \"import './fake.js'\";\n// import './ignored.js';\nexport const client = endpoint;\n",
        "api.js": "export const endpoint = '/orders';\n",
        "fake.js": "export const fake = true;\n",
        "README.md": "# Disposable project\n",
        "custom.nim": "proc main() = echo \"A different project language\"\n",
        "broken.py": "def broken(:\n",
        "never_execute.py": "from pathlib import Path\nPath('EXECUTED.txt').write_text('bad')\nraise RuntimeError('Source must never execute')\n",
        ".env": "TOKEN=do-not-read\n",
        "node_modules/ignored.js": "throw new Error('excluded');\n",
    }
    for file, source in sources.items():
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


class ModelAndMapping(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=OUTPUT, prefix='mcp-test-')
        self.root = Path(self.temp.name)
        assert self.root.resolve().is_relative_to(OUTPUT.resolve())
        fixture(self.root)
        self.store = GraphStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def mapped(self):
        state = self.store.read()
        return self.store.save(merge_mapping(state["graph"], scan_project(self.root)), state["revision"])

    def test_python_calls_imports_and_js_imports_are_grounded(self):
        graph = self.mapped()["graph"]
        nodes = {n["id"]: n for n in graph["nodes"]}
        pairs = {(nodes[e["from"]].get("file"), nodes[e["from"]].get("symbol"), nodes[e["to"]].get("file"), nodes[e["to"]].get("symbol"), e["relation"]) for e in graph["edges"]}
        self.assertIn(("app.py", "run", "package/service.py", "process", "calls"), pairs)
        self.assertIn(("app.py", "run", "package/storage.py", "write_record", "calls"), pairs)
        self.assertIn(("package/service.py", "Service.handle", "package/service.py", "Service.retry", "calls"), pairs)
        self.assertIn(("client.js", None, "api.js", None, "imports"), pairs)
        self.assertNotIn(("client.js", None, "fake.js", None, "imports"), pairs)
        self.assertFalse(any(p[1] == "indirect" and p[4] == "calls" for p in pairs), "A shadowed parameter must not be treated as an imported function")
        self.assertFalse(any(e["relation"] == "calls" and nodes[e["to"]].get("symbol") == "Service.handle" for e in graph["edges"]), "Unknown instance receiver types must not be guessed")
        self.assertTrue(all(e["evidence"] for e in graph["edges"]))
        self.assertTrue(any("broken.py" in d for d in graph["project"]["diagnostics"]))
        self.assertFalse((self.root / "EXECUTED.txt").exists())

    def test_layout_and_port_edits_do_not_request_code_changes(self):
        state = self.mapped()
        graph = state["graph"]
        graph["nodes"][0]["x"] += 200
        graph["edges"][0]["fromPort"] = "bottom"
        self.assertFalse(changes(graph)["hasChanges"])
        graph["nodes"][0]["description"] = "Use a queue and handle retries."
        delta = changes(graph)
        self.assertTrue(delta["hasChanges"])
        self.assertEqual(len(delta["nodes"]["changed"]), 1)

    def test_refresh_keeps_sketches_intentions_layout_and_deletions(self):
        state = self.mapped()
        graph = state["graph"]
        first = graph["nodes"][0]
        first["x"] = 460
        first["description"] = "Intended new responsibility."
        deleted = graph["nodes"].pop()
        graph["edges"] = [e for e in graph["edges"] if e["from"] != deleted["id"] and e["to"] != deleted["id"]]
        graph["nodes"].append({"id": "idea", "type": "note", "title": "Initial sketch", "description": "Still independent", "x": 0, "y": 900})
        merged = merge_mapping(graph, scan_project(self.root))
        kept = next(n for n in merged["nodes"] if n["id"] == first["id"])
        self.assertEqual(kept["description"], "Intended new responsibility.")
        self.assertEqual(kept["x"], 460)
        self.assertTrue(any(n["id"] == "idea" for n in merged["nodes"]))
        self.assertFalse(any(n["id"] == deleted["id"] for n in merged["nodes"]))

    def test_project_boundary_fingerprints_and_scan_limits(self):
        with self.assertRaises(ValueError):
            read_source(self.root, "../outside.py")
        with self.assertRaises(ValueError):
            read_source(self.root, ".env")
        with self.assertRaises(ValueError):
            read_source(self.root, "node_modules/ignored.js")
        limited = inventory(self.root, 2)
        self.assertEqual(len(limited["files"]), 2)
        self.assertTrue(limited["diagnostics"])
        text, digest = read_source(self.root, "app.py")
        self.assertEqual(len(digest), 64)
        self.assertIn("def run", text)
        self.assertIn('proc main', read_source(self.root, 'custom.nim')[0])

    def test_portable_metadata_conflicts_and_restart(self):
        state = self.mapped()
        graph = state["graph"]
        graph["workflow"] = {"goal": "Build reliably", "decisions": "Queue events", "reviewNotes": "Try another agent"}
        state = self.store.save(graph, state["revision"])
        self.assertEqual(GraphStore(self.root).read(), state)
        with self.assertRaises(ConflictError):
            self.store.save(graph, 0)
        pack = self.store.handoff()
        self.assertNotIn(str(self.root), json.dumps(pack))
        self.assertEqual(validate_graph(pack["graph"])["workflow"]["reviewNotes"], "Try another agent")
        self.assertTrue(pack["graph"]["codeBaseline"])
        graph["nodes"][0]["file"] = "../escape.py"
        with self.assertRaises(ValueError):
            validate_graph(graph)

    def test_read_errors_do_not_embed_absolute_roots_in_portable_notes(self):
        with patch('graphitout.scan.read_source', side_effect=PermissionError(13, 'Denied', str(self.root / 'app.py'))):
            result = inventory(self.root)
        self.assertTrue(result['diagnostics'])
        self.assertNotIn(str(self.root), json.dumps(result['diagnostics']))

    def test_cross_process_writes_cannot_silently_overwrite(self):
        code = "from pathlib import Path; import json,sys,time; from graphitout.model import GraphStore,ConflictError; s=GraphStore(Path(sys.argv[1])); g=s.read()['graph']; g['name']=sys.argv[2]; time.sleep(.2);\ntry:\n print(s.save(g,0)['revision'])\nexcept ConflictError:\n print('conflict')\n"
        one = subprocess.Popen([sys.executable, "-c", code, str(self.root), "Agent one"], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        two = subprocess.Popen([sys.executable, "-c", code, str(self.root), "Agent two"], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        results = [one.communicate(timeout=10), two.communicate(timeout=10)]
        self.assertEqual(sorted(out.strip() for out, _ in results), ["1", "conflict"])
        self.assertFalse(any(err for _, err in results))


class MCPProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_stdio_clients_can_discover_and_use_tools(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        with tempfile.TemporaryDirectory(dir=OUTPUT, prefix='mcp-legacy-') as directory:
            root = Path(directory)
            assert root.resolve().is_relative_to(OUTPUT.resolve())
            fixture(root)
            params = StdioServerParameters(command=sys.executable, args=[str(REPO / 'graphitout_mcp.py'), '--project', str(root)], cwd=REPO)
            async with Client(params, mode='legacy', read_timeout_seconds=15) as client:
                result = await client.call_tool('get_graph')
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content['revision'], 0)

    async def test_real_stdio_tools_resource_plan_handoff_and_attestation(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        with tempfile.TemporaryDirectory(dir=OUTPUT, prefix='mcp-protocol-') as directory:
            root = Path(directory)
            assert root.resolve().is_relative_to(OUTPUT.resolve())
            fixture(root)
            original = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            params = StdioServerParameters(command=sys.executable, args=[str(REPO / "graphitout_mcp.py"), "--project", str(root)], cwd=REPO)
            async with Client(params, read_timeout_seconds=15) as client:
                tools = await client.list_tools()
                self.assertIn("map_project", {t.name for t in tools.tools})
                async def call(name, **arguments):
                    result = await client.call_tool(name, arguments)
                    self.assertFalse(result.is_error, result.content)
                    return result.structured_content
                info = await call("get_project_info")
                self.assertEqual(info["revision"], 0)
                mapped = await call("map_project", expected_revision=0)
                self.assertGreater(mapped["nodes"], 10)
                state = await call("get_graph")
                node = next(n for n in state["graph"]["nodes"] if n.get("file") == "package/storage.py" and not n.get("symbol"))
                edited = await call("edit_graph", expected_revision=state["revision"], operations=[{"op": "upsert_node", "node": {"id": node["id"], "description": "Persist orders with explicit validation."}}])
                plan = await call("get_implementation_plan")
                self.assertTrue(plan["changes"]["hasChanges"])
                self.assertEqual(len(plan["changes"]["nodes"]["changed"]), 1)
                await call("set_workflow", expected_revision=edited["revision"], goal="Improve reliability", decisions="Validate before storage", review_notes="Second agent: also check retries")
                pack = await call("get_review_pack")
                self.assertEqual(pack["graph"]["workflow"]["reviewNotes"], "Second agent: also check retries")
                self.assertNotIn(str(root), json.dumps(pack))
                resource = await client.read_resource("graphitout://graph")
                self.assertTrue(resource.contents)
                # Every server operation so far must leave the actual project source unchanged.
                self.assertEqual(original, {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and ".graphitout" not in p.parts})
                # Simulate the coding agent using its ordinary file editor, then reporting verification.
                source = root / "package/storage.py"
                source.write_text('"""Storage responsibilities."""\ndef write_record(value):\n    if value is None:\n        raise ValueError("Missing record")\n    return value\n', encoding="utf-8")
                changed = await call("get_implementation_plan")
                self.assertIn("package/storage.py", changed["sourceChanges"]["modified"])
                current = await call("get_graph")
                await call("acknowledge_implementation", expected_revision=current["revision"], verification_summary="Fixture agent added input validation; inspected the resulting function.")
                accepted = await call("get_implementation_plan")
                self.assertFalse(accepted["changes"]["hasChanges"])
                stale = await client.call_tool("edit_graph", {"operations": [], "expected_revision": 0})
                self.assertTrue(stale.is_error)
                self.assertIn('current revision', stale.content[0].text)


if __name__ == "__main__":
    unittest.main()
