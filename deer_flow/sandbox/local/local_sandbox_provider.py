import logging
from pathlib import Path

from sandbox.local.local_sandbox import LocalSandbox, PathMapping
from sandbox.sandbox_ import Sandbox
from sandbox.sandbox_provider import SandboxProvider

logger = logging.getLogger(__name__)

_singleton: LocalSandbox | None = None

class LocalSandboxProvider(SandboxProvider):
    def __init__(self):
        """Initialize the local sandbox provider with path mappings."""
        self._path_mappings = self._setup_path_mappings()
    
    def _setup_path_mappings(self) -> list[PathMapping]:
        """
        Setup path mappings for local sandbox.

        Maps container paths to actual local paths, including skills directory
        and any custom mounts configured in config.yaml.

        Returns:
            List of path mappings
        """
        # 构建一个 路径映射（Path Mapping） 列表; 作用是实现 容器/虚拟路径 与 宿主机物理路径 之间的转换，
        # 使得沙箱内部（或 Agent 视角下）的路径访问能够安全地重定向到宿主机的实际目录

        mappings: list[PathMapping] = []

        # Map skills container path to local skills directory
        try:
            from config import get_app_config

            config = get_app_config()
            skills_path = config.skills.get_skills_path()
            container_path = config.skills.container_path

            # Only add mapping if skills directory exists
            # skill映射：
            # 宿主机的实际存储路径（skills_path）及其在沙箱中呈现的虚拟路径（container_path，默认为 /mnt/skills）
            if skills_path.exists():
                mappings.append(
                    PathMapping(
                        container_path=container_path,
                        local_path=str(skills_path),
                        read_only=True,  # Skills directory is always read-only
                    )  
                )

            # Map custom mounts from sandbox config
            # 自定义挂载：它允许用户在 config.yaml 中通过 sandbox.mounts 配置项，将任意的宿主机目录暴露给沙箱。
            _RESERVED_CONTAINER_PREFIXES = [container_path, "/mnt/acp-workspace", "/mnt/user-data"]
            
            sandbox_config = config.sandbox
            if sandbox_config and sandbox_config.mounts:
                for mount in sandbox_config.mounts:
                    host_path = Path(mount.host_path)
                    container_path = mount.container_path.rstrip("/") or "/"

                    # 宿主机路径必须为绝对路径
                    if not host_path.is_absolute():
                        logger.warning(
                            "Mount host_path must be absolute, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
                        continue
                    
                    # 容器内路径必须为绝对路径
                    if not container_path.startswith("/"):
                        logger.warning(
                            "Mount container_path must be absolute, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
                        continue

                    # Reject mounts that conflict with reserved container paths
                    # 容器内路径不能与保留前缀冲突
                    if any(container_path == p or container_path.startswith(p + "/") for p in _RESERVED_CONTAINER_PREFIXES):
                        logger.warning(
                            "Mount container_path conflicts with reserved prefix, skipping: %s",
                            mount.container_path,
                        )
                        continue
                    
                    # Ensure the host path exists before adding mapping
                    # 4. 宿主机路径必须真实存在
                    if host_path.exists():
                        mappings.append(
                            PathMapping(
                                container_path=container_path,
                                local_path=str(host_path.resolve()),
                                read_only=mount.read_only,
                            )
                        )
                    else:
                        logger.warning(
                            "Mount host_path does not exist, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )

        except Exception as e:
            # Log but don't fail if config loading fails
            logger.warning("Could not setup path mappings: %s", e, exc_info=True)
        
        return mappings
    
    def acquire(self, thread_id: str | None = None) -> str:
        global _singleton
        if _singleton is None:
            _singleton = LocalSandbox("local", path_mappings=self._path_mappings)
        return _singleton.id
    
    def get(self, sandbox_id: str) -> Sandbox | None:
        if sandbox_id == "local":
            if _singleton is None:
                self.acquire()
            return _singleton
        return None

    def release(self, sandbox_id: str) -> None:
        # LocalSandbox uses singleton pattern - no cleanup needed.
        # Note: This method is intentionally not called by SandboxMiddleware
        # to allow sandbox reuse across multiple turns in a thread.
        # For Docker-based providers (e.g., AioSandboxProvider), cleanup
        # happens at application shutdown via the shutdown() method.
        pass