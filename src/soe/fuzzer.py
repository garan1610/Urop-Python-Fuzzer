from pathlib import Path
import logging
import random
import time
from collections import defaultdict
from typing import Any

import soe._global as _global
from soe._types import RunResult, RunStatus, RunUnableToResolve
from soe.runner import can_resolve_function, f_run, type_name

logger = logging.getLogger("fuzzer")
KNOWN_TYPE_SPACE = ["str", "int", "float", "bool", "list", "dict", "tuple"]
PRIMITIVE_TYPE_SPACE = {"str", "int", "float", "bool"}
EXPLORE_PROBABILITY = 0.25
EXPECTED_ERROR_TYPES = {
    "TypeError",
    "ValueError",
    "KeyError",
    "LookupError",
    "UnicodeError",
}
UNEXPECTED_ERROR_TYPES = {
    "AssertionError",
    "AttributeError",
    "ImportError",
    "ModuleNotFoundError",
    "MemoryError",
    "NameError",
    "RuntimeError",
    "RecursionError",
    "SyntaxError",
}


def fuzz_string():
    length = random.randint(1, 20)
    charset = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choices(charset, k=length))


def fuzz_int():
    return random.randint(-1000000, 1000000)


def fuzz_float():
    return random.uniform(-1e6, 1e6)


def fuzz_bool():
    return random.choice([True, False])


def fuzz_list():
    return []


def fuzz_dict():
    return {}


def fuzz_tuple():
    return ()


def _random_fallback_for_type(type_str: str):
    if type_str == "str":
        return fuzz_string()
    if type_str == "int":
        return fuzz_int()
    if type_str == "float":
        return fuzz_float()
    if type_str == "bool":
        return fuzz_bool()
    if type_str == "list":
        return fuzz_list()
    if type_str == "dict":
        return fuzz_dict()
    if type_str == "tuple":
        return fuzz_tuple()
    return fuzz_int()


def _build_type_sample_index() -> dict[str, list[Any]]:
    index: dict[str, list[Any]] = defaultdict(list)
    type_list = _global.get_type_list()
    for values in type_list.values():
        for value in values:
            short_name = type_name(value)
            cls = value.__class__
            full_name = f"{cls.__module__}.{cls.__qualname__}"
            index[short_name].append(value)
            index[full_name].append(value)
    return index


def _available_type_space(sample_index: dict[str, list[Any]]) -> list[str]:
    discovered_types = []
    for type_name_key, values in sample_index.items():
        if not values:
            continue
        if "." in type_name_key:
            continue
        if type_name_key not in discovered_types:
            discovered_types.append(type_name_key)

    combined = list(KNOWN_TYPE_SPACE)
    for type_name_key in discovered_types:
        if type_name_key not in combined:
            combined.append(type_name_key)
    return combined


def _sample_for_type(type_str: str, sample_index: dict[str, list[Any]], use_epsilon_greedy: bool = False):
    candidates = sample_index.get(type_str, [])
    if not candidates and "." in type_str:
        candidates = sample_index.get(type_str.split(".")[-1], [])

    if type_str in PRIMITIVE_TYPE_SPACE:
        return _random_fallback_for_type(type_str)

    if candidates:
        return random.choice(candidates)

    return _random_fallback_for_type(type_str)


def _normalize_type_hint(type_hint: str) -> str:
    h = (type_hint or "").strip()
    if not h:
        return "Any"

    h = h.removeprefix("typing.")
    low = h.lower()
    if low == "any":
        return "Any"
    if "str" in low:
        return "str"
    if "bool" in low:
        return "bool"
    if "float" in low:
        return "float"
    if "int" in low:
        return "int"
    if "list" in low:
        return "list"
    if "dict" in low:
        return "dict"
    if "tuple" in low:
        return "tuple"
    if "|" in h:
        first_part = h.split("|", 1)[0].strip()
        if first_part:
            return _normalize_type_hint(first_part)
    if "[" in h and h.endswith("]"):
        prefix, _, inner = h.partition("[")
        if prefix in {"Optional", "Union", "Annotated"} and inner:
            first_inner = inner[:-1].split(",", 1)[0].strip()
            if first_inner:
                return _normalize_type_hint(first_inner)
        return prefix.split(".")[-1]
    return h.split(".")[-1]


