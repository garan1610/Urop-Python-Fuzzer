# ast_function_visitor.py
from __future__ import annotations
import ast
from typing import Dict, Optional
from .function_info import FunctionInfo


class FunctionCollector(ast.NodeVisitor):
    def __init__(self, module_name: str, filename: str):
        self.module_name = module_name
        self.filename = filename

        self.current_class: Optional[str] = None
        self.current_function_qualname: Optional[str] = None

        # NEW: nesting depth for functions
        self.function_depth: int = 0

        self.functions: Dict[str, FunctionInfo] = {}
        self.classes: Dict[str, dict] = {}

        # NEW: for each recorded function, map nested short name -> local qualname
        # { "pkg.mod.outer": {"inner": "pkg.mod.outer.<locals>.inner"} }
        self.nested_defs: Dict[str, Dict[str, str]] = {}

    def _make_func_qualname(self, func_name: str) -> str:
        if self.current_class:
            return f"{self.module_name}.{self.current_class}.{func_name}"
        return f"{self.module_name}.{func_name}"

    def _make_class_qualname(self, class_name: str) -> str:
        return f"{self.module_name}.{class_name}"

    def _make_local_func_qualname(self, outer_qualname: str, inner_name: str) -> str:
        return f"{outer_qualname}.<locals>.{inner_name}"

    def _extract_call_name(self, node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            parts.reverse()
            return ".".join(parts)
        return None

    def _annotation_to_str(self, ann: ast.expr | None) -> str:
        if ann is None:
            return "Any"
        try:
            return ast.unparse(ann)
        except Exception:
            if isinstance(ann, ast.Name):
                return ann.id
            return "Any"

    def _collect_param_specs(self, args: ast.arguments) -> list[tuple[str, str]]:
        specs: list[tuple[str, str]] = []
        for a in args.posonlyargs:
            specs.append((a.arg, self._annotation_to_str(a.annotation)))
        for a in args.args:
            specs.append((a.arg, self._annotation_to_str(a.annotation)))
        if args.vararg:
            specs.append(("*" + args.vararg.arg, self._annotation_to_str(args.vararg.annotation)))
        for a in args.kwonlyargs:
            specs.append((a.arg, self._annotation_to_str(a.annotation)))
        if args.kwarg:
            specs.append(("**" + args.kwarg.arg, self._annotation_to_str(args.kwarg.annotation)))
        return specs

    def visit_ClassDef(self, node: ast.ClassDef):
        class_qn = self._make_class_qualname(node.name)
        self.classes.setdefault(class_qn, {
            "module": self.module_name,
            "name": node.name,
            "qualname": class_qn,
            "filename": self.filename,
            "lineno": node.lineno,
            "methods": [],
        })

        prev = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = prev

    def _register_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str, is_nested: bool):
        param_specs = self._collect_param_specs(node.args)

        is_method = (self.current_class is not None) and (not is_nested)
        class_qn = self._make_class_qualname(self.current_class) if is_method else None  # type: ignore[arg-type]

        if is_method and param_specs and param_specs[0][0] in ("self", "cls"):
            param_specs = param_specs[1:]

        param_hints = [hint for _, hint in param_specs]

        info = FunctionInfo(
            qualname=qualname,
            module=self.module_name,
            cls=self.current_class,
            name=node.name,
            params=param_hints,
            filename=self.filename,
            lineno=node.lineno,
            is_class_method=is_method,
            class_qualname=class_qn,
            is_nested=is_nested,
        )
        self.functions[qualname] = info

        if is_method and class_qn in self.classes:
            self.classes[class_qn]["methods"].append(qualname)

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        is_nested = self.function_depth >= 1

        if not is_nested:
            qualname = self._make_func_qualname(node.name)
        else:
            # nested function qualname lives under the currently recorded outer function
            # outer must exist because function_depth>=1 implies we are inside one
            assert self.current_function_qualname is not None
            outer = self.current_function_qualname
            qualname = self._make_local_func_qualname(outer, node.name)
            # register mapping for rewriting calls like inner()
            self.nested_defs.setdefault(outer, {})[node.name] = qualname

        # Register it (even if nested; we'll filter later when saving)
        self._register_function(node, qualname, is_nested=is_nested)

        # Enter this function scope
        self.function_depth += 1
        prev_current = self.current_function_qualname

        # Track calls inside this function too (including nested function bodies),
        # but they will be filtered out at save time if nested.
        self.current_function_qualname = qualname

        self.generic_visit(node)

        # Exit
        self.current_function_qualname = prev_current
        self.function_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_function(node)

    def visit_Call(self, node: ast.Call):
        if self.current_function_qualname is not None:
            callee = self._extract_call_name(node.func)
            if callee is not None:
                # Rewrite bare-name calls to nested defs within THIS function
                if isinstance(node.func, ast.Name):
                    local_map = self.nested_defs.get(self.current_function_qualname, {})
                    if callee in local_map:
                        callee = local_map[callee]
                self.functions[self.current_function_qualname].calls.add(callee)
        self.generic_visit(node)
