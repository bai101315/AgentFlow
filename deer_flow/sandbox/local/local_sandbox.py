import errno
import ntpath
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sandbox.local.list_dir import list_dir
from sandbox.sandbox_ import Sandbox
from sandbox.search import GrepMatch, find_glob_matches, find_grep_matches


@dataclass(frozen=True)
class PathMapping:
    """A path mapping from a container path to a local path with optional read-only flag."""
    #  container path  ->  local path
    container_path: str
    local_path: str
    read_only: bool = False

class LocalSandbox(Sandbox):


    def _resolve_paths_in_command(self, command: str) -> str:
        """
        Resolve container paths to local paths in a command string.

        Args:
            command: Command string that may contain container paths

        Returns:
            Command with container paths resolved to local paths
        """

        # 将容器路径解析为本地路径
        import re

        # Sort mappings by length (longest first) for correct prefix matching
        sorted_mappings = sorted(self.path_mappings, key=lambda m: len(m.container_path), reverse=True)

        # Build regex pattern to match all container paths
        # Match container path followed by optional path components
        if not sorted_mappings:
            return command
        
        # Create pattern that matches any of the container paths.
        # The lookahead (?=/|$|...) ensures we only match at a path-segment boundary,
        # preventing /mnt/skills from matching inside /mnt/skills-extra.

        patterns = [re.escape(m.container_path) + r"(?=/|$|[\s\"';&|<>()])(?:/[^\s\"';&|<>()]*)?" for m in sorted_mappings]
        pattern = re.compile("|".join(f"({p})" for p in patterns))

        def replace_match(match: re.Match) -> str:
            matched_path = match.group(0)
            return self._resolve_path(matched_path)

        return pattern.sub(replace_match, command)


    def __init__(self, id: str, path_mappings: list[PathMapping] | None = None):
        """
        Initialize local sandbox with optional path mappings.

        Args:
            id: Sandbox identifier
            path_mappings: List of path mappings with optional read-only flag.
                          Skills directory is read-only by default.
        """
        super().__init__(id)
        self.path_mappings = path_mappings or []

    
    def execute_command(self, command: str) -> str:
        # Resolve container paths in command before execution

        resolved_command = self._resolve_paths_in_command(command)
        shell = self._get_shell()