def _choose_param_type(type_counts: dict[str, int], use_epsilon_greedy: bool = False) -> str:
    observed_types = list(type_counts.keys())
    if not observed_types:
        return random.choice(KNOWN_TYPE_SPACE)

    if use_epsilon_greedy and random.random() < EXPLORE_PROBABILITY:
        unexplored = [type_name_ for type_name_ in KNOWN_TYPE_SPACE if type_name_ not in observed_types]
        if unexplored:
            return random.choice(unexplored)
        return random.choice(KNOWN_TYPE_SPACE)

    weights = [max(1, int(type_counts[t])) for t in observed_types]
    return random.choices(observed_types, weights=weights, k=1)[0]


def _choose_any_type(sample_index: dict[str, list[Any]], use_epsilon_greedy: bool = False) -> str:
    available_types = _available_type_space(sample_index)
    if not available_types:
        return random.choice(KNOWN_TYPE_SPACE)

    if use_epsilon_greedy and random.random() < EXPLORE_PROBABILITY:
        return random.choice(available_types)

    typed_candidates = [type_name_key for type_name_key in available_types if sample_index.get(type_name_key)]
    if typed_candidates:
        return random.choice(typed_candidates)
    return random.choice(available_types)


def _build_param_values(
    params_info: dict | list,
    sample_index: dict[str, list[Any]],
    use_epsilon_greedy: bool = False,
) -> list:
    if not params_info:
        return []

    values = []

    if isinstance(params_info, list):
        for hint in params_info:
            normalized_hint = _normalize_type_hint(str(hint))
            chosen_type = normalized_hint
            chosen_with_epsilon = False
            if normalized_hint == "Any":
                chosen_type = _choose_any_type(sample_index, use_epsilon_greedy=use_epsilon_greedy)
                chosen_with_epsilon = use_epsilon_greedy
            values.append(
                _sample_for_type(
                    chosen_type,
                    sample_index,
                    use_epsilon_greedy=chosen_with_epsilon,
                )
            )
        return values

    if isinstance(params_info, dict):
        for param_name, type_counts in params_info.items():
            if param_name in ("self", "cls"):
                continue
            if not type_counts:
                seed_type = _choose_any_type(sample_index, use_epsilon_greedy=use_epsilon_greedy)
                logger.debug("No type info for param %s, seeding with %s", param_name, seed_type)
                values.append(
                    _sample_for_type(
                        seed_type,
                        sample_index,
                        use_epsilon_greedy=use_epsilon_greedy,
                    )
                )
                continue
            chosen_type = _choose_param_type(type_counts, use_epsilon_greedy=use_epsilon_greedy)
            chosen_with_epsilon = chosen_type == "Any" and use_epsilon_greedy
            if chosen_type == "Any":
                chosen_type = _choose_any_type(sample_index, use_epsilon_greedy=use_epsilon_greedy)
            values.append(
                _sample_for_type(
                    chosen_type,
                    sample_index,
                    use_epsilon_greedy=chosen_with_epsilon,
                )
            )
        return values

    return values


def _get_function_meta(f_name: str) -> dict[str, Any]:
    function_list = _global.get_function_list()
    funcs = function_list.get("functions", {})
    return funcs.get(f_name, {}) if isinstance(funcs, dict) else {}


def _classify_error(result: RunResult) -> str:
    meta = _get_function_meta(result.f_name)
    declared = {str(name) for name in meta.get("expected_exceptions", [])}
    exc_name = result.exception_type or ""
    exc_full_name = ".".join(part for part in (result.exception_module, exc_name) if part)

    if result.status == RunStatus.TIMEOUT:
        return "unexpected"
    if result.error_origin not in ("target", ""):
        return "unexpected"
    if exc_name in declared or exc_full_name in declared:
        return "expected"
    if exc_name in UNEXPECTED_ERROR_TYPES:
        return "unexpected"
    if exc_name in EXPECTED_ERROR_TYPES:
        return "expected"
    if result.error_origin == "target" and exc_name:
        return "unknown"
    return "unexpected"


