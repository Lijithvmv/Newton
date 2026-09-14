# Newton

![CI](https://github.com/Lijithvmv/newton/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![Node](https://img.shields.io/badge/node-18+-green)

A fully-local, Claude-Code-style coding and project assistant. Runs entirely on a local
LLM served by Ollama — **no cloud in the loop**. Newton reads your markdown for context,
edits code, runs the project, and builds whole projects from a goal.

Its bet: a small local model can't be trusted with full autonomy the way a frontier model
can, so **the system owns the plan and injects the right context at each stage** — a guided
assembly line, not an autonomous agent. Claude is a map; Newton is GPS.

## Monorepo layout

```
newton/
├── backend/                 Python — the engine + API
│   ├── src/newton/
│   │   ├── conductor/        Task Conductor: staged Understand→…→Remember, gates, self-correction
│   │   ├── project/          Project Conductor: decompose a goal → backlog → delegate to tasks
│   │   ├── index/            Whole-repo context: code-aware chunking, BM25 search, AST code-graph
│   │   ├── api/              FastAPI server: streams the engine to the browser over SSE
│   │   ├── config · llm · context · tools    shared primitives
│   │   └── cli.py            interactive CLI
│   ├── tests/
│   └── pyproject.toml
├── frontend/                React + Vite + TypeScript workspace app
│   └── src/{components,api,useNewton,types}
├── package.json             workspace root
└── README.md
```

## Prerequisites

- **Ollama** running locally (`http://localhost:11434`) with a tool-capable coding model:
  `ollama pull qwen2.5-coder:7b`
- **Python** ≥ 3.10, **Node** ≥ 18

## Setup

```bash
# Backend (from repo root)
python -m venv .venv
.venv/Scripts/pip install -e ./backend       # Windows;  .venv/bin/pip on POSIX

# Frontend
cd frontend && npm install && npm run build   # builds dist/, which the backend serves
```

## Run

```bash
# One process: backend serves the built frontend + API at http://127.0.0.1:8770
newton-server
```

For frontend development with hot reload, run the backend and the Vite dev server side by
side — Vite proxies `/api` to the backend:

```bash
newton-server                 # terminal 1  (API on :8770)
npm run dev                   # terminal 2  (UI on :5173, proxies to :8770)
```

## Command-line entry points

```bash
newton                        # interactive task CLI
newton-server                 # localhost web app + API
newton-project "build a ..."  # run the Project Conductor headless
newton-index . --query "..."  # inspect the repo index (search / --defs / --deps)
```

## Tests

```bash
.venv/Scripts/python -m pytest backend/tests -q
```

## License

[MIT](LICENSE) © Lijith V M
