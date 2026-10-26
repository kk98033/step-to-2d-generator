#!/usr/bin/env python3
"""Generate a deterministic API traceability graph from the current source tree.

The generated files are intentionally descriptive, not an API contract.  The
manual API reference remains the place for request/response semantics.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BACKEND_FILES = (
    ROOT / "web_app/backend/server.py",
    ROOT / "web_app/backend/account_api.py",
)
FRONTEND_ROOT = ROOT / "web_app/frontend/src"
WORKER_FILE = ROOT / "tools/cad_annotation_worker.py"
OUTPUT_DIR = ROOT / "docs/generated"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass(frozen=True)
class SourceRef:
    file: str
    line: int
    symbol: str


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def normalize_path(value: str) -> str:
    value = value.replace("${API_BASE}", "")
    value = re.sub(r"\$\{[^}]+\}", "{param}", value)
    value = value.split("?", 1)[0]
    value = re.sub(r"//+", "/", value.strip())
    return value if value.startswith("/") else f"/{value}"


def route_key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def paths_match(left: str, right: str) -> bool:
    a = left.strip("/").split("/")
    b = right.strip("/").split("/")
    if len(a) != len(b):
        return False
    return all(x == y or (x.startswith("{") and y.startswith("{")) for x, y in zip(a, b))


def node_id(kind: str, label: str) -> str:
    digest = hashlib.sha1(f"{kind}:{label}".encode("utf-8")).hexdigest()[:10]
    return f"{kind}_{digest}"


def literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def called_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def module_functions(statements: list[ast.stmt]) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return module-scope functions, including those guarded by if/try blocks."""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for item in statements:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(item)
        elif isinstance(item, ast.If):
            found.extend(module_functions(item.body))
            found.extend(module_functions(item.orelse))
        elif isinstance(item, ast.Try):
            found.extend(module_functions(item.body))
            found.extend(module_functions(item.orelse))
            found.extend(module_functions(item.finalbody))
            for handler in item.handlers:
                found.extend(module_functions(handler.body))
        elif isinstance(item, (ast.With, ast.AsyncWith)):
            found.extend(module_functions(item.body))
    return found


def scan_backend() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    routes: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for path in BACKEND_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = path.stem
        prefix = "/api" if path.name == "account_api.py" else ""
        functions = module_functions(tree.body)
        local_functions = {item.name for item in functions}
        for function in functions:
            source = SourceRef(rel(path), function.lineno, function.name)
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.lower()
                owner = called_name(decorator.func.value)
                endpoint = literal_string(decorator.args[0]) if decorator.args else None
                if method not in HTTP_METHODS or owner not in {"app", "router"} or endpoint is None:
                    continue
                full_path = normalize_path(prefix + endpoint)
                routes.append(
                    {
                        "id": node_id("api", route_key(method, full_path)),
                        "method": method.upper(),
                        "path": full_path,
                        "handler": function.name,
                        "source": source.__dict__,
                    }
                )
            direct_calls = sorted(
                {
                    name
                    for item in ast.walk(function)
                    if isinstance(item, ast.Call)
                    for name in [called_name(item.func)]
                    if name in local_functions and name != function.name
                }
            )
            for item in ast.walk(function):
                if (
                    isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Attribute)
                    and item.func.attr == "submit"
                    and item.args
                    and isinstance(item.args[0], ast.Name)
                    and item.args[0].id in local_functions
                ):
                    direct_calls.append(item.args[0].id)
            direct_calls = sorted(set(direct_calls))
            for target in direct_calls:
                calls.append(
                    {
                        "from": f"backend:{module}:{function.name}",
                        "to": f"backend:{module}:{target}",
                        "type": "calls",
                    }
                )
    return sorted(routes, key=lambda item: (item["path"], item["method"])), sorted(
        calls, key=lambda item: (item["from"], item["to"])
    )


SYMBOL_RE = re.compile(
    r"(?:function\s+(?P<function>[A-Za-z_$][\w$]*)\s*\(|"
    r"(?:const|let)\s+(?P<const>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)"
)
AXIOS_RE = re.compile(
    r"axios\.(?P<method>get|post|put|patch|delete)\s*\(\s*(?P<quote>[`'\"])(?P<url>.*?)(?P=quote)",
    re.DOTALL,
)