def _record_error(result: RunResult, label: str, params: list) -> None:
    detail = result.exception_type or result.status.name
    message = result.exception_message or ""
    origin = result.error_origin or "unknown"
    if label == "expected":
        logger.warning("EXPECTED_ERROR %s: %s origin=%s params=%s %s", result.f_name, detail, origin, params, message)
    elif label == "unknown":
        logger.error("UNKNOWN_ERROR %s: %s origin=%s params=%s %s", result.f_name, detail, origin, params, message)
    else:
        logger.error("UNEXPECTED_ERROR %s: %s origin=%s params=%s %s", result.f_name, detail, origin, params, message)
    _global.add_error(result)


def _attempt_prefix(
    f_name: str,
    global_iteration: int | None = None,
    func_iteration: int | None = None,
) -> str:
    prefix = f_name
    if global_iteration is not None:
        prefix += f" [global_iter={global_iteration}"
        if func_iteration is not None:
            prefix += f", func_iter={func_iteration}"
        prefix += "]"
    elif func_iteration is not None:
        prefix += f" [func_iter={func_iteration}]"
    return prefix


def _finalize_status_summary(
    success_count: int,
    expected_error_count: int,
    unexpected_error_count: int,
    unknown_error_count: int,
    skipped_count: int | None = None,
    skipped_reasons: dict[str, int] | None = None,
    iterations: int | None = None,
    elapsed: float | None = None,
) -> None:
    if iterations is not None:
        logger.info("  Total iterations: %s", iterations)
    logger.info("  Success: %s", success_count)
    logger.info("  Expected errors: %s", expected_error_count)
    logger.info("  Unexpected errors: %s", unexpected_error_count)
    logger.info("  Unknown errors: %s", unknown_error_count)
    if skipped_count is not None:
        logger.info("  Skipped: %s", skipped_count)
    if skipped_reasons:
        top_reasons = sorted(skipped_reasons.items(), key=lambda kv: kv[1], reverse=True)[:5]
        for reason, count in top_reasons:
            logger.info("  Skip reason (%s): %s", count, reason)
    if elapsed is not None:
        logger.info("  Total time: %.2fs", elapsed)

    recorded_errors = _global.get_error_list()
    if recorded_errors:
        logger.info("Errors recorded:")
        for item in recorded_errors:
            logger.error(repr(item))
    else:
        logger.info("No errors recorded.")


def simple_fuzzer(fuzz_dir: Path, show_success: bool = False) -> None:
    del fuzz_dir
    func_list = _global.get_function_list()
    if "functions" in func_list and isinstance(func_list["functions"], dict):
        func_list = func_list["functions"]

    success_count = 0
    expected_error_count = 0
    unexpected_error_count = 0
    unknown_error_count = 0
    skipped_count = 0
    iterations = 0
    skipped_reasons: dict[str, int] = defaultdict(int)
    sample_index = _build_type_sample_index()

    for f_name, f_meta in func_list.items():
        logger.debug("Fuzzing function %s", f_name)
        resolvable, reason = can_resolve_function(f_name)
        if not resolvable:
            skipped_count += 1
            skipped_reasons[reason or "unresolvable target"] += 1
            logger.debug("Skipping unresolvable function %s: %s", f_name, reason)
            continue

        params = _build_param_values(f_meta.get("params", []), sample_index)
        iterations += 1
        attempt_label = _attempt_prefix(f_name, global_iteration=iterations, func_iteration=1)

        try:
            result, _ = f_run(f_name, params)
            sample_index = _build_type_sample_index()
            if result.status == RunStatus.SUCCESS:
                success_count += 1
                if show_success:
                    logger.info("SUCCESS %s params=%s", attempt_label, params)
                else:
                    logger.debug("SUCCESS %s", attempt_label)
            else:
                classification = _classify_error(result)
                if classification == "expected":
                    expected_error_count += 1
                elif classification == "unknown":
                    unknown_error_count += 1
                else:
                    unexpected_error_count += 1
                result.f_name = attempt_label
                _record_error(result, classification, params)
        except RunUnableToResolve as e:
            skipped_count += 1
            skipped_reasons[str(e)] += 1
            logger.debug("Skipping %s: %s", attempt_label, e)
            continue
        except Exception as e:
            unexpected_error_count += 1
            logger.error("UNEXPECTED_EXCEPTION %s: %s params=%s", attempt_label, e, params)

    logger.info("Simple fuzzing completed:")
    _finalize_status_summary(
        success_count=success_count,
        expected_error_count=expected_error_count,
        unexpected_error_count=unexpected_error_count,
        unknown_error_count=unknown_error_count,
        skipped_count=skipped_count,
        skipped_reasons=skipped_reasons,
        iterations=iterations,
    )


