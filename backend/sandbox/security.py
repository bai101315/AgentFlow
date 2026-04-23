"""Security helpers for sandbox capability gating."""

from config import get_app_config

_LOCAL_SANDBOX_PROVIDER_MARKERS = (
    "deerflow.sandbox.local:LocalSandboxProvider",
    "deerflow.sandbox.local.local_sandbox_provider:LocalSandboxProvider",
)

# 由于 LocalSandboxProvider 不是安全的沙箱边界，因此已禁用主机 bash 执行。
# 请切换到 AioSandboxProvider 以获得隔离的 bash 访问，或者仅在完全受信任的本地环境中设置 
LOCAL_HOST_BASH_DISABLED_MESSAGE = (
    "Host bash execution is disabled for LocalSandboxProvider because it is not a secure "
    "sandbox boundary. Switch to AioSandboxProvider for isolated bash access, or set "
    "sandbox.allow_host_bash: true only in a fully trusted local environment."
)

# 由于 LocalSandboxProvider 不是安全的沙箱边界，因此已禁用 Bash 子代理
# 请切换到 AioSandboxProvider 以获得隔离的 bash 访问，
# 或者 ""仅在完全受信任的本地环境中设置 "sandbox.allow_host_bash: true
LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE = (
    "Bash subagent is disabled for LocalSandboxProvider because host bash execution is not "
    "a secure sandbox boundary. Switch to AioSandboxProvider for isolated bash access, or "
    "set sandbox.allow_host_bash: true only in a fully trusted local environment."
)

def uses_local_sandbox_provider(config=None) -> bool:
    """Return True when the active sandbox provider is the host-local provider."""
    # 当是本地提供者的话，就返回True；不执行bash
    if config is None:
        config = get_app_config()

    sandbox_cfg = getattr(config, "sandbox", None)
    sandbox_use = getattr(sandbox_cfg, "use", "")
    if sandbox_use in _LOCAL_SANDBOX_PROVIDER_MARKERS:
        return True
    return sandbox_use.endswith(":LocalSandboxProvider") and "deerflow.sandbox.local" in sandbox_use

def is_host_bash_allowed(config=None) -> bool:
    """Return whether host bash execution is explicitly allowed."""
    # 是否允许主机执行bash脚本；返回False的话，就会把bash剔除
    if config is None:
        config = get_app_config()

    sandbox_cfg = getattr(config, "sandbox", None)
    if sandbox_cfg is None:
        return True
    if not uses_local_sandbox_provider(config):
        return True
    return bool(getattr(sandbox_cfg, "allow_host_bash", False))