"""Newton — a fully-local Claude-Code-style project + coding assistant.

Runs entirely on a local LLM served by Ollama. No cloud dependency in the loop:
the engine (aisuite) points at http://localhost:11434, reads markdown for context,
edits code, runs the project, and drafts reports from the project folder.

Components (all local): aisuite (engine) · Ollama (brain) · markdown context ·
QMD (search) · Graphify (code graph) · LLM-wiki · Mem0 (memory) · MarkItDown (intake).
"""

__version__ = "0.1.0"
