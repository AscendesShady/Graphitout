# Graphitout with coding agents

Graphitout has two independent modes:

- **Browser sketching:** open `index.html` or the Pages site. Make initial ideas, algorithms, and architecture diagrams. No Python, account, server, or connection is needed.
- **Project-connected mode:** run the optional local companion for any project folder. An MCP-capable coding agent can inspect source and draw/edit the shared graph. Open the local editor link to see and change that same graph.

The HTML editor remains a single standalone file. The optional server is a separate Python process. GitHub Pages serves the standalone editor; it does not run the local Python server or access your folders.

## Install the optional companion once

Use Python **3.11 or newer**. In the Graphitout repository:

```sh
python -m venv .venv
```

Windows / PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

macOS / Linux:

```sh
.venv/bin/python -m pip install -r requirements-mcp.txt
```

The server uses the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Installation affects the Graphitout virtual environment, not your app's Python dependencies.

## Point it at any folder

For a local editor without an agent connection:

```powershell
.\.venv\Scripts\python.exe .\graphitout_mcp.py --project "D:\Projects\MyApp" --editor-only --map --open
```

This opens a project-connected editor, maps available source, and keeps it running until you stop the process. Omit `--map` to start with an existing graph or a blank project canvas. Omit `--open` to receive the editor URL in the terminal. `--port 0` chooses an available loopback port; a fixed port can be supplied if you need one.

For an agent connection, configure the same script over **stdio** as shown below. The MCP client owns the server's lifetime. No API key or cloud service is required. Do not use `--editor-only` in an MCP configuration.

The project defaults to `--project`, then `GRAPHITOUT_PROJECT`, then `CLAUDE_PROJECT_DIR`, then the working directory. Nothing is hardcoded to a particular app or project language.

## Claude Code