def nearest_symbol(text: str, position: int, fallback: str) -> str:
    matches = list(SYMBOL_RE.finditer(text, 0, position))
    if not matches:
        return fallback
    match = matches[-1]
    return match.group("function") or match.group("const") or fallback


def scan_frontend() -> list[dict[str, Any]]:
    usages: list[dict[str, Any]] = []
    for path in sorted([*FRONTEND_ROOT.rglob("*.ts"), *FRONTEND_ROOT.rglob("*.tsx")]):
        text = path.read_text(encoding="utf-8")
        for match in AXIOS_RE.finditer(text):
            raw_url = match.group("url")
            if "/api/" not in raw_url:
                continue
            line = text.count("\n", 0, match.start()) + 1
            symbol = nearest_symbol(text, match.start(), path.stem)
            usages.append(
                {
                    "id": node_id("frontend", f"{rel(path)}:{symbol}"),
                    "method": match.group("method").upper(),
                    "path": normalize_path(raw_url),
                    "raw_url": raw_url,
                    "source": SourceRef(rel(path), line, symbol).__dict__,
                }
            )
    return sorted(usages, key=lambda item: (item["source"]["file"], item["source"]["line"]))


def string_options(node: ast.AST | None, assignments: dict[str, set[str]]) -> set[str]:
    value = literal_string(node)
    if value is not None:
        return {value}
    if isinstance(node, ast.Name):
        return assignments.get(node.id, set())
    if isinstance(node, ast.IfExp):
        return string_options(node.body, assignments) | string_options(node.orelse, assignments)
    return set()


def dict_action_values(function: ast.AST) -> set[str]:
    assignments: dict[str, set[str]] = {}
    for item in ast.walk(function):
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            value = item.value
            options = string_options(value, assignments)
            for target in targets:
                if isinstance(target, ast.Name) and options:
                    assignments.setdefault(target.id, set()).update(options)
    values: set[str] = set()
    for item in ast.walk(function):
        if not isinstance(item, ast.Dict):
            continue
        for key, value in zip(item.keys, item.values):
            if literal_string(key) == "action":
                values.update(string_options(value, assignments))
    return values


