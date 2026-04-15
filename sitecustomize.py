"""Process-wide compatibility shims loaded automatically by Python startup.

This module is imported by Python's site initialization when present on sys.path.
It provides a narrow workaround for incompatible langgraph/langgraph-prebuilt
combinations that expect newer runtime symbols/attributes.
"""

from typing import TypedDict


def _patch_langgraph_runtime() -> None:
    try:
        import langgraph.runtime as runtime_mod
    except Exception:
        return

    if not hasattr(runtime_mod, "ExecutionInfo"):
        class ExecutionInfo(TypedDict, total=False):
            pass

        runtime_mod.ExecutionInfo = ExecutionInfo  # type: ignore[attr-defined]

    if not hasattr(runtime_mod, "ServerInfo"):
        class ServerInfo(TypedDict, total=False):
            pass

        runtime_mod.ServerInfo = ServerInfo  # type: ignore[attr-defined]

    runtime_cls = getattr(runtime_mod, "Runtime", None)
    if runtime_cls is not None:
        if not hasattr(runtime_cls, "execution_info"):
            setattr(runtime_cls, "execution_info", None)
        if not hasattr(runtime_cls, "server_info"):
            setattr(runtime_cls, "server_info", None)


_patch_langgraph_runtime()