Register the companion using the [Claude Code stdio configuration](https://code.claude.com/docs/en/mcp#option-3-add-a-local-stdio-server). Replace the Python and script paths with your Graphitout installation:

```powershell
claude mcp add --scope user --transport stdio graphitout -- "C:\Tools\Graphitout\.venv\Scripts\python.exe" "C:\Tools\Graphitout\graphitout_mcp.py"
```

With current Claude Code, its project environment selects the folder. To connect a particular project explicitly, append `--project "D:\Projects\MyApp"` after the script path. For macOS/Linux, use `.venv/bin/python` and your absolute script path.

Open Claude Code in the intended app folder and check `/mcp`. Each client handles server trust and tool permissions through its own interface.

Example prompt:

> Use Graphitout to map this project. First get the current revision, then call map_project. Read relevant files to refine responsibilities and relationships. Keep source claims linked to files and lines; identify uncertain relationships. Give me the editor link.

## VS Code

Use the [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration). Merge this server entry into `.vscode/mcp.json` in whichever project you want to map:

```json
{
  "servers": {
    "graphitout": {
      "type": "stdio",
      "command": "C:/Tools/Graphitout/.venv/Scripts/python.exe",
      "args": [
        "C:/Tools/Graphitout/graphitout_mcp.py",
        "--project",
        "${workspaceFolder}"
      ]
    }
  }
}
```

Replace the installation paths. The workspace variable selects the current project. Use Agent mode with an MCP-capable coding assistant, start/trust the server through VS Code's MCP controls, and give the same mapping prompt. Other VS Code extensions may have their own MCP settings; use their stdio configuration with the same command and arguments.

For a Claude project configuration, the outer key is `mcpServers` rather than VS Code's `servers`. Configuration examples are under `examples/`; they are templates and do not modify your editor settings automatically.

## Code → graph

1. `get_project_info` returns the connected folder, revision, and authenticated editor URL.
2. `list_project_files` inventories common source, configuration, and documentation files.
3. `map_project(expected_revision=…, include_symbols=true)` establishes an observed source baseline and updates the graph.
4. `read_project_file` returns numbered text and its SHA-256 fingerprint. The agent uses these to investigate deeper relationships.
5. `edit_graph` or `set_graph` adds clearer responsibilities, architectural groupings, and relationships with evidence.

File/symbol IDs stay stable across scans. Refresh mapping retains manual sketches, positions, proposed semantic edits, and intentionally removed graph items. The browser displays updates from the agent automatically while you are not editing a field.

Automatic mapping is intentionally bounded:

- **Any supported text project:** file nodes with relative paths and fingerprints.
- **Python:** AST-based module imports, class/function nodes, containment, and resolvable local/imported call sites. Source is parsed as text and is never imported or executed.
- **JavaScript/TypeScript:** literal relative imports/requires, with conservative text analysis. No receiver typing or package/alias resolution.
- **Other languages:** inventory and agent-assisted analysis; the agent can add file/line evidence using `edit_graph`.

Dynamic dispatch, runtime behavior, and data flow need agent review or separate execution evidence. The server reports parse failures and omissions. Mapping defaults to 300 files; inventory accepts up to 1,000. A graph supports 1,000 blocks and 4,000 connections. Large files above 1 MB and dependency/build/hidden folders are skipped. Use `include_symbols=false` or a narrower project folder for large repositories.

## Graph → code

In either editor, edit responsibilities, connections, file references, or **Workflow & agent review** notes. In connected mode, changes are saved in the project, where the coding agent can read them.

Moving blocks or choosing connector ports is layout only. Changes to responsibilities, graph structure, communication labels/directions, and workflow notes express proposed design requirements.

Then tell your agent:

> Read Graphitout's implementation plan. Check changed source fingerprints and inspect the affected files. Explain the proposed patch for my design changes, implement it with your normal coding tools, run relevant checks, and acknowledge the implementation with what was verified.

`get_implementation_plan` compares the graph against its code/accepted-design baseline and reports source files whose fingerprints changed. The agent makes actual source edits through its own coding tools. The MCP server writes graph state, not arbitrary source patches, and the browser does not initiate an unattended agent run.

After implementation, `acknowledge_implementation` records the agent's verification report, updated file fingerprints, and a new accepted design baseline. This records an agent attestation; it is not independent proof that the software or tests ran. Re-map when you want a fresh static source view.

## Take the graph to another agent

Export **Review pack · JSON** or call `get_review_pack`. The pack contains the diagram, code baseline, relative file/symbol references, source evidence/fingerprints, design decisions, and review notes. It includes source summaries and references, but does not bundle full source files or automatically record the absolute project root, live server session, or agent credentials.

Give the pack to another coding agent together with access to the same project or its copy. Ask it to inspect the references, check fingerprints, critique the design, and record alternatives in `workflow.reviewNotes`. It can use `import_review_pack` in its connected project, or you can import the pack into the standalone browser.

**Implementation brief · Markdown** is readable review context. **Portable HTML** carries an independent copy of the editor and diagram, including its design context, and disconnects from the local bridge. Ordinary JSON exports retain optional project/workflow metadata too. Use the updated editor to preserve these fields.

## Project state and concurrent agents

State is stored in `<project>/.graphitout/workspace.json`, with a revision and a code baseline. Add `.graphitout/` to that project's `.gitignore` if you want working graph state local; commit selected review packs separately if you want them versioned.

Graph writes require the revision read by the client. Cross-process locking prevents two agent servers from saving the same revision simultaneously. If another agent changes the graph while you have unsaved browser edits, Graphitout retains your proposal in browser storage when available and shows a conflict. Export it to reconcile with the agent, or explicitly load the shared version.

The bridge binds to `127.0.0.1`, uses a per-process session link and HttpOnly cookie, validates Host/Origin, and exposes only graph APIs. Use the local link returned by the server; do not try to connect the public Pages tab directly to a private project.

## Verification

Browser-only regressions remain under `npm test`. Optional MCP checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s qa -p test_mcp.py -v
npm run test:mcp-browser
```

For macOS/Linux, use `.venv/bin/python`. `GRAPHITOUT_PYTHON` can select a different interpreter for the MCP browser checks; otherwise they use the repository's `.venv`. The protocol tests exercise real stdio clients, including legacy initialization. Browser checks exercise source mapping, edits, live updates, review exports, independent portable copies, and conflict recovery in disposable project folders. A real Claude Code or VS Code session still needs your chosen project configuration and its client trust step.
