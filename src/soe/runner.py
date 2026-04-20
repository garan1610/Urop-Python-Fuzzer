import copy
import importlib
import inspect
import json
import logging
import os
import pickle
import subprocess
import sys
import tempfile
import traceback
import types
from pathlib import Path
from typing import Any

import soe._global as _global
from soe._global import get_dir_path, get_function_list, get_type_list, set_type_list
from soe._types import RunResult, RunStatus, RunTimeout, RunUnableToResolve

logger = logging.getLogger("runner")

MAX_SAMPLES_PER_TYPE = 50
MAX_STRING_SAMPLE_LENGTH = 256
MAX_CONTAINER_SAMPLE_ITEMS = 32
MAX_NESTED_SAMPLE_DEPTH = 3
MAX_REPR_FALLBACK_LENGTH = 128
MAX_TRACEBACK_LINES = 6


def _prepare_worker_import_path(dir_path: str) -> None:
    if not dir_path:
        return

    resolved = str(Path(dir_path).resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def _fingerprint(val: Any) -> str:
    if val is None or isinstance(val, (int, float, str, bool)):
        return f"{type(val).__name__}:{val!r}"

    if isinstance(val, (list, tuple)):
        inner = ",".join(_fingerprint(x) for x in val)
        return f"{type(val).__name__}:[{inner}]"

    if isinstance(val, dict):
        items = sorted(val.items(), key=lambda kv: str(kv[0]))
        inner = ",".join(f"{str(k)!r}:{_fingerprint(v)}" for k, v in items)
        return f"dict:{{{inner}}}"

    cls = val.__class__
    return f"{cls.__module__}.{cls.__qualname__}:{repr(val)}"


def type_key(val: Any) -> type:
    return val.__class__


def _is_sampleable_value(val: Any) -> bool:
    if inspect.isclass(val) or inspect.ismodule(val) or inspect.isroutine(val):
        return False

    nonsampleable_types = (
        types.ModuleType,
        types.FunctionType,
        types.BuiltinFunctionType,
        types.MethodType,
        types.CodeType,
        types.FrameType,
        types.TracebackType,
        types.GeneratorType,
        types.CoroutineType,
        types.AsyncGeneratorType,
    )
    if isinstance(val, nonsampleable_types):
        return False

    try:
        pickle.dumps(type_key(val))
        pickle.dumps(val)
    except Exception:
        return False

    return True


def _sanitize_sample(val: Any, depth: int = 0) -> Any:
    if isinstance(val, str):
        if len(val) > MAX_STRING_SAMPLE_LENGTH:
            return val[:MAX_STRING_SAMPLE_LENGTH]
        return val

    if depth >= MAX_NESTED_SAMPLE_DEPTH:
        if isinstance(val, (list, tuple, dict)):
            return f"<truncated:{type(val).__name__}>"
        return val

    if isinstance(val, list):
        return [_sanitize_sample(x, depth + 1) for x in val[:MAX_CONTAINER_SAMPLE_ITEMS]]

    if isinstance(val, tuple):
        return tuple(_sanitize_sample(x, depth + 1) for x in val[:MAX_CONTAINER_SAMPLE_ITEMS])

    if isinstance(val, dict):
        out: dict[Any, Any] = {}
        for k, v in list(val.items())[:MAX_CONTAINER_SAMPLE_ITEMS]:
            key = k
            if not isinstance(key, (str, int, float, bool, type(None), tuple)):
                key = repr(key)[:MAX_REPR_FALLBACK_LENGTH]
            out[key] = _sanitize_sample(v, depth + 1)
        return out

    return val


def _add_type_sample(type_store: dict[type, list], seen: dict[type, set[str]], val: Any) -> None:
    val = _sanitize_sample(val)
    if not _is_sampleable_value(val):
        return

    k = type_key(val)
    bucket = type_store.setdefault(k, [])
    fingerprints = seen.setdefault(k, set())
    fp = _fingerprint(val)

    if fp in fingerprints or len(bucket) >= MAX_SAMPLES_PER_TYPE:
        return

    bucket.append(val)
    fingerprints.add(fp)


def _marker_for_dedupe(item: Any) -> Any:
    if isinstance(item, dict):
        try:
            marker = tuple(sorted(item.items()))
            hash(marker)
            return marker
        except Exception:
            return _fingerprint(item)

    try:
        hash(item)
        return item
    except Exception:
        return _fingerprint(item)


def merge_type_dicts_unique(d1: dict[type, list], d2: dict[type, list]) -> dict[type, list]:
    merged: dict[type, list] = {}
    for key in set(d1) | set(d2):
        combined = d1.get(key, []) + d2.get(key, [])
        seen: set[Any] = set()
        unique_items: list[Any] = []
        for item in combined:
            marker = _marker_for_dedupe(item)
            if marker in seen:
                continue
            seen.add(marker)
            unique_items.append(item)
        merged[key] = unique_items[:MAX_SAMPLES_PER_TYPE]
    return merged


def _empty_type_store() -> dict[type, list]:
    return {}


def _format_traceback(exc: BaseException) -> list[str]:
    return traceback.format_exception(type(exc), exc, exc.__traceback__)[-MAX_TRACEBACK_LINES:]


def _classify_error_origin(exc: BaseException) -> str:
    if isinstance(exc, RunTimeout):
        return "timeout"
    if isinstance(exc, RunUnableToResolve):
        return "resolve"

    repo_root = str(get_dir_path().resolve())
    tb = exc.__traceback__
    while tb is not None:
        frame_file = tb.tb_frame.f_code.co_filename
        if frame_file and os.path.abspath(frame_file).startswith(repo_root):
            return "target"
        tb = tb.tb_next
    return "external"


def _build_error_result(
    target_fn,
    params: list,
    status: RunStatus,
    exc: BaseException | None = None,
) -> RunResult:
    if exc is None:
        return RunResult(f=target_fn, params=params, status=status)

    exc_type = type(exc)
    return RunResult(
        f=target_fn,
        params=params,
        status=status,
        exception_type=exc_type.__name__,
        exception_message=str(exc),
        exception_module=exc_type.__module__,
        error_origin=_classify_error_origin(exc),
        traceback_summary=_format_traceback(exc),
    )


def resolve_by_dotted_name(dotted: str):
    fuzz_dir_str = str(get_dir_path().resolve())
    if fuzz_dir_str not in sys.path:
        sys.path.insert(0, fuzz_dir_str)

    dotted_original = dotted
    if dotted.startswith("."):
        dotted = dotted.lstrip(".")

    parts = dotted.split(".")
    last_mod_exc: Exception | None = None

    for i in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:i])
        try:
            if dotted_original.startswith("."):
                mod = importlib.import_module(mod_name, package="soe")
            else:
                mod = importlib.import_module(mod_name)

            obj = mod
            for attr in parts[i:]:
                obj = getattr(obj, attr)

            if not callable(obj):
                raise RunUnableToResolve(f"{dotted} resolved to non-callable: {type(obj)}")
            return obj
        except ModuleNotFoundError as e:
            last_mod_exc = e
            continue
        except AttributeError as e:
            raise RunUnableToResolve(f"Attribute not found while resolving {dotted}: {e}") from e
        except Exception as e:
            raise RunUnableToResolve(f"Failed to resolve {dotted}: {e}") from e

    if last_mod_exc:
        raise RunUnableToResolve(
            f"Cannot import any module prefix of {dotted}: {last_mod_exc}"
        ) from last_mod_exc
    raise RunUnableToResolve(f"Cannot resolve: {dotted}")


