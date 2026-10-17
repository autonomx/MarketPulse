"""ChatSessionStore adapter backed by the provider-free Chat repository."""
from __future__ import annotations

from typing import Any

import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from .memory_port import chat_memory_service as default_memory_service
from app.conversation.contracts import AcceptedChatActivityRecorder, LiveVoiceChatPort

from .compaction import InMemoryConversationSummaryRepository
from .history_search import default_history_search_service
from .json_import import import_legacy_chat_json
from .models import ChatSession
from .prompt_store import ChatSessionStore as PromptAssemblyChatSessionStore
from .repository import ChatImportState, InMemoryChatRepository
from app.platform.chat.history_search import HistorySearchService
from app.platform.chat.compaction import ConversationSummaryRepository


class InMemoryChatSessionStore(PromptAssemblyChatSessionStore):
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        legacy_json_path: str | Path | None = None,
        import_legacy: bool = True,
        memory_service_factory: Callable[[], Any] = default_memory_service,
        history_search_factory: Callable[[], HistorySearchService] = default_history_search_service,
        summary_repository_factory: Callable[[], ConversationSummaryRepository] = InMemoryConversationSummaryRepository,
        live_voice_chat_port: LiveVoiceChatPort | None = None,
        live_agent_planner: object | None = None,
        accepted_chat_activity_recorder: AcceptedChatActivityRecorder | None = None,
    ) -> None:
        self.repository = InMemoryChatRepository(db_path)
        self.path = Path(legacy_json_path) if legacy_json_path is not None else Path(":memory:chat")
        self.memory_service_factory = memory_service_factory
        self.history_search_factory = history_search_factory
        self.summary_repository_factory = summary_repository_factory
        self.live_voice_chat_port = live_voice_chat_port
        if live_agent_planner is None:
            from .live_agent_store import default_live_agent_planner

            live_agent_planner = default_live_agent_planner()
        self.live_agent_planner = live_agent_planner
        self.accepted_chat_activity_recorder = accepted_chat_activity_recorder
        self._prompt_context_cache = OrderedDict()
        self._prompt_context_cache_lock = threading.Lock()
        self.import_state: ChatImportState | None = None
        if import_legacy and legacy_json_path is not None:
            self.import_state = import_legacy_chat_json(
                self.repository,
                source_path=legacy_json_path,
            )

    def _load_sessions(self) -> list[ChatSession]:
        return self.repository.load_sessions()

    def _save_session(self, session: ChatSession) -> None:
        self.repository.save_session(session)
