"""Project-independent MCP tools for observed code, intended workflows, and handoff."""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .bridge import EditorBridge
from .model import GraphStore, HANDOFF_INSTRUCTIONS, changes, implementation_brief, timestamp, validate_graph
from .scan import changed_source_files, inventory, merge_mapping, read_source, scan_project


def create_server(store: GraphStore, bridge: EditorBridge) -> MCPServer:
    mcp = MCPServer("Graphitout", version="2.0.0", instructions=HANDOFF_INSTRUCTIONS)
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    graph_write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    def checked(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except (ValueError, KeyError, TypeError, OSError) as error:
                raise ToolError(str(error)) from error
        return invoke

    @mcp.tool(annotations=read_only, structured_output=True)
    @checked
    def get_project_info() -> dict[str, Any]:
        """Get the connected folder, editor link, current revision, and analysis boundaries. Open editor_url in a browser."""
        state = store.read()
        return {"projectFolder": str(store.root), "projectName": store.root.name, "editor_url": bridge.url,
                "revision": state["revision"], "sourceWrites": False, "instructions": HANDOFF_INSTRUCTIONS}

    @mcp.tool(annotations=read_only, structured_output=True)
    @checked
    def list_project_files(max_files: int = 1000) -> dict[str, Any]:
        """Inventory text source, configuration, and documentation in any project. Reports omissions; ignores dependency/build/hidden folders."""
        return inventory(store.root, max_files)

    @mcp.tool(annotations=read_only, structured_output=True)
    @checked
    def read_project_file(file: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
        """Read a project-relative text source with line numbers and SHA-256. Never imports or executes source. Maximum 500 lines per call."""
        if type(start_line) is not int or type(end_line) is not int or start_line < 1 or end_line < start_line or end_line - start_line >= 500:
            raise ValueError("Request a positive line range of at most 500 lines.")
        text, digest = read_source(store.root, file)
        lines = text.splitlines()
        return {"file": file, "sha256": digest, "totalLines": len(lines), "startLine": start_line,
                "text": "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines) if start_line <= i + 1 <= end_line)}

    @mcp.tool(annotations=graph_write, structured_output=True)
    @checked
    def map_project(expected_revision: int, include_symbols: bool = True, max_files: int = 300) -> dict[str, Any]:
        """Map any folder: file inventory, Python AST imports/resolved calls, literal JS/TS relative imports. Preserve sketches, layout, intended edits/deletions. Establish a code baseline; report limitations."""
        current = store.read()
        if current["revision"] != expected_revision:
            raise ValueError("Revision changed; call get_graph first.")
        observed = scan_project(store.root, include_symbols, max_files)
        graph = merge_mapping(current["graph"], observed)
        updated = store.save(graph, expected_revision)
        return {"revision": updated["revision"], "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                "diagnostics": graph["project"]["diagnostics"], "limitations": graph["project"]["analysisLimitations"], "editor_url": bridge.url}

    @mcp.tool(annotations=read_only, structured_output=True)
    @checked
    def get_graph(include_baseline: bool = False) -> dict[str, Any]:
        """Read the current shared graph/revision. File references, evidence, intended responsibilities, and workflow notes are portable. Request baseline only when needed."""
        state = store.read()
        if not include_baseline:
            state["graph"].pop("codeBaseline", None)
        return state

    @mcp.tool(annotations=graph_write, structured_output=True)
    @checked
    def set_graph(graph: dict, expected_revision: int) -> dict[str, Any]:
        """Replace the visible diagram with a valid version 1 graph. Read the latest revision first. Omitted project/baseline context is retained. Writes graph state only."""
        current = store.read()["graph"]
        candidate = copy.deepcopy(graph)
        for key in ("project", "codeBaseline"):
            if key not in candidate and key in current:
                candidate[key] = current[key]
        return store.save(candidate, expected_revision)

    @mcp.tool(annotations=graph_write, structured_output=True)
    @checked
    def edit_graph(operations: list[dict], expected_revision: int) -> dict[str, Any]:
        """Apply up to 400 operations: upsert_node {node}, remove_node {id}, upsert_edge {edge}, remove_edge {id}. Partial upserts retain metadata; new nodes need id. Use file/line evidence for code claims and intent=planned for proposed architecture."""
        if len(operations) > 400:
            raise ValueError("Use at most 400 operations per call.")
        graph = store.read()["graph"]
        for operation in operations:
            action = operation.get("op")
            if action in ("upsert_node", "upsert_edge"):
                kind, payload = ("nodes", "node") if action == "upsert_node" else ("edges", "edge")
                item = operation[payload]
                existing = next((n for n in graph[kind] if n["id"] == item.get("id")), None)
                if existing:
                    existing.update(item)
                elif kind == "nodes":
                    index = len(graph["nodes"])
                    graph[kind].append({"type": "component", "title": "Planned component", "description": "", "x": (index % 4) * 320,
                                        "y": (index // 4) * 220, "intent": "planned", **item})
                else:
                    graph[kind].append({"label": "", "style": "solid", "direction": "forward", "fromPort": "right", "toPort": "left",
                                        "intent": "planned", **item})
            elif action in ("remove_node", "remove_edge"):
                nid = operation["id"]
                if action == "remove_node":
                    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != nid]
                    graph["edges"] = [e for e in graph["edges"] if e["from"] != nid and e["to"] != nid]
                else:
                    graph["edges"] = [e for e in graph["edges"] if e["id"] != nid]
            else:
                raise ValueError("Unknown graph operation.")
        return store.save(graph, expected_revision)

    @mcp.tool(annotations=graph_write, structured_output=True)
    @checked
    def set_workflow(goal: str, decisions: str, review_notes: str, expected_revision: int) -> dict[str, Any]:
        """Record the intended project goal, architecture decisions, and a second opinion. These become the portable implementation/review context."""
        graph = store.read()["graph"]
        graph["workflow"] = {"goal": goal, "decisions": decisions, "reviewNotes": review_notes}
        return store.save(graph, expected_revision)

    @mcp.tool(annotations=read_only, structured_output=True)
    @checked
    def get_implementation_plan() -> dict[str, Any]:
        """Compare intended graph with code baseline, ignoring positions/routes. Detect changed source fingerprints. Use normal coding tools for a reviewed patch; inspect files and run tests before acknowledging."""
        state = store.read()
        graph = state["graph"]
        return {"revision": state["revision"], "changes": changes(graph), "sourceChanges": changed_source_files(store.root, graph),
                "markdown": implementation_brief(graph), "nextSteps": ["Read referenced files and review mapping limits.",
                "Explain the affected-file patch plan for the semantic changes.", "Implement with the coding agent's normal file-editing tools.",
                "Run relevant checks and inspect the resulting code.", "Call acknowledge_implementation with the verification summary."]}

    @mcp.tool(annotations=graph_write, structured_output=True)
    @checked
    def acknowledge_implementation(verification_summary: str, expected_revision: int) -> dict[str, Any]:
        """After implementing and verifying graph changes, record the agent's report, current file fingerprints, and a new accepted design baseline. This records an attestation, not independent proof that tests ran."""
        if not verification_summary.strip():
            raise ValueError("Include what was changed and how it was verified.")
        state = store.read()
        graph = state["graph"]
        listed = inventory(store.root, 1000)
        for node in graph["nodes"]:
            if node.get("file") and node["file"] not in {f["path"] for f in listed["files"]}:
                raise ValueError(f"Referenced file is missing or excluded: {node['file']}. Resolve it before acknowledging.")
        project = graph.setdefault("project", {"name": store.root.name, "language": "mixed", "strategy": "agent-reviewed", "analysisLimitations": [], "diagnostics": []})
        project.update({"fileHashes": {f["path"]: f["sha256"] for f in listed["files"]}, "mappedAt": timestamp(),
                        "implementationNote": verification_summary, "diagnostics": listed["diagnostics"]})
        graph["codeBaseline"] = {key: copy.deepcopy(graph[key]) for key in ("version", "name", "nodes", "edges", "workflow") if key in graph}
        return store.save(graph, expected_revision)

    @mcp.tool(annotations=read_only, structured_output=True)
    @checked
    def get_review_pack() -> dict[str, Any]:
        """Return a portable review pack for another agent: graph, code baseline, relative file references, fingerprints, intentions, and critique notes. Does not bundle full source files or automatically record the absolute project root."""
        return store.handoff()

    @mcp.tool(annotations=graph_write, structured_output=True)
    @checked
    def import_review_pack(bundle: dict, expected_revision: int) -> dict[str, Any]:
        """Import a portable review pack into the currently connected project. Validate fingerprints against this project's files before implementing; imported evidence must be reviewed."""
        state = store.import_handoff(bundle, expected_revision)
        return {**state, "sourceChanges": changed_source_files(store.root, state["graph"])}

    @mcp.resource("graphitout://graph")
    def graph_resource() -> str:
        """The shared architecture diagram and revision."""
        return json.dumps(store.read(), ensure_ascii=False)

    @mcp.prompt()
    def review_architecture() -> str:
        """Ask any agent for a source-grounded second opinion."""
        return "Call get_review_pack and read_project_file to review the architecture. " + HANDOFF_INSTRUCTIONS

    @mcp.prompt()
    def implement_workflow() -> str:
        """Translate the user's intended graph into reviewed code changes."""
        return "Call get_implementation_plan, inspect sourceChanges, and explain the proposed patch. " + HANDOFF_INSTRUCTIONS

    return mcp