def blackbox_fuzzer(
    fuzz_dir: Path,
    duration_seconds: int = 60,
    time_per_func: float = 1.0,
    show_success: bool = False,
) -> None:
    del fuzz_dir
    func_list = _global.get_function_list()
    if "functions" in func_list and isinstance(func_list["functions"], dict):
        func_list = func_list["functions"]

    type_list = _global.get_type_list()
    type_samples_count = sum(len(vals) for vals in type_list.values()) if isinstance(type_list, dict) else 0
    logger.info(
        "Starting blackbox fuzzer with %s functions, %ss total, %ss per function",
        len(func_list),
        duration_seconds,
        time_per_func,
    )
    logger.info("Type list has %s types with %s total samples", len(type_list), type_samples_count)

    start_time = time.time()
    iterations = 0
    skipped_count = 0
    skipped_reasons: dict[str, int] = defaultdict(int)
    success_count = 0
    expected_error_count = 0
    unexpected_error_count = 0
    unknown_error_count = 0
    sample_index = _build_type_sample_index()

    for f_name, f_meta in list(func_list.items()):
        if time.time() - start_time >= duration_seconds:
            logger.info("Total duration %ss reached after %s iterations", duration_seconds, iterations)
            break

        logger.debug("Fuzzing function %s", f_name)
        resolvable, reason = can_resolve_function(f_name)
        if not resolvable:
            skipped_count += 1
            skipped_reasons[reason or "unresolvable target"] += 1
            logger.debug("Skipping unresolvable function %s: %s", f_name, reason)
            continue

        func_start = time.time()
        func_iterations = 0
        func_success = 0
        func_expected_error = 0
        func_unexpected_error = 0
        func_unknown_error = 0

        while time.time() - func_start < time_per_func:
            if time.time() - start_time >= duration_seconds:
                break

            func_iterations += 1
            iterations += 1
            attempt_label = _attempt_prefix(
                f_name,
                global_iteration=iterations,
                func_iteration=func_iterations,
            )
            param_values = _build_param_values(
                f_meta.get("params", []),
                sample_index,
                use_epsilon_greedy=True,
            )

            try:
                result, _ = f_run(f_name, param_values)
                sample_index = _build_type_sample_index()
                if result.status == RunStatus.SUCCESS:
                    func_success += 1
                    success_count += 1
                    if show_success:
                        logger.info("SUCCESS %s params=%s", attempt_label, param_values)
                else:
                    classification = _classify_error(result)
                    if classification == "expected":
                        func_expected_error += 1
                        expected_error_count += 1
                    elif classification == "unknown":
                        func_unknown_error += 1
                        unknown_error_count += 1
                    else:
                        func_unexpected_error += 1
                        unexpected_error_count += 1
                    result.f_name = attempt_label
                    _record_error(result, classification, param_values)
            except RunUnableToResolve as e:
                skipped_count += 1
                skipped_reasons[str(e)] += 1
                logger.debug("Skipping %s: %s", attempt_label, e)
                break
            except Exception as e:
                func_unexpected_error += 1
                unexpected_error_count += 1
                logger.error("UNEXPECTED_EXCEPTION %s: %s params=%s", attempt_label, e, param_values)
                break

        logger.info(
            "%s: %s iterations (%s success, %s expected errors, %s unexpected errors, %s unknown errors) in %.2fs",
            f_name,
            func_iterations,
            func_success,
            func_expected_error,
            func_unexpected_error,
            func_unknown_error,
            time.time() - func_start,
        )

    logger.info("Blackbox fuzzing completed:")
    _finalize_status_summary(
        success_count=success_count,
        expected_error_count=expected_error_count,
        unexpected_error_count=unexpected_error_count,
        unknown_error_count=unknown_error_count,
        skipped_count=skipped_count,
        skipped_reasons=skipped_reasons,
        iterations=iterations,
        elapsed=time.time() - start_time,
    )