def _instantiate_for_method(class_qualname: str):
    cls = resolve_by_dotted_name(class_qualname)
    if not inspect.isclass(cls):
        raise RunUnableToResolve(f"{class_qualname} did not resolve to a class")

    try:
        return cls()
    except Exception:
        try:
            instance = cls.__new__(cls)  # type: ignore
        except Exception as e:
            raise RunUnableToResolve(f"Cannot instantiate {class_qualname}: {e}") from e

        try:
            init = getattr(instance, "__init__", None)
            if callable(init):
                sig = inspect.signature(init)
                required = [
                    p
                    for p in sig.parameters.values()
                    if p.name != "self"
                    and p.default is inspect._empty
                    and p.kind
                    not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                ]
                if not required:
                    init()
        except Exception:
            pass
        return instance


def _resolve_callable_for_run(f_name: str):
    function_list = copy.deepcopy(get_function_list())
    funcs = function_list.get("functions", {})
    meta = funcs.get(f_name, {}) if isinstance(funcs, dict) else {}

    if meta.get("is_class_method"):
        class_qn = meta.get("class_qualname")
        method_name = f_name.split(".")[-1]
        if not class_qn:
            raise RunUnableToResolve(f"Class method {f_name} has no class_qualname")
        instance = _instantiate_for_method(class_qn)
        try:
            return getattr(instance, method_name)
        except AttributeError as e:
            raise RunUnableToResolve(f"Method {method_name} not found on {class_qn}") from e

    return resolve_by_dotted_name(f_name)


