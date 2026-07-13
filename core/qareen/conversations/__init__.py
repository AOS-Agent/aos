"""Conversations — unified model for chat + companion sessions.

See ~/.claude/projects/-Volumes-AOS-X-project-aos/memory/project_chat_companion_merge.md
for the vision. In short:

    Companion IS the thing you talk to. It can be a simple conversation
    (Response only) or a complex multilayered one (Notes, Approvals, Voice,
    Research, Threads). Capabilities toggle mid-conversation. There is no
    "chat vs session" — there is one Conversation with adjustable depth.

This package owns:
  - The Conversation + ConversationMessage data model
  - SQLite-backed ConversationStore (qareen.db: conversations, conversation_messages)
  - Migration from legacy companion_sessions table
"""

from qareen.conversations.store import (
    Conversation,
    ConversationMessage,
    ConversationStore,
    DEFAULT_CAPABILITIES,
    ALL_CAPABILITIES,
)

__all__ = [
    "Conversation",
    "ConversationMessage",
    "ConversationStore",
    "DEFAULT_CAPABILITIES",
    "ALL_CAPABILITIES",
]
