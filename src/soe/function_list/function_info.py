# function_info.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Set, Optional

@dataclass
class FunctionInfo:
    qualname: str
    module: str
    cls: Optional[str]
    class_qualname: Optional[str]
    name: str
    # ordered parameter annotation hints, e.g. ["int", "str", "Any"]
    params: list[str] = field(default_factory=list)

    is_class_method: bool = False
    
    filename: str = ""
    lineno: int = 0

    # short names of called functions (store as set for dedup)
    calls: Set[str] = field(default_factory=set)


    is_nested: bool = False

    def to_json_dict(self, dep_graph: dict[str, set[str]] | None = None) -> dict:
        return {
            "params": self.params,
            "filename": self.filename,
            "is_class_method": self.is_class_method,
            "class": self.cls,
            "class_qualname": self.class_qualname,
            "lineno": self.lineno,
            "calls": sorted((dep_graph.get(self.qualname, set()) if dep_graph else set())),
        }