def json_safe(obj: Any):
    if obj is None or isinstance(obj, (int, float, str, bool)):
        return obj
    if isinstance(obj, list):
        return [json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    cls = obj.__class__
    return {"__type__": f"{cls.__module__}.{cls.__qualname__}", "repr": repr(obj)}


def dump_type_list_to_json(type_list: dict[type, list], path: str = "type_list.json") -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.seek(0)
        f.truncate(0)
        json_dict = {}
        for cls_type, vals in type_list.items():
            key = (
                f"{cls_type.__module__}.{cls_type.__qualname__}"
                if hasattr(cls_type, "__module__")
                else str(cls_type)
            )
            json_dict[key] = [json_safe(v) for v in vals]
        json.dump(json_dict, f, indent=2)


def f_run(f_name, params=None) -> tuple[RunResult, dict]:
    if params is None:
        params = []
    if _use_subprocess_runner():
        return _f_run_in_worker(f_name, params)
    target_fn = _resolve_callable_for_run(f_name)
    result = run(target_fn, params)
    result[0].f_name = f_name
    return result


def _use_subprocess_runner() -> bool:
    if os.environ.get("SOE_RUNNER_WORKER") == "1":
        return False
    if os.environ.get("SOE_RUNNER_INPROCESS") == "1":
        return False
    return True


def _resolve_runner_python() -> str:
    env_py = os.environ.get("SOE_VENV_PYTHON")
    if env_py and Path(env_py).is_file():
        return env_py

    active_venv = os.environ.get("VIRTUAL_ENV")
    if active_venv:
        active_py = Path(active_venv) / "Scripts" / "python.exe"
        if active_py.is_file():
            return str(active_py)

    project_root = Path(__file__).resolve().parents[2]
    win_venv_py = project_root / ".venv" / "Scripts" / "python.exe"
    if win_venv_py.is_file():
        return str(win_venv_py)

    return sys.executable


def _f_run_in_worker(f_name: str, params: list) -> tuple[RunResult, dict]:
    dir_path = str(get_dir_path())
    payload = {
        "f_name": f_name,
        "params": params,
        "dir_path": dir_path,
        "function_list": get_function_list(),
    }

    with tempfile.TemporaryDirectory(prefix="soe_runner_") as td:
        payload_path = Path(td) / "payload.pkl"
        result_path = Path(td) / "result.pkl"
        with open(payload_path, "wb") as f:
            pickle.dump(payload, f)

        env = os.environ.copy()
        env["SOE_RUNNER_WORKER"] = "1"
        project_root = Path(__file__).resolve().parents[2]
        src_path = project_root / "src"
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{src_path}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(src_path)
        )
        cmd = [
            _resolve_runner_python(),
            "-m",
            "soe.runner",
            "--worker",
            str(payload_path),
            str(result_path),
            dir_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(project_root))
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            raise RunUnableToResolve(f"Worker run failed for {f_name}: {msg}")

        with open(result_path, "rb") as f:
            result = pickle.load(f)

    updated_function_list = result.get("function_list")
    if isinstance(updated_function_list, dict):
        _global.set_function_list(updated_function_list)

    delta_type_list = result.get("type_list", {})
    if isinstance(delta_type_list, dict):
        merged = merge_type_dicts_unique(get_type_list(), delta_type_list)
        _global.set_type_list(merged)
    else:
        merged = get_type_list()

    run_result = RunResult.from_dict(result.get("run_result", {}))
    run_result.f_name = f_name
    run_result.params = list(params)
    return run_result, merged


def can_resolve_function(f_name: str) -> tuple[bool, str]:
    try:
        _resolve_callable_for_run(f_name)
        return True, ""
    except Exception as e:
        return False, str(e)


def type_name(val) -> str:
    return type(val).__name__


def _inc_param_type(function_list: dict, func_name: str, param_name: str, val: Any) -> None:
    type_k = type_name(val)
    funcs = function_list.setdefault("functions", {})
    fmeta = funcs.setdefault(func_name, {})
    param_types_all = fmeta.setdefault("param_types", {})
    param_types = param_types_all.setdefault(param_name, {})
    param_types[type_k] = param_types.get(type_k, 0) + 1


