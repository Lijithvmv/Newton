"""Ask — a chat mode that answers questions about the project, without editing anything.

Retrieves the relevant code, memory, and wiki for the question and asks the local model to
answer grounded in that context. No stages, no gates, no writes — just a grounded answer.
"""

from .conductor import ChatConductor, ChatResult

__all__ = ["ChatConductor", "ChatResult"]
