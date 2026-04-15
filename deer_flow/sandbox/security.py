

LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE = (
    "Bash subagent is disabled for LocalSandboxProvider because host bash execution is not "
    "a secure sandbox boundary. Switch to AioSandboxProvider for isolated bash access, or "
    "set sandbox.allow_host_bash: true only in a fully trusted local environment."
)

def is_host_bash_allowed(config=None) -> bool:
    """Return whether host bash execution is explicitly allowed."""
    # if config is None:
    #     config = get_app_config()

    # sandbox_cfg = getattr(config, "sandbox", None)
    # if sandbox_cfg is None:
    #     return True
    # if not uses_local_sandbox_provider(config):
    #     return True
    # return bool(getattr(sandbox_cfg, "allow_host_bash", False))
    return False