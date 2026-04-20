# function_list.py
import os
import ast
import json
from pathlib import Path
from collections import defaultdict
from .function_info import FunctionInfo
from .ast_function_visitor import FunctionCollector


def module_name_from_path(root_dir: str, file_path: str) -> str:
    rel = os.path.relpath(file_path, root_dir)
    no_ext = os.path.splitext(rel)[0]
    parts = no_ext.split(os.sep)
    return ".".join(parts)

def collect_repo_symbols(root_dir: str) -> tuple[dict[str, FunctionInfo], dict[str, dict]]:
    """
    Returns:
      all_functions: qualname -> FunctionInfo
      all_classes: class_qualname -> class metadata dict
    """
    all_functions: dict[str, FunctionInfo] = {}
    all_classes: dict[str, dict] = {}

    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if not fname.endswith(".py"):
                continue

            fullpath = os.path.join(dirpath, fname)
            try:
                with open(fullpath, "r", encoding="utf-8") as f:
                    src = f.read()
            except (UnicodeDecodeError, OSError):
                continue

            try:
                tree = ast.parse(src, filename=fullpath)
            except SyntaxError:
                continue

            modname = module_name_from_path(root_dir, fullpath)
            collector = FunctionCollector(modname, fullpath)
            collector.visit(tree)

            all_functions.update(collector.functions)

            # merge classes (and methods lists)
            for cq, meta in collector.classes.items():
                if cq not in all_classes:
                    all_classes[cq] = meta
                else:
                    # merge methods
                    existing = all_classes[cq].setdefault("methods", [])
                    for m in meta.get("methods", []):
                        if m not in existing:
                            existing.append(m)

                    # keep earliest lineno if it was missing
                    if not all_classes[cq].get("lineno") and meta.get("lineno"):
                        all_classes[cq]["lineno"] = meta["lineno"]

    return all_functions, all_classes

def build_dependency_graph(all_functions: dict[str, FunctionInfo]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)

    name_index: dict[str, list[str]] = defaultdict(list)
    module_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for qname, finfo in all_functions.items():
        name_index[finfo.name].append(qname)
        module_index[(finfo.module, finfo.name)].append(qname)

    for caller_qname, finfo in all_functions.items():
        for call in finfo.calls:
            if call in all_functions:
                graph[caller_qname].add(call)
                continue

            short = call.split(".")[-1]
            same_module_matches = module_index.get((finfo.module, short), [])
            if len(same_module_matches) == 1:
                graph[caller_qname].add(same_module_matches[0])
                continue

            global_matches = name_index.get(short, [])
            if len(global_matches) == 1:
                graph[caller_qname].add(global_matches[0])
        graph.setdefault(caller_qname, set())

    return graph

def is_public_function(finfo: FunctionInfo) -> bool:
    # Allow single underscore functions (private functions used in tests)
    # Only exclude double underscore (magic methods)
    if finfo.name.startswith("__"):
        return False
    return True


def generate_function_list(path: Path) -> dict[str, dict]:
    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    root = os.path.abspath(PROJECT_ROOT / path)
    print(root)
    public_only = True

    all_funcs, all_classes = collect_repo_symbols(root)
    funcs = {q: f for q, f in all_funcs.items() if is_public_function(f)} if public_only else all_funcs
    dep_graph = build_dependency_graph(all_funcs)

    out = {
        "functions": {
            q: {
                "params": f.params,
                "filename": f.filename,
                "lineno": f.lineno,
                "calls": sorted(dep_graph.get(q, set())),
                "is_class_method": f.is_class_method,
                "class_qualname": f.class_qualname,
                "class": f.cls,
            }
            for q, f in funcs.items()
            if not f.is_nested   # ✅ exclude nested defs from final output
        },
        "classes": all_classes,
    }

    dump_function_list(out)

    return out

def dump_function_list(f_list: dict[str, dict]) -> None:    
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(curr_dir, "function_list.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(f_list, f, indent=2)
    print("Saved to:", output_path)
