#!/usr/bin/env python3
"""A small standalone Hermes-like CLI agent.

This script intentionally does not import this repository's backend code. It
implements the local scaffolding around an agent: sessions, transcripts,
search, memory, exports, and an interactive command menu. The actual "agent"
reply is a simple placeholder so it can later be swapped with a real LLM call.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import json
import os
import sqlite3
import sys
import textwrap
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


APP_NAME = "Hermes Lite"
DEFAULT_HOME = Path.cwd() / ".hermes_lite"
BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
DEFAULT_BACKEND_MODEL = "minimax-m2.5"
DEFAULT_TOOL_GROUPS = "web,file:read,file:write,bash"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def short_id() -> str:
    return uuid.uuid4().hex[:12]


def one_line(text: str, limit: int = 90) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def setup_file_logging() -> None:
    root = logging.root
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.INFO)
    file_handler = logging.FileHandler("debug.log", mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(file_handler)


def strip_reasoning_tags(text: str) -> str:
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def slugify_agent_name(name: str, fallback: str | None = None) -> str:
    slug = re.sub(r"[^A-Za-z0-9-]+", "-", name.strip()).strip("-").lower()
    return slug or fallback or f"agent-{short_id()}"


def parse_tool_groups(toolsets: str) -> list[str] | None:
    groups = [item.strip() for item in toolsets.split(",") if item.strip()]
    return groups or None


def sync_backend_agent_config(agent: "AgentInfo") -> None:
    """Write this CLI agent into backend's custom-agent directory."""
    if not agent.backend_name:
        return
    from config.paths import get_paths

    agent_dir = get_paths().agent_dir(agent.backend_name)
    agent_dir.mkdir(parents=True, exist_ok=True)
    tool_groups = parse_tool_groups(agent.toolsets)
    config_lines = [
        f"name: {agent.backend_name}",
        f"description: {json.dumps(agent.description, ensure_ascii=False)}",
    ]
    if agent.model:
        config_lines.append(f"model: {json.dumps(agent.model, ensure_ascii=False)}")
    if tool_groups is not None:
        config_lines.append("tool_groups:")
        config_lines.extend(f"- {group}" for group in tool_groups)
    (agent_dir / "config.yaml").write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    (agent_dir / "SOUL.md").write_text(agent.system_prompt or "", encoding="utf-8")


@dataclass
class SessionInfo:
    session_id: str
    agent_id: str | None
    title: str
    created_at: str
    updated_at: str
    message_count: int
    archived: int


@dataclass
class AgentInfo:
    agent_id: str
    backend_name: str
    name: str
    description: str
    model: str
    system_prompt: str
    toolsets: str
    created_at: str
    updated_at: str
    active: int


