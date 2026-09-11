# Newton

A fully-local, Claude-Code-style coding and project assistant. Runs entirely on a local
LLM served by Ollama — no cloud dependency in the loop. Newton reads this file for
context, edits code, runs the project, and drafts reports from the project folder.

## Architecture
- `newton/config.py` — settings; all defaults point at localhost. Models are aisuite
  `provider:model` strings resolving to Ollama.
- `newton/llm.py` — the single place we call the model, via aisuite.
- `newton/tools.py` — the tool belt: read/write/edit/list/grep/run/finish, sandboxed to
  the project root. Mutating tools pass an approval gate.
- `newton/context.py` — loads NEWTON.md + docs/ into the agent's context each session.
- `newton/agent.py` — the plan→act→observe loop. Uses a JSON text protocol (not native
  tool-calling) so it works on any Ollama model, gemma3:4b included.
- `newton/cli.py` — dependency-free REPL; owns all I/O and the approval prompts.

## Principles
- Local-first: nothing phones home. Handing work to Claude is a manual, deliberate choice.
- Model-agnostic loop: the text protocol means swapping models is a config change.
- Human in the loop: writes and shell commands are always approved before running.

## Run it

```
python -m newton                 # interactive REPL
python -m newton "your task"     # one-shot task
python -m newton --yes "task"    # auto-approve mutations (careful)
newton-project "build a ..."     # decompose a goal → backlog → build
newton-report status             # read the project → draft docs/STATUS.md
```

## Retrieval & knowledge layers (all built natively — no external services)
- Whole-repo index → BM25 + AST code-graph + local-embedding semantic re-rank (`newton-index`)
- Memory → semantic cross-session recall over `.newton/memory.jsonl`
- Wiki → self-maintaining curated knowledge pages in `wiki/` (`newton-wiki`)
- Reports → project → grounded docs/{STATUS,ARCHITECTURE,PROGRESS}.md (`newton-report`)
- Intake → any PDF/DOCX/PPTX/XLSX → markdown in `raw/`, auto-indexed (`newton-intake`)
- Authoring → draft grounded PRD/architecture/brainstorm/design docs from a topic (`newton-author`)

## Web app
Five workspace modes: Chat/Code (task), Cowork (project), Author (draft docs), Report
(status/architecture/progress); drag-drop or "+ document" to ingest files. Runs stream live
with a stage stepper, Context Inspector, and approval gates. `newton-server` serves it at :8770.

## Next
- Native tool-calling for tool-capable models (optional; JSON protocol works everywhere today).
- Deployment / procurement / tracking flows from the original vision (not yet scoped).