def scan_worker() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tree = ast.parse(WORKER_FILE.read_text(encoding="utf-8"), filename=str(WORKER_FILE))
    actions: dict[str, dict[str, Any]] = {
        "render": {
            "action": "render",
            "handler": "run_job",
            "source": SourceRef(rel(WORKER_FILE), 274, "run_job").__dict__,
        }
    }
    dispatches: list[dict[str, Any]] = []
    for function in (
        item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        for item in ast.walk(function):
            if not isinstance(item, ast.Compare) or not isinstance(item.left, ast.Name) or item.left.id != "action":
                continue
            for comparator in item.comparators:
                action = literal_string(comparator)
                if action and action != "render":
                    actions[action] = {
                        "action": action,
                        "handler": function.name,
                        "source": SourceRef(rel(WORKER_FILE), item.lineno, function.name).__dict__,
                    }
    for path in BACKEND_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in module_functions(tree.body):
            invokes_worker = any(
                isinstance(item, ast.Call) and called_name(item.func) == "_run_cad_annotation_worker"
                for item in ast.walk(function)
            )
            for action in sorted(dict_action_values(function)):
                dispatches.append(
                    {
                        "from": f"backend:{path.stem}:{function.name}",
                        "to": f"worker:{action}",
                        "type": "dispatches" if invokes_worker else "configures_worker_action",
                    }
                )
    return sorted(actions.values(), key=lambda item: item["action"]), sorted(
        dispatches, key=lambda item: (item["from"], item["to"])
    )


def api_category(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        return "system"
    return parts[1]


def build_graph() -> dict[str, Any]:
    routes, backend_calls = scan_backend()
    frontend = scan_frontend()
    worker_actions, worker_dispatches = scan_worker()
    edges: list[dict[str, Any]] = []
    for route in routes:
        handler_id = f"backend:{Path(route['source']['file']).stem}:{route['handler']}"
        edges.append({"from": route["id"], "to": handler_id, "type": "handled_by"})
    unresolved: list[dict[str, Any]] = []
    for usage in frontend:
        matches = [
            route
            for route in routes
            if route["method"] == usage["method"] and paths_match(route["path"], usage["path"])
        ]
        frontend_id = f"frontend:{usage['source']['file']}:{usage['source']['symbol']}"
        if not matches:
            unresolved.append(usage)
            continue
        for route in matches:
            edges.append({"from": frontend_id, "to": route["id"], "type": "calls_api"})
    edges.extend(backend_calls)
    edges.extend(worker_dispatches)
    unique_edges = [dict(items) for items in sorted({tuple(sorted(edge.items())) for edge in edges})]
    return {
        "schema_version": 1,
        "generator": rel(Path(__file__).resolve()),
        "summary": {
            "backend_routes": len(routes),
            "frontend_http_calls": len(frontend),
            "unresolved_frontend_calls": len(unresolved),
            "worker_actions": len(worker_actions),
        },
        "routes": routes,
        "frontend_calls": frontend,
        "worker_actions": worker_actions,
        "edges": unique_edges,
        "unresolved_frontend_calls": unresolved,
    }


def markdown_catalog(graph: dict[str, Any]) -> str:
    summary = graph["summary"]
    lines = [
        "# Generated API Catalog",
        "",
        "> 此檔案由 `python tools/generate_system_api_graph.py` 自動產生，請勿手動修改。",
        "> 它描述目前程式碼實況；請求與回應的對外契約仍以 `docs/api_reference.md` 為準。",
        "",
        "## Summary",
        "",
        f"- Backend routes: {summary['backend_routes']}",
        f"- Frontend HTTP calls: {summary['frontend_http_calls']}",
        f"- Unresolved frontend calls: {summary['unresolved_frontend_calls']}",
        f"- CAD Worker actions: {summary['worker_actions']}",
        "",
        "## Backend routes",
        "",
        "| Method | Path | Handler | Source |",
        "| --- | --- | --- | --- |",
    ]
    for route in graph["routes"]:
        source = route["source"]
        lines.append(
            f"| {route['method']} | `{route['path']}` | `{route['handler']}` | "
            f"`{source['file']}:{source['line']}` |"
        )
    lines.extend(["", "## CAD Worker actions", "", "| Action | Dispatcher | Source |", "| --- | --- | --- |"])
    for action in graph["worker_actions"]:
        source = action["source"]
        lines.append(
            f"| `{action['action']}` | `{action['handler']}` | `{source['file']}:{source['line']}` |"
        )
    if graph["unresolved_frontend_calls"]:
        lines.extend(
            [
                "",
                "## Unresolved frontend calls",
                "",
                "動態 URL 或尚未找到對應後端路由的呼叫；應在審查時確認。",
                "",
                "| Method | Path | Source |",
                "| --- | --- | --- |",
            ]
        )
        for call in graph["unresolved_frontend_calls"]:
            source = call["source"]
            lines.append(f"| {call['method']} | `{call['path']}` | `{source['file']}:{source['line']}` |")
    return "\n".join(lines) + "\n"


def mermaid_label(value: str) -> str:
    return value.replace('"', "'").replace("[", "(").replace("]", ")")


def relationship_markdown(graph: dict[str, Any]) -> str:
    routes_by_id = {route["id"]: route for route in graph["routes"]}
    frontend_edges = [edge for edge in graph["edges"] if edge["type"] == "calls_api"]
    calls_by_file: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    frontend_sources = {
        f"frontend:{call['source']['file']}:{call['source']['symbol']}": call["source"]
        for call in graph["frontend_calls"]
    }
    for edge in frontend_edges:
        source = frontend_sources.get(edge["from"])
        route = routes_by_id.get(edge["to"])
        if source and route:
            calls_by_file.setdefault(source["file"], []).append((edge["from"], route))
    lines = [
        "# Generated API Relationships",
        "",
        "> 由 `python tools/generate_system_api_graph.py` 自動產生。圖太大時，請使用 `--query` 查單一 API 或函數。",
        "",
        "## System overview",
        "",
        "```mermaid",
        "flowchart LR",
        "  UI[React UI] --> API[FastAPI routes]",
        "  API --> SERVICE[Backend services]",
        "  SERVICE --> WORKER[Isolated CAD Worker]",
        "  WORKER --> ENGINE[Legacy / Experimental engines]",
        "```",
    ]
    for file, pairs in sorted(calls_by_file.items()):
        lines.extend(["", f"## `{file}`", "", "```mermaid", "flowchart LR"])
        seen: set[tuple[str, str]] = set()
        for frontend_id, route in sorted(pairs, key=lambda pair: (pair[1]["path"], pair[1]["method"])):
            source = frontend_sources[frontend_id]
            ui_id = node_id("ui", frontend_id)
            api_id = node_id("route", route_key(route["method"], route["path"]))
            handler_id = node_id("handler", f"{route['source']['file']}:{route['handler']}")
            edge_key = (ui_id, api_id)
            if edge_key not in seen:
                lines.append(f'  {ui_id}["{mermaid_label(source["symbol"])}"] --> {api_id}["{route["method"]} {mermaid_label(route["path"])}"]')
                seen.add(edge_key)
            lines.append(f'  {api_id} --> {handler_id}["{mermaid_label(route["handler"])}"]')
        lines.append("```")
    lines.extend(["", "## Worker dispatch", "", "```mermaid", "flowchart LR"])
    for edge in graph["edges"]:
        if edge["type"] not in {"dispatches", "configures_worker_action"}:
            continue
        source_id = node_id("backend", edge["from"])
        worker_id = node_id("worker", edge["to"])
        lines.append(f'  {source_id}["{mermaid_label(edge["from"])}"] --> {worker_id}["{mermaid_label(edge["to"])}"]')
    lines.extend(["```", ""])
    return "\n".join(lines)


def outputs(graph: dict[str, Any]) -> dict[Path, str]:
    return {
        OUTPUT_DIR / "api_graph.json": json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        OUTPUT_DIR / "api_catalog.md": markdown_catalog(graph),
        OUTPUT_DIR / "api_relationships.md": relationship_markdown(graph),
    }


def query_graph(graph: dict[str, Any], query: str) -> int:
    needle = query.lower()
    route_ids = {
        route["id"]
        for route in graph["routes"]
        if needle in route["path"].lower() or needle in route["handler"].lower()
    }
    matches: list[str] = []
    for route in graph["routes"]:
        if route["id"] in route_ids:
            source = route["source"]
            matches.append(
                f"ROUTE {route['method']} {route['path']} -> {route['handler']} ({source['file']}:{source['line']})"
            )
    seeds = set(route_ids)
    for edge in graph["edges"]:
        if needle in edge["from"].lower():
            seeds.add(edge["from"])
        if needle in edge["to"].lower():
            seeds.add(edge["to"])

    selected_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def select(edge: dict[str, Any]) -> None:
        key = (edge["from"], edge["type"], edge["to"])
        if key not in seen_edges:
            seen_edges.add(key)
            selected_edges.append(edge)

    # Include UI callers and other direct inbound context without recursively
    # walking backwards through shared authentication/storage helpers.
    for edge in graph["edges"]:
        if edge["to"] in seeds or needle in edge["from"].lower() or needle in edge["to"].lower():
            select(edge)

    frontier = set(seeds)
    visited = set(seeds)
    for _depth in range(5):
        next_frontier: set[str] = set()
        for edge in graph["edges"]:
            if edge["from"] not in frontier:
                continue
            select(edge)
            if edge["to"] not in visited:
                visited.add(edge["to"])
                next_frontier.add(edge["to"])
        frontier = next_frontier
        if not frontier:
            break
    for edge in selected_edges:
        matches.append(f"EDGE {edge['from']} -[{edge['type']}]-> {edge['to']}")
    if not matches:
        print(f"No relationship found for: {query}")
        return 1
    for line in matches[:40]:
        print(line)
    if len(matches) > 40:
        print(f"... {len(matches) - 40} more relationships omitted")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files differ")
    parser.add_argument("--query", help="print a bounded impact view for an API path or symbol")
    args = parser.parse_args(argv)
    graph = build_graph()
    if args.query:
        return query_graph(graph, args.query)
    generated = outputs(graph)
    if args.check:
        stale = [rel(path) for path, content in generated.items() if not path.exists() or path.read_text(encoding="utf-8") != content]
        if stale:
            print("Generated API graph is stale:")
            for path in stale:
                print(f"- {path}")
            return 1
        print("Generated API graph is current.")
        return 0
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in generated.items():
        path.write_text(content, encoding="utf-8", newline="\n")
    summary = graph["summary"]
    print(
        "Generated API graph: "
        f"{summary['backend_routes']} routes, {summary['frontend_http_calls']} frontend calls, "
        f"{summary['worker_actions']} worker actions, {summary['unresolved_frontend_calls']} unresolved."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
