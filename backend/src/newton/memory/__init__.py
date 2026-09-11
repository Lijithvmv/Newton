"""Cross-session memory — what Newton remembers between runs.

Built on the same local embedder as the index (no Mem0/Chroma dependency): completed
tasks and decisions are stored with their embeddings and recalled by meaning, so a new
task can be informed by relevant past work. Degrades gracefully to recency when no
embedding model is available.
"""

from .store import Memory, MemoryItem

__all__ = ["Memory", "MemoryItem"]
