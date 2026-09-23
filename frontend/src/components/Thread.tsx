import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { TaskStatus, ThreadItem } from "../types";
import type { StageProgress } from "../useNewton";
import { Backlog } from "./Backlog";
import { Icon } from "./icons";
import { StageStepper } from "./StageStepper";
import { Thinking } from "./Thinking";

function Prism({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M12 3 L21 19 H3 Z" fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}

function Item({ it, status }: { it: ThreadItem; status: Record<string, TaskStatus> }) {
  switch (it.kind) {
    case "user":
      return null; // rendered at the turn level
    case "stage":
      return (
        <div className="ev stage">
          <span className="dot" />
          {it.name}
        </div>
      );
    case "note":
      return <div className="ev note">{it.text}</div>;
    case "plan":
      return (
        <div className="plan">
          <div className="pt">Plan</div>
          <ol>
            {it.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
        </div>
      );
    case "diff":
      // A brand-new markdown file (a drafted report / PRD / brainstorm) reads far better
      // rendered than as a wall of "+" lines. Real edits — and new code files — keep the diff.
      if (!it.oldText && it.file.endsWith(".md")) {
        return (
          <div className="doccard">
            <div className="dochead">
              <Icon name="pencil" size={13} className="pencil" /> {it.file}
            </div>
            <div className="docbody md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{it.newText}</ReactMarkdown>
            </div>
          </div>
        );
      }
      return (
        <div className="diffcard">
          <div className="diffh">
            <Icon name="pencil" size={13} className="pencil" /> {it.file}
          </div>
          <div className="diff">
            {it.oldText
              ? it.oldText.split("\n").map((l, i) => (
                  <div className="d" key={`o${i}`}>{`- ${l}`}</div>
                ))
              : null}
            {it.newText.split("\n").map((l, i) => (
              <div className="a" key={`n${i}`}>{`+ ${l}`}</div>
            ))}
          </div>
        </div>
      );
    case "verify": {
      const fail = /fail/i.test(it.text);
      return (
        <div className={`ev verify ${fail ? "bad" : "good"}`}>
          <Icon name={fail ? "x" : "check"} size={13} />
          <span>{it.text}</span>
        </div>
      );
    }
    case "halt":
      return <div className="ev halt">{it.text}</div>;
    case "blueprint": {
      const bp = it.blueprint;
      return (
        <div className="blueprint">
          <div className="bph">
            <Icon name="layers" size={14} /> Architecture
          </div>
          {bp.stack && <div className="bpstack">{bp.stack}</div>}
          {bp.summary && <div className="bpsummary">{bp.summary}</div>}
          <div className="bpcomps">
            {bp.components.map((c, i) => (
              <div className="bpcomp" key={i}>
                <div className="bpcname">
                  {c.layer && <span className="bplayer">{c.layer}</span>}
                  <b>{c.name}</b>
                  {c.concern && <span className="bpconcern">{c.concern}</span>}
                </div>
                <div className="bpfiles">
                  {c.files.map((f, j) => (
                    <code key={j}>{f}</code>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }
    case "backlog":
      return <Backlog tasks={it.tasks} status={status} />;
    case "taskhead":
      return (
        <div className="taskhead">
          <span className="tid">{it.id}</span> {it.title}
        </div>
      );
    case "answer":
      return (
        <div className="answer md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{it.text}</ReactMarkdown>
        </div>
      );
    case "evidence": {
      const ev = it.evidence;
      if (!ev.checks.length && !ev.artifacts.length) return null;
      return (
        <div className={`evidence ${ev.verified ? "ok" : "bad"}`}>
          <div className="evhead">
            <Icon name={ev.verified ? "check" : "alert"} size={14} />
            <b>Evidence</b>
            <span className="evsum">
              {ev.checks.filter((c) => c.passed).length}/{ev.checks.length} checks · {ev.artifacts.length} files
              {ev.decisions && ev.decisions.length > 0 ? ` · ${ev.decisions.length} decisions` : ""}
              {ev.verified ? " · verified" : " · unverified"}
            </span>
          </div>
          {ev.checks.length > 0 && (
            <ul className="evchecks">
              {ev.checks.map((c) => (
                <li key={c.id} className={c.passed ? "pass" : "fail"}>
                  <Icon name={c.passed ? "check" : "x"} size={12} />
                  <span className="evgoal">{c.goal}</span>
                  {c.detail && <span className="evdetail">{c.detail.split("\n")[0].slice(0, 90)}</span>}
                </li>
              ))}
            </ul>
          )}
          {ev.artifacts.length > 0 && (
            <ul className="evfiles">
              {ev.artifacts.map((a) => (
                <li key={a.file}>
                  <span className="evfile">{a.file}</span>
                  <span className="evhash">{a.sha256.slice(0, 10)}</span>
                  <span className="evbytes">{a.bytes} B</span>
                </li>
              ))}
            </ul>
          )}
          {ev.decisions && ev.decisions.length > 0 && (
            <ul className="evdecisions">
              {ev.decisions.map((d, i) => (
                <li key={i}>
                  <span className="evdname">{d.name.replace(/_/g, " ")}</span>
                  <span className="evdanswer">{d.answer}</span>
                  <span
                    className={`evdconf ${d.confidence >= 0.8 ? "hi" : d.confidence >= 0.5 ? "mid" : "lo"}`}
                    title={`${d.decider} · ${d.reason}`}
                  >
                    {Math.round(d.confidence * 100)}%
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      );
    }
    case "final":
      return <div className={`final ${it.ok ? "ok" : "bad"}`}>{it.text}</div>;
  }
}

// Per-mode guidance shown on the empty workspace — what the mode does + clickable examples.
const GUIDES: Record<string, { title: string; body: string; examples: string[] }> = {
  chat: {
    title: "Ask about your project",
    body: "Ask a question about your code. Newton searches the repo, memory, and notes, and answers — no edits.",
    examples: ["What does the verify stage do?", "Where is the repo index built?", "How does memory recall work?"],
  },
  task: {
    title: "Make a change to your code",
    body: "Describe what you want. Newton plans it, edits the files, runs and reviews the change, and asks before it writes.",
    examples: ["Add a --json flag to the exporter", "Write unit tests for the risk-scoring module", "Refactor the auth middleware to use the token helper"],
  },
  project: {
    title: "Build a feature or a project from a goal",
    body: "Describe what you want built. Newton breaks it into an ordered plan of small steps, then builds each one with exactly the right context from your codebase, verifying and fixing as it goes — and checkpointing after every step, so a long build can be interrupted and resumed.",
    examples: [
      "Add a shopping cart: a Cart class and a checkout function, with tests",
      "A CLI todo app: add, list, and complete tasks with a saved store",
      "A task-tracker API with user auth and a SQLite database",
    ],
  },
  author: {
    title: "Draft a document",
    body: "Pick a doc type below and give a topic. Newton drafts it grounded in your real codebase, section by section.",
    examples: ["A PRD for the document-intake feature", "The architecture for the report generator"],
  },
  report: {
    title: "Generate a report",
    body: "Newton reads the whole project — git history, the repo index, and the mission-control notes — and writes a grounded report. Pick a type below and press send.",
    examples: [],
  },
};

function Guide({ mode, onExample }: { mode: string; onExample: (t: string) => void }) {
  const g = GUIDES[mode] ?? GUIDES.task;
  return (
    <div className="guide">
      <div className="gmark"><Prism size={22} /></div>
      <h3>{g.title}</h3>
      <p>{g.body}</p>
      {g.examples.length > 0 && (
        <>
          <div className="exlabel">Try</div>
          <div className="examples">
            {g.examples.map((ex, i) => (
              <button className="ex" key={i} onClick={() => onExample(ex)}>
                {ex}<span className="arw">→</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

interface Turn {
  user: string;
  items: ThreadItem[];
}

export function Thread({
  thread,
  stages,
  taskStatus,
  running,
  mode,
  onExample,
}: {
  thread: ThreadItem[];
  stages: StageProgress;
  taskStatus: Record<string, TaskStatus>;
  running: boolean;
  mode: string;
  onExample: (t: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight });
  }, [thread, stages, running]);

  // Group the flat stream into turns: each user message starts a turn; everything after
  // it is Newton's response, so the pipeline reads as one coherent assistant reply.
  const turns: Turn[] = [];
  for (const it of thread) {
    if (it.kind === "user") turns.push({ user: it.text, items: [] });
    else if (turns.length) turns[turns.length - 1].items.push(it);
  }

  return (
    <div className="stream" ref={ref}>
      {thread.length === 0 && <Guide mode={mode} onExample={onExample} key={mode} />}

      {turns.map((turn, ti) => {
        const last = ti === turns.length - 1;
        return (
          <div className="turn" key={ti}>
            <div className="msgU">{turn.user}</div>
            <div className="assistant">
              <div className="ahead">
                <span className="aavatar">
                  <Prism />
                </span>
                <span className="aname">Newton</span>
              </div>
              <div className="abody">
                {turn.items.map((it, i) => (
                  <Item key={i} it={it} status={taskStatus} />
                ))}
                {last && stages.current && <StageStepper stages={stages} />}
                {last && running && <Thinking stage={stages.current} />}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