def run(target_fn, params=None) -> tuple[RunResult, dict]:
    if params is None:
        params = []

    function_list = get_function_list()
    run_type_store = _empty_type_store()
    samples_seen: dict[type, set[str]] = {}
    tracked_frames = set()
    locals_seen_keys: dict[int, set[str]] = {}

    def tracer(frame, event, arg):
        if event == "call":
            code = frame.f_code
            module_name = frame.f_globals.get("__name__", "")
            qualname = code.co_qualname
            callee_name = f"{module_name}.{qualname}" if module_name else qualname

            is_target_entry = code.co_name == target_fn.__name__ and frame.f_code is target_fn.__code__
            is_child_of_tracked = frame.f_back in tracked_frames

            if is_target_entry or is_child_of_tracked:
                tracked_frames.add(frame)
                locals_seen_keys[id(frame)] = set(frame.f_locals.keys())
                funcs = function_list.get("functions", {})
                if callee_name in funcs:
                    try:
                        args_info = inspect.getargvalues(frame)
                        for p in args_info.args:
                            if p in args_info.locals:
                                _inc_param_type(function_list, callee_name, p, args_info.locals[p])
                                _add_type_sample(run_type_store, samples_seen, args_info.locals[p])

                        if args_info.varargs and args_info.varargs in args_info.locals:
                            starred = f"*{args_info.varargs}"
                            v = args_info.locals[args_info.varargs]
                            _inc_param_type(function_list, callee_name, starred, v)
                            _add_type_sample(run_type_store, samples_seen, v)

                        if args_info.keywords and args_info.keywords in args_info.locals:
                            starred = f"**{args_info.keywords}"
                            v = args_info.locals[args_info.keywords]
                            _inc_param_type(function_list, callee_name, starred, v)
                            _add_type_sample(run_type_store, samples_seen, v)
                    except Exception:
                        pass

            return tracer

        if frame in tracked_frames:
            if event == "line":
                cur_keys = set(frame.f_locals.keys())
                prev_keys = locals_seen_keys.get(id(frame), set())
                new_keys = cur_keys - prev_keys
                locals_seen_keys[id(frame)] = cur_keys
                for k in new_keys:
                    try:
                        _add_type_sample(run_type_store, samples_seen, frame.f_locals[k])
                    except Exception:
                        pass
            elif event == "return":
                try:
                    _add_type_sample(run_type_store, samples_seen, arg)
                except Exception:
                    pass
                try:
                    for v in frame.f_locals.values():
                        _add_type_sample(run_type_store, samples_seen, v)
                except Exception:
                    pass
                tracked_frames.discard(frame)
                locals_seen_keys.pop(id(frame), None)

        return tracer

    old_trace = sys.gettrace()
    sys.settrace(tracer)
    try:
        target_fn(*params)
    except SystemExit as e:
        raise RunUnableToResolve(f"SystemExit while running target: {e}") from e
    except RunTimeout as e:
        result = _build_error_result(target_fn, params, RunStatus.TIMEOUT, e)
    except Exception as e:
        result = _build_error_result(target_fn, params, RunStatus.ERROR, e)
    else:
        set_type_list(merge_type_dicts_unique(get_type_list(), run_type_store))
        _global.set_function_list(function_list)
        result = RunResult(f=target_fn, params=params, status=RunStatus.SUCCESS)
    finally:
        sys.settrace(old_trace)

    return result, run_type_store


def _worker_main(payload_path: str, result_path: str, dir_path: str = "") -> int:
    payload: dict[str, Any] = {}
    try:
        _prepare_worker_import_path(dir_path)
        with open(payload_path, "rb") as f:
            payload = pickle.load(f)

        _global.init_global()
        _global.set_dir_path(Path(payload.get("dir_path", ".")))
        _global.set_function_list(payload.get("function_list", {}))

        f_name = payload["f_name"]
        params = payload.get("params", [])
        target_fn = _resolve_callable_for_run(f_name)
        run_result, type_delta = run(target_fn, params)
        run_result.f_name = f_name

        out = {
            "run_result": run_result.to_dict(),
            "function_list": _global.get_function_list(),
            "type_list": type_delta,
        }
    except Exception as exc:
        error_result = _build_error_result(None, payload.get("params", []), RunStatus.ERROR, exc)
        error_result.f_name = payload.get("f_name", "")
        out = {
            "run_result": error_result.to_dict(),
            "function_list": _global.get_function_list(),
            "type_list": {},
        }

    with open(result_path, "wb") as f:
        pickle.dump(out, f)
    return 0


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=3, metavar=("PAYLOAD", "RESULT", "DIR_PATH"))
    args = parser.parse_args()

    if args.worker:
        return _worker_main(args.worker[0], args.worker[1], args.worker[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
