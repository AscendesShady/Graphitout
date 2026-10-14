"""Launch the optional Graphitout MCP companion for ANY project folder."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import webbrowser
from pathlib import Path

from graphitout.bridge import EditorBridge
from graphitout.model import GraphStore
from graphitout.scan import merge_mapping, scan_project


def main():
    parser = argparse.ArgumentParser(description="Graphitout project-independent MCP server and optional local editor.")
    parser.add_argument("--project", default=os.environ.get("GRAPHITOUT_PROJECT") or os.environ.get("CLAUDE_PROJECT_DIR") or ".", help="Any project folder; defaults to the agent's project/cwd.")
    parser.add_argument("--port", type=int, default=0, help="Loopback editor port; 0 chooses an available port.")
    parser.add_argument("--editor-only", action="store_true", help="Start the local project-connected browser editor without an MCP client.")
    parser.add_argument("--open", action="store_true", help="Open the local editor in your default browser.")
    parser.add_argument("--map", action="store_true", help="Map source immediately, preserving existing sketches and design changes.")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535.")
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    store = GraphStore(Path(args.project))
    if args.map:
        state = store.read()
        store.save(merge_mapping(state["graph"], scan_project(store.root)), state["revision"])
    bridge = EditorBridge(store, Path(__file__).with_name("index.html"), args.port)
    bridge.start()
    try:
        if args.open:
            webbrowser.open(bridge.url)
        if args.editor_only:
            print(json.dumps({"editor_url": bridge.url, "projectFolder": str(store.root)}), flush=True)
            bridge.thread.join()
        else:
            try:
                from graphitout.server import create_server
            except ModuleNotFoundError as error:
                if error.name == "mcp":
                    raise SystemExit("Install the optional MCP dependency: python -m pip install -r requirements-mcp.txt") from error
                raise
            create_server(store, bridge).run(transport="stdio")
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
