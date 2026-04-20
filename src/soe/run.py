"""
Backward-compatible runner shim.

Use `soe.runner` for new code. This module keeps existing imports/tests working.
"""

from soe.runner import (  # noqa: F401
    can_resolve_function,
    dump_type_list_to_json,
    f_run,
    json_safe,
    merge_type_dicts_unique,
    resolve_by_dotted_name,
    run,
    type_key,
    type_name,
)
