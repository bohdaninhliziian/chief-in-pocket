"""Session storage for the conversational agent.

The store is the only place chat sessions live; API routes and MCP tools
go through it and never keep mutable session data of their own. The
in-memory implementation is MVP-only: sessions are lost when the process
restarts. A Redis or PostgreSQL store later slots in behind the same
protocol without touching the agent, tools or API.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from pydantic_ai.messages import ModelMessage

from recipes.sessions.models import SessionState


@dataclass
class ChatSession:
    state: SessionState
    history: list[ModelMessage] = field(default_factory=list)

    def copy(self) -> ChatSession:
        # Deep-copy state, shallow-copy the history list: ModelMessage
        # objects are treated as immutable once recorded.
        return ChatSession(
            state=self.state.model_copy(deep=True),
            history=list(self.history),
        )


class SessionStore(Protocol):
    async def get(self, session_id: str) -> ChatSession | None:
        """Return the session with this id, or None when it does not exist."""
        ...

    async def get_or_create(self, session_id: str | None) -> ChatSession:
        """Return the existing session, or create (and persist) a new one.

        ``None`` generates a fresh session id. An unknown non-None id is
        deliberately adopted as-is — manual MCP use (Inspector, stdio
        clients) supplies its own ids; a chat client resuming an id after
        a process restart therefore silently gets a blank session.
        """
        ...

    async def save(self, session: ChatSession) -> None:
        """Persist the session, replacing any previous version."""
        ...


class InMemorySessionStore:
    """Process-local store; safe for concurrent async requests.

    Volatile by design (MVP): everything is lost on restart.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> ChatSession | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            return session.copy() if session else None

    async def get_or_create(self, session_id: str | None) -> ChatSession:
        async with self._lock:
            if session_id is not None and session_id in self._sessions:
                return self._sessions[session_id].copy()
            new_id = session_id or uuid.uuid4().hex
            session = ChatSession(state=SessionState(session_id=new_id))
            self._sessions[new_id] = session
            return session.copy()

    async def save(self, session: ChatSession) -> None:
        async with self._lock:
            # Copies isolate the stored session from later caller mutations.
            self._sessions[session.state.session_id] = session.copy()
