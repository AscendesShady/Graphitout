"""Deterministic static mapping. Reads source as text; never imports or executes it."""
from __future__ import annotations

import ast
import copy
import hashlib
import os
import re
import tokenize
from pathlib import Path
from typing import Any

from .model import empty_graph, relative_path, semantic, timestamp, validate_graph

IGNORED = {".git", ".hg", ".svn", ".graphitout", ".venv", "venv", "env", "node_modules", "__pycache__",
           "dist", "build", "coverage", ".next", ".nuxt", ".cache", "vendor", "target", "bin", "obj", ".idea"}
EXTENSIONS = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".java", ".cs", ".go", ".rs",
              ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".kts", ".scala", ".r", ".jl",
              ".html", ".css", ".scss", ".less", ".sql", ".sh", ".ps1", ".bat", ".lua", ".dart", ".ex", ".exs", ".fs", ".vb",
              ".json", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".md", ".txt", ".ipynb"}
NAMED_FILES = {"Dockerfile", "Makefile", "CMakeLists.txt", "LICENSE", "Procfile"}
JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".json")
MAX_SOURCE_BYTES = 1024 * 1024
BINARY_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.gz', '.7z', '.rar', '.exe', '.dll', '.so', '.dylib', '.pyc', '.woff', '.woff2', '.ttf', '.mp4', '.mp3', '.wav', '.sqlite', '.db'}
PRIVATE_EXTENSIONS = {'.pem', '.key', '.p12', '.pfx', '.keystore'}
LIMITS = [
    "Static syntax is evidence of a dependency or call site, not proof of runtime execution or data flow.",
    "Python imports and resolvable local/imported calls use AST analysis; dynamic imports, runtime dispatch, and receiver types are not inferred.",
    "JavaScript/TypeScript mapping recognizes literal relative imports/requires; aliases and package dependencies need agent review.",
    "Other languages and configuration/docs are inventoried as files. An agent can read them and add evidence-backed relationships.",
    "Generated/dependency folders and symbolic links are skipped. Files above 1 MB, parse errors, and limits are reported.",
]


def error_note(error: Exception, root: Path) -> str:
    message = str(error)
    for prefix in (str(root), root.as_posix()):
        message = message.replace(prefix, '<project>')
    return message[:500]


def source_path(root: Path, file: str) -> Path:
    file = relative_path(file)
    if any(part in IGNORED or part.startswith(".") for part in Path(file).parts):
        raise ValueError("Hidden, state, and dependency files are excluded from source tools.")
    path = (root / file).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Source path must stay inside the connected project.")
    if path.suffix.lower() in BINARY_EXTENSIONS | PRIVATE_EXTENSIONS:
        raise ValueError("Binary and private-key files are excluded from text source tools.")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source files above 1 MB are excluded.")
    return path


def read_source(root: Path, file: str) -> tuple[str, str]:
    path = source_path(root, file)
    data = path.read_bytes()
    if len(data) > MAX_SOURCE_BYTES or b"\x00" in data:
        raise ValueError("Source must be a text file below 1 MB.")
    if path.suffix.lower() in {".py", ".pyi"}:
        with tokenize.open(path) as handle:
            text = handle.read()
    else:
        text = data.decode("utf-8-sig")
    return text, hashlib.sha256(data).hexdigest()


