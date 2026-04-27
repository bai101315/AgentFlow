from pathlib import Path
import logging
from datetime import date

LOG_DIR = Path(__file__).resolve().parent / "logs"

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"
MAGENTA = "\033[35m"


class ExcludeLoggerPrefixFilter(logging.Filter):
    """Exclude records whose logger name matches configured prefixes."""

    def __init__(self, prefixes: tuple[str, ...]):
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        logger_name = record.name
        for prefix in self._prefixes:
            if logger_name == prefix or logger_name.startswith(f"{prefix}."):
                return False
        return True


def configure_logging() -> None:
    """Configure logging to both console and daily log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    daily_log_file = LOG_DIR / f"{date.today():%Y-%m-%d}.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(
        ExcludeLoggerPrefixFilter(("agents.memory", "backend.agents.memory"))
    )

    file_handler = logging.FileHandler(daily_log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


# def repair_langgraph_prebuilt():
#     # Workaround for incompatible langgraph/langgraph-prebuilt builds where
#     # langgraph.runtime does not expose ExecutionInfo/ServerInfo.
#     try:
#         import langgraph.runtime as _langgraph_runtime

#         if not hasattr(_langgraph_runtime, "ExecutionInfo"):
#             class _ExecutionInfo(TypedDict, total=False):
#                 pass

#             _langgraph_runtime.ExecutionInfo = _ExecutionInfo  # type: ignore[attr-defined]

#         if not hasattr(_langgraph_runtime, "ServerInfo"):
#             class _ServerInfo(TypedDict, total=False):
#                 pass

#             _langgraph_runtime.ServerInfo = _ServerInfo  # type: ignore[attr-defined]

#         runtime_cls = getattr(_langgraph_runtime, "Runtime", None)
#         if runtime_cls is not None:
#             # Some langgraph-prebuilt builds expect these attrs on Runtime instances.
#             if not hasattr(runtime_cls, "execution_info"):
#                 setattr(runtime_cls, "execution_info", None)
#             if not hasattr(runtime_cls, "server_info"):
#                 setattr(runtime_cls, "server_info", None)
#     except Exception:
#         # Keep startup resilient; downstream import errors will still surface.
#         pass
