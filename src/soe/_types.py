from enum import Enum
from typing import Any, Optional
from collections.abc import Callable

class RunUnableToResolve(Exception):
    pass

class RunTimeout(Exception):
    pass

class RunStatus(Enum):
    SUCCESS = 0
    ERROR = 1
    TIMEOUT = 2


class RunResult:
    def __init__(
        self,
        f: Optional[Callable] = None,
        f_name: str = "",
        params: Optional[list] = None,
        status: RunStatus = RunStatus.SUCCESS,
        exception_type: str = "",
        exception_message: str = "",
        exception_module: str = "",
        error_origin: str = "",
        traceback_summary: Optional[list[str]] = None,
    ):
        self.f = f
        self.f_name = f_name
        self.params = list(params) if params is not None else []
        self.status = status
        self.exception_type = exception_type
        self.exception_message = exception_message
        self.exception_module = exception_module
        self.error_origin = error_origin
        self.traceback_summary = list(traceback_summary) if traceback_summary else []
        self.dotted = True if isinstance(f_name, str) else False

    def to_dict(self) -> dict[str, Any]:
        return {
            "f_name": self.f_name,
            "params": list(self.params),
            "status": self.status.name,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "exception_module": self.exception_module,
            "error_origin": self.error_origin,
            "traceback_summary": list(self.traceback_summary),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunResult":
        status_name = data.get("status", RunStatus.ERROR.name)
        status = RunStatus[status_name] if status_name in RunStatus.__members__ else RunStatus.ERROR
        return cls(
            f_name=data.get("f_name", ""),
            params=data.get("params", []),
            status=status,
            exception_type=data.get("exception_type", ""),
            exception_message=data.get("exception_message", ""),
            exception_module=data.get("exception_module", ""),
            error_origin=data.get("error_origin", ""),
            traceback_summary=data.get("traceback_summary", []),
        )

    def __repr__(self) -> str:
        return (
            "RunResult("
            f"f={self.f}, "
            f"f_name='{self.f_name}', "
            f"params={self.params}, "
            f"status={self.status}, "
            f"exception_type='{self.exception_type}', "
            f"error_origin='{self.error_origin}'"
            ")"
        )