def inventory(root: Path, max_files: int = 1000) -> dict:
    if type(max_files) is not int or not 1 <= max_files <= 1000:
        raise ValueError("max_files must be between 1 and 1,000.")
    files, diagnostics, inspected = [], [], 0
    for directory, subdirs, names in os.walk(root, followlinks=False):
        subdirs[:] = sorted(d for d in subdirs if d not in IGNORED and not d.startswith(".") and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if name.startswith(".") or path.is_symlink() or path.suffix.lower() in BINARY_EXTENSIONS | PRIVATE_EXTENSIONS:
                continue
            inspected += 1
            if inspected > max_files * 10:
                diagnostics.append("Inspection limit reached after many unreadable/oversized files; additional files were not inspected.")
                return {"files": files, "diagnostics": diagnostics}
            file = path.relative_to(root).as_posix()
            if len(files) >= max_files:
                diagnostics.append(f"Inventory stopped at {max_files} files; additional files were not mapped.")
                return {"files": files, "diagnostics": diagnostics}
            try:
                text, digest = read_source(root, file)
                files.append({"path": file, "bytes": path.stat().st_size, "sha256": digest,
                              "lines": len(text.splitlines()), "language": path.suffix.lower().lstrip(".") or "text"})
            except (OSError, UnicodeError, SyntaxError, ValueError) as error:
                diagnostics.append(f"{file}: {error_note(error, root)}")
    return {"files": files, "diagnostics": diagnostics}


def identifier(kind: str, value: str) -> str:
    return kind + "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def module_aliases(file: str, root_name: str) -> list[str]:
    parts = list(Path(file).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    aliases = [".".join(parts)]
    if parts and parts[0] == "src":
        aliases.append(".".join(parts[1:]))
    aliases += [root_name + ("." + alias if alias else "") for alias in list(aliases)]
    return list(dict.fromkeys(aliases))


def dotted(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = dotted(expr.value)
        return base + "." + expr.attr if base else None
    return None


class Definitions(ast.NodeVisitor):
    def __init__(self):
        self.scope: list[str] = []
        self.items: dict[str, ast.AST] = {}
        self.calls: list[tuple[str, ast.Call]] = []
        self.imports: list[tuple[str, ast.AST]] = []
        self.shadowed: dict[str, set[str]] = {}

    def _definition(self, node: ast.AST) -> None:
        self.scope.append(node.name)
        self.items[".".join(self.scope)] = node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = [arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            names += [arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg]
            self.shadowed.setdefault(".".join(self.scope), set()).update(names)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append((".".join(self.scope), node))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.shadowed.setdefault(".".join(self.scope), set()).add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append((".".join(self.scope), node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append((".".join(self.scope), node))


def scan_project(root: Path, include_symbols: bool = True, max_files: int = 300) -> dict:
    listed = inventory(root, max_files)
    files = listed["files"]
    graph = empty_graph(root.name)
    diagnostics = listed["diagnostics"][:]
    nodes = graph["nodes"]
    file_ids = {}
    symbol_ids = {}
    units: dict[str, dict] = {}
    module_map: dict[str, list[str]] = {}
    hashes = {}
    for i, record in enumerate(files):
        file = record["path"]
        nid = identifier("file", file)
        file_ids[file] = nid
        hashes[file] = record["sha256"]
        nodes.append({"id": nid, "type": "component", "title": Path(file).name[:160], "description": file,
                      "x": (i % 4) * 320, "y": (i // 4) * 220, "file": file, "intent": "observed",
                      "evidence": [{"file": file, "line": 1, "kind": "file-inventory"}]})
        if Path(file).suffix.lower() not in {".py", ".pyi"}:
            continue
        try:
            text, _ = read_source(root, file)
            tree = ast.parse(text, filename=file)
            defs = Definitions()
            defs.visit(tree)
            nodes[-1]["description"] = (ast.get_docstring(tree) or file)[:5000]
            aliases = module_aliases(file, root.name)
            for alias in aliases:
                if alias:
                    module_map.setdefault(alias, []).append(file)
            units[file] = {"tree": tree, "defs": defs, "aliases": aliases, "bindings": {}}
        except (SyntaxError, ValueError, OSError, UnicodeError) as error:
            diagnostics.append(f"{file}: Python analysis failed: {error_note(error, root)}")
    def resolve_module(name: str) -> str | None:
        matches = list(dict.fromkeys(module_map.get(name, [])))
        return matches[0] if len(matches) == 1 else None

    edge_index = {}
    def add_edge(source: str, target: str, relation: str, label: str, file: str, line: int) -> None:
        if source == target and relation == "imports":
            return
        key = source, target, relation
        evidence = {"file": file, "line": line, "kind": "python-ast" if file.endswith((".py", ".pyi")) else "literal-relative-import"}
        if key in edge_index:
            edge = edge_index[key]
            if len(edge["evidence"]) < 100 and evidence not in edge["evidence"]:
                edge["evidence"].append(evidence)
            return
        if len(graph["edges"]) >= 4000:
            if "Edge limit reached; additional relationships were omitted." not in diagnostics:
                diagnostics.append("Edge limit reached; additional relationships were omitted.")
            return
        edge = {"id": identifier("edge", "|".join(key)), "from": source, "to": target, "label": label[:160],
                "style": "solid", "direction": "forward", "fromPort": "left" if relation == "contains" else "right", "toPort": "left",
                "relation": relation, "intent": "observed", "evidence": [evidence]}
        graph["edges"].append(edge)
        edge_index[key] = edge

    if include_symbols:
        for file, unit in units.items():
            for symbol, definition in unit["defs"].items.items():
                if len(nodes) >= 1000:
                    if "Node limit reached; some Python symbols were omitted." not in diagnostics:
                        diagnostics.append("Node limit reached; some Python symbols were omitted.")
                    break
                nid = identifier("symbol", file + ":" + symbol)
                symbol_ids[file, symbol] = nid
                index = len(nodes)
                nodes.append({"id": nid, "type": "service" if isinstance(definition, ast.ClassDef) else "component",
                              "title": symbol[:160], "description": (ast.get_docstring(definition) or f"Defined in {file}:{definition.lineno}")[:5000],
                              "x": (index % 4) * 320, "y": (index // 4) * 220, "file": file, "symbol": symbol,
                              "intent": "observed", "evidence": [{"file": file, "line": definition.lineno, "kind": "python-ast"}]})
                add_edge(file_ids[file], nid, "contains", "defines", file, definition.lineno)
    unresolved_imports = 0
    for file, unit in units.items():
        aliases = unit["aliases"]
        module_name = next((a for a in aliases if not a.startswith(root.name + ".") and not a.startswith("src.")), aliases[0])
        package = module_name if Path(file).name.startswith("__init__.") else module_name.rpartition(".")[0]
        bindings: dict[tuple[str, str], tuple[str, str] | None] = unit["bindings"]
        def bind(scope: str, alias: str, target_file: str, target_symbol: str) -> None:
            key = scope, alias
            value = target_file, target_symbol
            bindings[key] = value if key not in bindings or bindings[key] == value else None
        for scope, imp in unit["defs"].imports:
            if isinstance(imp, ast.Import):
                for alias in imp.names:
                    target = resolve_module(alias.name)
                    if target:
                        add_edge(file_ids[file], file_ids[target], "imports", "imports " + alias.name, file, imp.lineno)
                        # Store full dotted prefix for imports without an alias; avoids guessing a package object's type.
                        bind(scope, alias.asname or alias.name, target, "")
                    else:
                        unresolved_imports += 1
            else:
                base = imp.module or ""
                if imp.level:
                    parts = package.split(".") if package else []
                    if imp.level > len(parts) + 1:
                        unresolved_imports += len(imp.names)
                        continue
                    parts = parts[:len(parts) - imp.level + 1]
                    base = ".".join(parts + ([base] if base else []))
                for alias in imp.names:
                    target = resolve_module(base + "." + alias.name if base else alias.name)
                    symbol = ""
                    if not target:
                        target = resolve_module(base)
                        symbol = alias.name
                    if target:
                        add_edge(file_ids[file], file_ids[target], "imports", "imports " + (base or alias.name), file, imp.lineno)
                        if alias.name != "*":
                            bind(scope, alias.asname or alias.name, target, symbol)
                    else:
                        unresolved_imports += 1
        def resolve_call(scope: str, expression: str) -> tuple[str, str] | None:
            if expression.startswith(("self.", "cls.")):
                owner = scope.rpartition(".")[0]
                candidate = owner + "." + expression.split(".", 1)[1]
                if candidate in unit["defs"].items:
                    return file, candidate
                return None
            scopes = [scope]
            while scopes[-1]:
                scopes.append(scopes[-1].rpartition(".")[0])
            for current in scopes:
                if expression.split(".")[0] in unit["defs"].shadowed.get(current, set()):
                    return None
                candidate = current + "." + expression if current else expression
                if candidate in unit["defs"].items:
                    return file, candidate
                for prefix_size in range(len(expression.split(".")), 0, -1):
                    parts = expression.split(".")
                    prefix = ".".join(parts[:prefix_size])
                    imported = bindings.get((current, prefix))
                    if imported:
                        target, symbol = imported
                        combined = ".".join(([symbol] if symbol else []) + parts[prefix_size:])
                        if combined in units.get(target, {}).get("defs", Definitions()).items:
                            return target, combined
            return None
        for scope, call in unit["defs"].calls:
            expression = dotted(call.func)
            target = resolve_call(scope, expression) if expression else None
            if target:
                target_file, target_symbol = target
                source_id = symbol_ids.get((file, scope), file_ids[file])
                target_id = symbol_ids.get((target_file, target_symbol), file_ids[target_file])
                if source_id != target_id or include_symbols:
                    add_edge(source_id, target_id, "calls", "calls " + target_symbol, file, call.lineno)
    # Conservative literal-import mapping for JS/TS. No package resolution or receiver inference.
    js_pattern = re.compile(r"(?:\b(?:import|export)\s+(?:[^;\n]*?\bfrom\s*)?|\brequire\s*\(\s*|\bimport\s*\(\s*)['\"](?P<path>\.[^'\"\n]+)['\"]")
    for file in file_ids:
        if Path(file).suffix.lower() not in JS_EXTENSIONS[:-1]:
            continue
        text, _ = read_source(root, file)
        # Preserve string contents and line numbers while removing actual comments.
        cleaned = re.sub(r"('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`)|(/\*[\s\S]*?\*/|//[^\n]*)",
                         lambda match: match[1] if match[1] else re.sub(r"[^\n]", " ", match[2]), text)
        for match in js_pattern.finditer(cleaned):
            strings = re.finditer(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`", cleaned)
            if any(value.start() <= match.start() < value.end() for value in strings):
                continue
            requested = match.group("path")
            normalized = os.path.normpath(str(Path(file).parent / requested)).replace("\\", "/")
            candidates = [normalized] + [normalized + ext for ext in JS_EXTENSIONS] + [normalized + "/index" + ext for ext in JS_EXTENSIONS]
            target = next((candidate for candidate in candidates if candidate in file_ids), None)
            if target:
                add_edge(file_ids[file], file_ids[target], "imports", "imports " + requested, file, cleaned[:match.start()].count("\n") + 1)
    # Group symbols beneath their own file rather than mixing every definition into one grid.
    node_by_id = {node['id']: node for node in nodes}
    cursor_y = 0
    file_names = list(file_ids)
    for row_start in range(0, len(file_names), 4):
        row = file_names[row_start:row_start + 4]
        tallest = 0
        for column, file in enumerate(row):
            parent = node_by_id[file_ids[file]]
            parent['x'], parent['y'] = column * 400, cursor_y
            children = [node for node in nodes if node.get('file') == file and node.get('symbol')]
            for index, child in enumerate(children):
                child['x'], child['y'] = column * 400, cursor_y + 220 + index * 180
            tallest = max(tallest, len(children))
        cursor_y += (tallest + 1) * 180 + 80
    if unresolved_imports:
        diagnostics.append(f"{unresolved_imports} Python import names were external, ambiguous, wildcard-only, or unresolved; no target was invented.")
    languages = sorted({item["language"] for item in files})
    graph["project"] = {"name": root.name[:100], "language": ", ".join(languages)[:80], "mappedAt": timestamp(),
                        "strategy": "file inventory + Python AST + literal JS/TS relative imports", "fileHashes": hashes,
                        "analysisLimitations": LIMITS[:], "diagnostics": diagnostics[:3000]}
    return validate_graph(graph)


def merge_mapping(current: dict, observed: dict) -> dict:
    """Refresh source evidence while retaining layout, intended edits, and intentional deletions."""
    result = copy.deepcopy(observed)
    baseline = current.get("codeBaseline", empty_graph(current["name"]))
    for kind in ("nodes", "edges"):
        old = {item["id"]: item for item in current[kind]}
        previous = {item["id"]: item for item in baseline[kind]}
        new = {item["id"]: item for item in observed[kind]}
        removed_by_user = previous.keys() - old.keys()
        combined = []
        for nid, item in new.items():
            if nid in removed_by_user:
                continue
            if nid in old:
                original = old[nid]
                if nid not in previous or semantic(original) != semantic(previous[nid]):
                    item = {**item, **copy.deepcopy(original)}
                else:
                    for key in ("x", "y", "fromPort", "toPort"):
                        if key in original:
                            item[key] = original[key]
            combined.append(item)
        for nid in old.keys() - new.keys():
            if nid not in previous or semantic(old[nid]) != semantic(previous[nid]):
                combined.append(copy.deepcopy(old[nid]))
        result[kind] = combined
    available = {node["id"] for node in result["nodes"]}
    result["edges"] = [edge for edge in result["edges"] if edge["from"] in available and edge["to"] in available]
    result["name"] = current["name"]
    if "workflow" in current:
        result["workflow"] = copy.deepcopy(current["workflow"])
    result["codeBaseline"] = {key: copy.deepcopy(observed[key]) for key in ("version", "name", "nodes", "edges")}
    if "workflow" in baseline:
        result["codeBaseline"]["workflow"] = copy.deepcopy(baseline["workflow"])
    return validate_graph(result)


def changed_source_files(root: Path, graph: dict) -> dict:
    modified, missing = [], []
    for file, digest in graph.get("project", {}).get("fileHashes", {}).items():
        try:
            _, now = read_source(root, file)
            if now != digest:
                modified.append(file)
        except (OSError, UnicodeError, ValueError, SyntaxError):
            missing.append(file)
    return {"modified": modified, "missing": missing,
            "note": "Compared only fingerprinted files. Re-map to discover added files and new source relationships."}
