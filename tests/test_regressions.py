import os
import pickle
import sys
from pathlib import Path

import soe._global as _global
from soe._types import RunResult, RunStatus
from soe.fuzzer import _build_param_values, _build_type_sample_index, _choose_param_type, _classify_error, _sample_for_type
from soe.function_list.function_list import generate_function_list
from soe.runner import _worker_main, f_run


TEST_REPO = Path("tests/test_repo")
TEST_REPO_ABS = TEST_REPO.resolve()
if str(TEST_REPO_ABS) not in sys.path:
    sys.path.insert(0, str(TEST_REPO_ABS))

from tests.test_repo.test_src_3.main import CustomClass


def _init_test_repo() -> None:
    _global.init_global()
    _global.set_dir_path(TEST_REPO_ABS)
    _global.set_function_list(generate_function_list(TEST_REPO))


def test_init_global_clears_error_list():
    _global.init_global()
    _global.add_error(RunResult(f_name="demo", status=RunStatus.ERROR))

    _global.init_global()

    assert _global.get_error_list() == []


def test_error_classification_uses_exception_metadata_not_function_name(monkeypatch):
    monkeypatch.setenv("SOE_RUNNER_INPROCESS", "1")
    _init_test_repo()

    result, _ = f_run("test_src_1.main.error_func", [1])

    assert result.status == RunStatus.ERROR
    assert result.exception_type == "ZeroDivisionError"
    assert result.error_origin == "target"
    assert _classify_error(result) == "unknown"


def test_failed_run_does_not_merge_type_samples(monkeypatch):
    monkeypatch.setenv("SOE_RUNNER_INPROCESS", "1")
    _init_test_repo()

    result, _ = f_run("test_src_1.main.error_func", [1])

    assert result.status == RunStatus.ERROR
    assert _global.get_type_list() == {}


def test_blackbox_sampling_balances_primitive_sources(monkeypatch):
    sample_index = {"int": [99]}
    monkeypatch.setattr("soe.fuzzer.fuzz_int", lambda: -7)
    generated = _sample_for_type("int", sample_index, use_epsilon_greedy=True)
    assert generated == -7


def test_blackbox_sampling_prefers_nonprimitive_type_list(monkeypatch):
    sample_list = [1, 2, 3]
    sample_index = {"list": [sample_list]}

    monkeypatch.setattr("soe.fuzzer.random.random", lambda: 0.1)
    assert _sample_for_type("list", sample_index, use_epsilon_greedy=True) == sample_list


def test_epsilon_greedy_type_selection_explores_unseen_types(monkeypatch):
    monkeypatch.setattr("soe.fuzzer.random.random", lambda: 0.1)
    chosen = _choose_param_type({"int": 5, "float": 1}, use_epsilon_greedy=True)

    assert chosen in {"str", "bool", "list", "dict", "tuple"}


def test_annotation_hint_can_reuse_custom_object_from_type_list():
    _global.init_global()
    custom_obj = CustomClass(7)
    _global.set_type_list({CustomClass: [custom_obj]})
    sample_index = _build_type_sample_index()

    values = _build_param_values(["CustomClass"], sample_index)

    assert values == [custom_obj]


def test_any_hint_can_reuse_discovered_custom_object(monkeypatch):
    _global.init_global()
    custom_obj = CustomClass(11)
    _global.set_type_list({CustomClass: [custom_obj]})
    sample_index = _build_type_sample_index()

    monkeypatch.setattr(
        "soe.fuzzer.random.choice",
        lambda seq: "CustomClass" if "CustomClass" in seq else seq[0],
    )

    values = _build_param_values(["Any"], sample_index, use_epsilon_greedy=True)

    assert values == [custom_obj]


def test_subprocess_runner_can_unpickle_custom_object_params():
    _init_test_repo()

    custom_obj = CustomClass(5)
    _global.set_type_list({CustomClass: [custom_obj]})
    workspace_tmp = Path(".tmp") / "pytest_runner_payloads"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    payload_path = workspace_tmp / "payload.pkl"
    result_path = workspace_tmp / "result.pkl"

    payload = {
        "f_name": "test_src_3.main.add_object",
        "params": [2, custom_obj],
        "dir_path": str(TEST_REPO_ABS),
        "function_list": _global.get_function_list(),
    }
    with open(payload_path, "wb") as f:
        pickle.dump(payload, f)

    status_code = _worker_main(str(payload_path), str(result_path), str(TEST_REPO_ABS))

    with open(result_path, "rb") as f:
        result = pickle.load(f)

    run_result = RunResult.from_dict(result["run_result"])
    assert status_code == 0
    assert run_result.status == RunStatus.SUCCESS