class HermesStore:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.sessions_dir = home / "sessions"
        self.exports_dir = home / "exports"
        self.db_path = home / "state.db"
        self.memory_path = home / "memory.json"

    def setup(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                """
                create table if not exists sessions (
                    session_id text primary key,
                    agent_id text,
                    title text not null,
                    created_at text not null,
                    updated_at text not null,
                    message_count integer not null default 0,
                    archived integer not null default 0
                )
                """
            )
            conn.execute(
                """
                create table if not exists agents (
                    agent_id text primary key,
                    backend_name text not null default '',
                    name text not null,
                    description text not null default '',
                    model text not null default 'minimax-m2.5',
                    system_prompt text not null default '',
                    toolsets text not null default 'web,file:read,file:write,bash',
                    created_at text not null,
                    updated_at text not null,
                    active integer not null default 0
                )
                """
            )
            conn.execute(
                """
                create virtual table if not exists session_search
                using fts5(session_id, role, content, created_at)
                """
            )
            columns = [row["name"] for row in conn.execute("pragma table_info(sessions)").fetchall()]
            if "agent_id" not in columns:
                conn.execute("alter table sessions add column agent_id text")
            agent_columns = [row["name"] for row in conn.execute("pragma table_info(agents)").fetchall()]
            if "backend_name" not in agent_columns:
                conn.execute("alter table agents add column backend_name text not null default ''")
            conn.execute(
                "update agents set model = ? where model = '' or model = 'local-placeholder'",
                (DEFAULT_BACKEND_MODEL,),
            )
            conn.execute(
                "update agents set toolsets = ? where toolsets = '' or toolsets = 'chat,memory,session'",
                (DEFAULT_TOOL_GROUPS,),
            )
            agent_count = conn.execute("select count(*) as count from agents").fetchone()["count"]
            if agent_count == 0:
                ts = now_iso()
                conn.execute(
                    """
                    insert into agents(
                        agent_id, backend_name, name, description, model, system_prompt,
                        toolsets, created_at, updated_at, active
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        short_id(),
                        "default-agent",
                        "Default Agent",
                        "Backend-backed default agent for quick CLI experiments.",
                        DEFAULT_BACKEND_MODEL,
                        "You are a helpful CLI assistant.",
                        DEFAULT_TOOL_GROUPS,
                        ts,
                        ts,
                    ),
                )
            for row in conn.execute("select * from agents where backend_name = ''").fetchall():
                backend_name = slugify_agent_name(row["name"], fallback=f"agent-{row['agent_id']}")
                conn.execute("update agents set backend_name = ? where agent_id = ?", (backend_name, row["agent_id"]))
        if not self.memory_path.exists():
            self.save_memory({"facts": [], "summary": "", "updated_at": now_iso()})
        for agent in self.list_agents():
            sync_backend_agent_config(agent)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def transcript_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def create_session(self, title: str | None = None, agent_id: str | None = None) -> SessionInfo:
        agent = self.get_agent(agent_id) if agent_id else self.get_active_agent()
        if agent is None:
            raise ValueError("No agent available. Create an agent first.")
        session_id = short_id()
        ts = now_iso()
        title = title or f"Session {session_id}"
        with self.connect() as conn:
            conn.execute(
                """
                insert into sessions(session_id, agent_id, title, created_at, updated_at, message_count, archived)
                values (?, ?, ?, ?, ?, 0, 0)
                """,
                (session_id, agent.agent_id, title, ts, ts),
            )
        self.transcript_path(session_id).touch()
        return SessionInfo(session_id, agent.agent_id, title, ts, ts, 0, 0)

    def get_session(self, session_id: str) -> SessionInfo | None:
        with self.connect() as conn:
            row = conn.execute(
                "select * from sessions where session_id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, limit: int = 20, include_archived: bool = False) -> list[SessionInfo]:
        where = "" if include_archived else "where archived = 0"
        with self.connect() as conn:
            rows = conn.execute(
                f"select * from sessions {where} order by updated_at desc limit ?",
                (limit,),
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def create_agent(
        self,
        name: str,
        description: str = "",
        model: str = DEFAULT_BACKEND_MODEL,
        system_prompt: str = "",
        toolsets: str = DEFAULT_TOOL_GROUPS,
        backend_name: str | None = None,
        make_active: bool = True,
    ) -> AgentInfo:
        agent_id = short_id()
        backend_name = slugify_agent_name(backend_name or name, fallback=f"agent-{agent_id}")
        ts = now_iso()
        with self.connect() as conn:
            if make_active:
                conn.execute("update agents set active = 0")
            conn.execute(
                """
                insert into agents(
                    agent_id, backend_name, name, description, model, system_prompt,
                    toolsets, created_at, updated_at, active
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    backend_name,
                    name,
                    description,
                    model or DEFAULT_BACKEND_MODEL,
                    system_prompt,
                    toolsets or DEFAULT_TOOL_GROUPS,
                    ts,
                    ts,
                    1 if make_active else 0,
                ),
            )
        agent = AgentInfo(
            agent_id,
            backend_name,
            name,
            description,
            model or DEFAULT_BACKEND_MODEL,
            system_prompt,
            toolsets or DEFAULT_TOOL_GROUPS,
            ts,
            ts,
            1 if make_active else 0,
        )
        sync_backend_agent_config(agent)
        return agent

    def list_agents(self) -> list[AgentInfo]:
        with self.connect() as conn:
            rows = conn.execute("select * from agents order by active desc, updated_at desc").fetchall()
        return [self._row_to_agent(row) for row in rows]

    def get_agent(self, agent_id: str | None) -> AgentInfo | None:
        if not agent_id:
            return None
        with self.connect() as conn:
            row = conn.execute("select * from agents where agent_id = ?", (agent_id,)).fetchone()
        return self._row_to_agent(row) if row else None

    def get_active_agent(self) -> AgentInfo | None:
        with self.connect() as conn:
            row = conn.execute("select * from agents where active = 1 order by updated_at desc limit 1").fetchone()
            if row is None:
                row = conn.execute("select * from agents order by updated_at desc limit 1").fetchone()
        return self._row_to_agent(row) if row else None

    def select_agent(self, agent_id: str) -> AgentInfo:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_id}")
        with self.connect() as conn:
            conn.execute("update agents set active = 0")
            conn.execute(
                "update agents set active = 1, updated_at = ? where agent_id = ?",
                (now_iso(), agent_id),
            )
        selected = self.get_agent(agent_id)
        if selected is None:
            raise ValueError(f"Unknown agent: {agent_id}")
        return selected

    def update_agent(
        self,
        agent_id: str,
        name: str | None = None,
        description: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        toolsets: str | None = None,
        backend_name: str | None = None,
    ) -> AgentInfo:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_id}")
        with self.connect() as conn:
            conn.execute(
                """
                update agents
                set backend_name = ?, name = ?, description = ?, model = ?, system_prompt = ?,
                    toolsets = ?, updated_at = ?
                where agent_id = ?
                """,
                (
                    slugify_agent_name(backend_name, fallback=agent.backend_name) if backend_name is not None else agent.backend_name,
                    name if name is not None else agent.name,
                    description if description is not None else agent.description,
                    model if model is not None else agent.model,
                    system_prompt if system_prompt is not None else agent.system_prompt,
                    toolsets if toolsets is not None else agent.toolsets,
                    now_iso(),
                    agent_id,
                ),
            )
        updated = self.get_agent(agent_id)
        if updated is None:
            raise ValueError(f"Unknown agent: {agent_id}")
        sync_backend_agent_config(updated)
        return updated

    def delete_agent(self, agent_id: str) -> None:
        agents = self.list_agents()
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_id}")
        if len(agents) <= 1:
            raise ValueError("Cannot delete the last agent.")
        if agent.active:
            raise ValueError("Cannot delete the active agent. Select another agent first.")
        with self.connect() as conn:
            conn.execute("update sessions set agent_id = null where agent_id = ?", (agent_id,))
            conn.execute("delete from agents where agent_id = ?", (agent_id,))

    def append_message(self, session_id: str, role: str, content: str) -> None:
        info = self.get_session(session_id)
        if info is None:
            raise ValueError(f"Unknown session: {session_id}")
        record = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": now_iso(),
        }
        with self.transcript_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
        with self.connect() as conn:
            conn.execute(
                """
                update sessions
                set updated_at = ?, message_count = message_count + 1
                where session_id = ?
                """,
                (record["created_at"], session_id),
            )
            conn.execute(
                """
                insert into session_search(session_id, role, content, created_at)
                values (?, ?, ?, ?)
                """,
                (session_id, role, content, record["created_at"]),
            )

    def read_messages(self, session_id: str) -> list[dict]:
        path = self.transcript_path(session_id)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                messages.append(json.loads(line))
        return messages

    def search(self, query: str, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as conn:
            try:
                return conn.execute(
                    """
                    select session_id, role, content, created_at
                    from session_search
                    where session_search match ?
                    order by rank
                    limit ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                quoted_query = '"' + query.replace('"', '""') + '"'
                return conn.execute(
                    """
                    select session_id, role, content, created_at
                    from session_search
                    where session_search match ?
                    order by rank
                    limit ?
                    """,
                    (quoted_query, limit),
                ).fetchall()

    def export_session(self, session_id: str, target: Path | None = None) -> Path:
        info = self.get_session(session_id)
        if info is None:
            raise ValueError(f"Unknown session: {session_id}")
        messages = self.read_messages(session_id)
        target = target or self.exports_dir / f"{session_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# {info.title}",
            "",
            f"- Session ID: `{info.session_id}`",
            f"- Created: {info.created_at}",
            f"- Updated: {info.updated_at}",
            "",
            "---",
            "",
        ]
        for msg in messages:
            role = msg["role"].title()
            lines.extend([f"## {role}", "", msg["content"], ""])
        target.write_text("\n".join(lines), encoding="utf-8")
        return target

    def delete_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from sessions where session_id = ?", (session_id,))
            conn.execute("delete from session_search where session_id = ?", (session_id,))
        self.transcript_path(session_id).unlink(missing_ok=True)

    def archive_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update sessions set archived = 1, updated_at = ? where session_id = ?",
                (now_iso(), session_id),
            )

    def load_memory(self) -> dict:
        try:
            return json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"facts": [], "summary": "", "updated_at": now_iso()}

    def save_memory(self, memory: dict) -> None:
        memory["updated_at"] = now_iso()
        self.memory_path.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")

    def add_memory_fact(self, fact: str) -> None:
        memory = self.load_memory()
        facts = memory.setdefault("facts", [])
        facts.append({"id": short_id(), "content": fact, "created_at": now_iso()})
        self.save_memory(memory)

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionInfo:
        return SessionInfo(
            session_id=row["session_id"],
            agent_id=row["agent_id"] if "agent_id" in row.keys() else None,
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"],
            archived=row["archived"],
        )

    @staticmethod
    def _row_to_agent(row: sqlite3.Row) -> AgentInfo:
        return AgentInfo(
            agent_id=row["agent_id"],
            backend_name=row["backend_name"],
            name=row["name"],
            description=row["description"],
            model=row["model"],
            system_prompt=row["system_prompt"],
            toolsets=row["toolsets"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            active=row["active"],
        )


class LocalAgent:
    """Placeholder agent.

    Replace `reply()` with a real model call when you are ready. It receives the
    session messages and long-term memory, so the integration point is already
    shaped like a simple chat agent.
    """

    def __init__(self, agent: AgentInfo) -> None:
        self.agent = agent

    def reply(self, user_input: str, messages: list[dict], memory: dict) -> str:
        lowered = user_input.strip().lower()
        if lowered in {"help", "/help"}:
            return command_help()
        if "summary" in lowered or "总结" in lowered:
            return self._summarize(messages)
        facts = memory.get("facts", [])
        memory_hint = f"\n\nI currently remember {len(facts)} long-term fact(s)." if facts else ""
        return (
            f"I am {self.agent.name}, a Hermes-like local CLI agent. "
            f"Model: {self.agent.model}. Toolsets: {self.agent.toolsets}. "
            "I saved your message and can manage sessions, search history, export transcripts, and store memory. "
            "Swap LocalAgent.reply() with an LLM call to make me genuinely intelligent."
            f"{memory_hint}\n\n"
            f"You said: {user_input}"
        )

    def _summarize(self, messages: list[dict]) -> str:
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        if not user_messages:
            return "No user messages yet."
        latest = "\n".join(f"- {one_line(msg, 120)}" for msg in user_messages[-8:])
        return f"Recent user messages:\n{latest}"


class BackendAgent:
    """Thin adapter from this CLI to the real backend lead agent."""

    def __init__(self, agent: AgentInfo, thread_id: str) -> None:
        self.agent = agent
        self.thread_id = thread_id

    def reply(self, user_input: str) -> str:
        return asyncio.run(self.areply(user_input))

    async def areply(self, user_input: str) -> str:
        lowered = user_input.strip().lower()
        if lowered in {"help", "/help"}:
            return command_help()
        if "summary" in lowered or "总结" in user_input:
            user_input = "请总结当前会话。"

        from langchain_core.messages import HumanMessage
        from langgraph.runtime import Runtime

        from backend.agents import make_lead_agent
        from backend.agents.checkpointer import make_checkpointer
        import deer_flow_mcp.cache as mcp_cache

        try:
            await mcp_cache.initialize_mcp_tools()
        except Exception as exc:
            print(f"Warning: Failed to initialize MCP tools: {exc}")
            mcp_cache._mcp_tools_cache = []
            mcp_cache._cache_initialized = True
        config = {
            "configurable": {
                "thread_id": self.thread_id,
                "thinking_enabled": False,
                "is_plan_mode": True,
                "model_name": self.agent.model,
                "agent_name": self.agent.backend_name,
                "subagent_enabled": True,
                "tools_enabled": True,
            }
        }
        config["configurable"]["__pregel_runtime"] = Runtime(context={"thread_id": self.thread_id})
        state = {"messages": [HumanMessage(content=user_input)]}

        async with make_checkpointer() as checkpointer:
            runnable = make_lead_agent(config, checkpointer=checkpointer)
            result = await runnable.ainvoke(state, config=config, context={"thread_id": self.thread_id})

        if result.get("messages"):
            return strip_reasoning_tags(result["messages"][-1].content)
        return ""


def command_help() -> str:
    return textwrap.dedent(
        """
        Commands inside chat:
          /help                 Show this help
          /menu                 Return to the main menu
          /agent                Show current agent
          /sessions             List recent sessions
          /memory               Show memory
          /remember <fact>      Save a long-term memory fact
          /export [path]        Export this session to Markdown
          /archive              Archive this session
          /delete               Delete this session
          /exit                 Quit
        """
    ).strip()


def print_sessions(sessions: list[SessionInfo]) -> None:
    if not sessions:
        print("No sessions found.")
        return
    print("\nRecent sessions")
    print("-" * 88)
    print(f"{'ID':<14} {'Agent':<14} {'Msgs':>5} {'Updated':<22} Title")
    print("-" * 88)
    for s in sessions:
        flag = " [archived]" if s.archived else ""
        agent_id = s.agent_id or "-"
        print(f"{s.session_id:<14} {agent_id:<14} {s.message_count:>5} {s.updated_at:<22} {s.title}{flag}")


def print_agents(agents: list[AgentInfo]) -> None:
    if not agents:
        print("No agents found.")
        return
    print("\nAgents")
    print("-" * 98)
    print(f"{'ID':<14} {'Active':<8} {'Model':<22} {'Toolsets':<24} Name")
    print("-" * 98)
    for agent in agents:
        active = "yes" if agent.active else ""
        print(f"{agent.agent_id:<14} {active:<8} {agent.model:<22} {agent.toolsets:<24} {agent.name}")


def print_agent_detail(agent: AgentInfo) -> None:
    print(
        textwrap.dedent(
            f"""
            Agent Detail
            ============
            ID: {agent.agent_id}
            Backend name: {agent.backend_name}
            Name: {agent.name}
            Active: {"yes" if agent.active else "no"}
            Model: {agent.model}
            Toolsets: {agent.toolsets}
            Description: {agent.description or "-"}
            System prompt:
            {agent.system_prompt or "-"}
            Created: {agent.created_at}
            Updated: {agent.updated_at}
            """
        ).strip()
    )


def chat_loop(store: HermesStore, session_id: str) -> None:
    info = store.get_session(session_id)
    if info is None:
        print(f"Session not found: {session_id}")
        return
    agent_info = store.get_agent(info.agent_id) or store.get_active_agent()
    if agent_info is None:
        print("No agent available. Create an agent first.")
        return
    sync_backend_agent_config(agent_info)
    agent = BackendAgent(agent_info, session_id)

    print(f"\nEntering session: {info.title} ({info.session_id})")
    print(f"Agent: {agent_info.name} ({agent_info.agent_id})")
    print("Type /help for commands.\n")

    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        if not user_input:
            continue

        command, _, arg = user_input.partition(" ")
        command = command.lower()

        if command in {"/exit", "exit", "q", "quit"}:
            print("Bye.")
            return
        if command == "/menu":
            return
        if command == "/help":
            print(command_help())
            continue
        if command == "/sessions":
            print_sessions(store.list_sessions())
            continue
        if command == "/agent":
            print_agent_detail(agent_info)
            continue
        if command == "/memory":
            print(json.dumps(store.load_memory(), indent=2, ensure_ascii=False))
            continue
        if command == "/remember":
            if not arg.strip():
                print("Usage: /remember <fact>")
                continue
            store.add_memory_fact(arg.strip())
            print("Saved memory fact.")
            continue
        if command == "/export":
            target = Path(arg.strip()) if arg.strip() else None
            path = store.export_session(session_id, target)
            print(f"Exported to {path}")
            continue
        if command == "/archive":
            store.archive_session(session_id)
            print("Archived session.")
            return
        if command == "/delete":
            confirm = input("Delete this session permanently? Type DELETE to confirm: ").strip()
            if confirm == "DELETE":
                store.delete_session(session_id)
                print("Deleted session.")
                return
            print("Delete cancelled.")
            continue

        store.append_message(session_id, "user", user_input)
        print("\nHermes is thinking...\n")
        try:
            reply = agent.reply(user_input)
        except Exception as exc:
            reply = f"Backend call failed: {exc}"
        store.append_message(session_id, "assistant", reply)
        print(f"\nHermes > {reply}\n")


def create_agent_menu(store: HermesStore) -> None:
    print("\nCreate Agent")
    print("-" * 32)
    name = input("Name: ").strip()
    if not name:
        print("Name is required.")
        return
    backend_name = input(f"Backend name [{slugify_agent_name(name)}]: ").strip() or slugify_agent_name(name)
    description = input("Description: ").strip()
    model = input(f"Model [{DEFAULT_BACKEND_MODEL}]: ").strip() or DEFAULT_BACKEND_MODEL
    toolsets = input(f"Tool groups [{DEFAULT_TOOL_GROUPS}]: ").strip() or DEFAULT_TOOL_GROUPS
    print("System prompt (blank line to finish):")
    prompt_lines = []
    while True:
        line = input()
        if line == "":
            break
        prompt_lines.append(line)
    make_active = input("Set as active agent? [Y/n]: ").strip().lower() not in {"n", "no"}
    agent = store.create_agent(
        name=name,
        backend_name=backend_name,
        description=description,
        model=model,
        system_prompt="\n".join(prompt_lines),
        toolsets=toolsets,
        make_active=make_active,
    )
    print(f"Created agent: {agent.name} ({agent.agent_id})")


def select_agent_menu(store: HermesStore) -> None:
    print_agents(store.list_agents())
    agent_id = input("Agent ID: ").strip()
    if not agent_id:
        return
    try:
        agent = store.select_agent(agent_id)
        print(f"Active agent: {agent.name} ({agent.agent_id})")
    except ValueError as exc:
        print(exc)


def show_agent_detail_menu(store: HermesStore) -> None:
    print_agents(store.list_agents())
    agent_id = input("Agent ID (blank for active): ").strip()
    agent = store.get_agent(agent_id) if agent_id else store.get_active_agent()
    if agent is None:
        print("Agent not found.")
        return
    print_agent_detail(agent)


def edit_agent_menu(store: HermesStore) -> None:
    print_agents(store.list_agents())
    agent_id = input("Agent ID: ").strip()
    agent = store.get_agent(agent_id)
    if agent is None:
        print("Agent not found.")
        return
    print("Press Enter to keep the current value.")
    backend_name = input(f"Backend name [{agent.backend_name}]: ").strip()
    name = input(f"Name [{agent.name}]: ").strip()
    description = input(f"Description [{agent.description}]: ").strip()
    model = input(f"Model [{agent.model}]: ").strip()
    toolsets = input(f"Toolsets [{agent.toolsets}]: ").strip()
    change_prompt = input("Edit system prompt? [y/N]: ").strip().lower() in {"y", "yes"}
    system_prompt = None
    if change_prompt:
        print("System prompt (blank line to finish):")
        prompt_lines = []
        while True:
            line = input()
            if line == "":
                break
            prompt_lines.append(line)
        system_prompt = "\n".join(prompt_lines)
    try:
        updated = store.update_agent(
            agent_id,
            backend_name=backend_name or None,
            name=name or None,
            description=description if description else None,
            model=model or None,
            system_prompt=system_prompt,
            toolsets=toolsets or None,
        )
        print(f"Updated agent: {updated.name} ({updated.agent_id})")
    except ValueError as exc:
        print(exc)


def delete_agent_menu(store: HermesStore) -> None:
    print_agents(store.list_agents())
    agent_id = input("Agent ID to delete: ").strip()
    if not agent_id:
        return
    confirm = input("Type DELETE to confirm: ").strip()
    if confirm != "DELETE":
        print("Delete cancelled.")
        return
    try:
        store.delete_agent(agent_id)
        print("Deleted agent.")
    except ValueError as exc:
        print(exc)


def main_menu(store: HermesStore) -> None:
    while True:
        active_agent = store.get_active_agent()
        active_label = f"{active_agent.name} ({active_agent.agent_id})" if active_agent else "none"
        # print(
        #     textwrap.dedent(
        #         f"""
        #         {APP_NAME}
        #         {'=' * len(APP_NAME)}
        #         Active agent: {active_label}

        #         1. Create agent
        #         2. Select agent
        #         3. Agent details
        #         4. Edit agent
        #         5. New session with active agent
        #         6. Resume session
        #         7. List sessions
        #         8. Search sessions
        #         9. Export session
        #         10. Memory
        #         11. Delete session
        #         12. Delete agent
        #         13. Quit
        #         """
        #     ).strip()
        # )

        print(
            textwrap.dedent(
                f"""
                {APP_NAME}
                {'=' * len(APP_NAME)}
                Active agent: {active_label}

                1. Create agent
                2. Select agent
                3. Agent details
                4. Edit agent
                5. New session with active agent
                6. Memory
                7. Delete session
                8. Delete agent
                9. Quit
                """
            ).strip()
        )



        choice = input("\nChoose an option: ").strip()
        if choice == "1":
            create_agent_menu(store)
        elif choice == "2":
            select_agent_menu(store)
        elif choice == "3":
            show_agent_detail_menu(store)
        elif choice == "4":
            edit_agent_menu(store)
        elif choice == "5":
            title = input("Title (blank for auto): ").strip() or None
            try:
                session = store.create_session(title, active_agent.agent_id if active_agent else None)
                chat_loop(store, session.session_id)
            except ValueError as exc:
                print(exc)
        elif choice == "6":
            print_sessions(store.list_sessions())
            session_id = input("Session ID: ").strip()
            if session_id:
                chat_loop(store, session_id)
        elif choice == "7":
            print_sessions(store.list_sessions(include_archived=True))
        elif choice == "8":
            query = input("Search query: ").strip()
            if not query:
                continue
            for row in store.search(query):
                print(f"\n[{row['created_at']}] {row['session_id']} {row['role']}")
                print(one_line(row["content"], 180))
        elif choice == "9":
            session_id = input("Session ID: ").strip()
            if session_id:
                try:
                    print(f"Exported to {store.export_session(session_id)}")
                except ValueError as exc:
                    print(exc)
        elif choice == "10":
            memory_menu(store)
        elif choice == "11":
            session_id = input("Session ID to delete: ").strip()
            if session_id:
                confirm = input("Type DELETE to confirm: ").strip()
                if confirm == "DELETE":
                    store.delete_session(session_id)
                    print("Deleted.")
        elif choice == "12":
            delete_agent_menu(store)
        elif choice == "13":
            print("Bye.")
            return
        else:
            print("Unknown option.")


def memory_menu(store: HermesStore) -> None:
    while True:
        print(
            textwrap.dedent(
                """
                Memory
                ======
                1. Show memory
                2. Add fact
                3. Clear memory
                4. Back
                """
            ).strip()
        )
        choice = input("\nChoose an option: ").strip()
        if choice == "1":
            print(json.dumps(store.load_memory(), indent=2, ensure_ascii=False))
        elif choice == "2":
            fact = input("Fact: ").strip()
            if fact:
                store.add_memory_fact(fact)
                print("Saved.")
        elif choice == "3":
            confirm = input("Clear all memory? Type CLEAR to confirm: ").strip()
            if confirm == "CLEAR":
                store.save_memory({"facts": [], "summary": "", "updated_at": now_iso()})
                print("Memory cleared.")
        elif choice == "4":
            return
        else:
            print("Unknown option.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone Hermes-like CLI agent scaffold.")
    parser.add_argument("--home", type=Path, default=Path(os.getenv("HERMES_LITE_HOME", DEFAULT_HOME)))
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("menu", help="Open interactive menu")
    new_parser = sub.add_parser("new", help="Create and enter a new session")
    new_parser.add_argument("--title", default=None)
    new_parser.add_argument("--agent-id", default=None)

    resume_parser = sub.add_parser("resume", help="Resume a session")
    resume_parser.add_argument("session_id")

    sub.add_parser("agents", help="List agents")

    list_parser = sub.add_parser("list", help="List sessions")
    list_parser.add_argument("--all", action="store_true", help="Include archived sessions")

    search_parser = sub.add_parser("search", help="Search session transcripts")
    search_parser.add_argument("query")

    export_parser = sub.add_parser("export", help="Export a session to Markdown")
    export_parser.add_argument("session_id")
    export_parser.add_argument("--out", type=Path, default=None)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_file_logging()
    args = parse_args(argv or sys.argv[1:])
    store = HermesStore(args.home)
    store.setup()

    command = args.command or "menu"
    if command == "menu":
        main_menu(store)
    elif command == "new":
        session = store.create_session(args.title, args.agent_id)
        chat_loop(store, session.session_id)
    elif command == "resume":
        chat_loop(store, args.session_id)
    elif command == "agents":
        print_agents(store.list_agents())
    elif command == "list":
        print_sessions(store.list_sessions(include_archived=args.all))
    elif command == "search":
        for row in store.search(args.query):
            print(f"[{row['created_at']}] {row['session_id']} {row['role']}: {one_line(row['content'], 180)}")
    elif command == "export":
        print(store.export_session(args.session_id, args.out))
    else:
        raise AssertionError(f"Unhandled command: {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
