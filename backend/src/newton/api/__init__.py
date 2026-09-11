"""Newton's localhost web application — the workspace shell.

A FastAPI backend serves the single-page frontend and runs the Conductor engine in a
worker thread, streaming every stage, context-window composition, plan, diff, and verify
gate to the browser over Server-Sent Events. Approval gates round-trip through the UI:
the engine blocks on a write until the human clicks Approve. Fully local — the only
network call the whole stack makes is to Ollama on localhost.
"""
