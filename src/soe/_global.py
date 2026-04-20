import threading
import logging
from soe._types import RunResult
from pathlib import Path

logger = logging.getLogger('_global')

dir_path = Path()
function_list: dict[str, dict] = {}
type_list: dict[type, list] = {}
error_list: list[RunResult] = []
_f_lock = threading.Lock()
_t_lock = threading.Lock()
_e_lock = threading.Lock()


def init_global() -> None:
    logger.debug("Initializing global state")
    set_function_list({})
    set_type_list({})
    clear_error_list()


def get_dir_path() -> Path:
    return dir_path

def set_dir_path(path: Path) -> None:
    global dir_path
    dir_path = path


# function_list
def get_function_list() -> dict:
    with _f_lock:
        return function_list.copy()
    
def get_function(f_name: str) -> dict:
    with _f_lock:
        return function_list.get(f_name, {})

def set_function_list(f_list: dict) -> None:
    global function_list
    with _f_lock:
        function_list = f_list

def set_function(f_name: str, f_info: dict) -> None:
    with _f_lock:
        function_list.update({f_name: f_info})


# type_list
def get_type_list() -> dict:
    with _t_lock:
        return {key: list(values) for key, values in type_list.items()}
    
def get_type(t_name: type) -> list:
    with _t_lock:
        return type_list.get(t_name, [])
    
def set_type_list(t_list: dict) -> None:
    global type_list
    with _t_lock:
        type_list = t_list

def set_type(t_name: type, t_value: list) -> None:
    with _t_lock:
        type_list.update({t_name: t_value})


# error_list
def get_error_list() -> list:
    with _e_lock:
        return list(error_list)
    
def add_error(result: RunResult) -> None:
    with _e_lock:
        error_list.append(result)


def clear_error_list() -> None:
    global error_list
    with _e_lock:
        error_list = []


if __name__ == "__main__":
    init_global()
