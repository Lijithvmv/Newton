import { useCallback, useEffect, useRef, useState } from "react";
import { approve, fetchModels, openEvents, startRun } from "./api";
import type {
  BacklogTask,
  ContextPayload,
  DocType,
  GatePayload,
  Mode,
  TaskStatus,
  ThreadItem,
} from "./types";

export const STAGES = ["Understand", "Retrieve", "Plan", "Edit", "Verify", "Remember"];

export interface StageProgress {
  current: string | null;
  done: string[];
}

export interface NewtonState {
  models: string[];
  thread: ThreadItem[];
  stages: StageProgress;
  context: ContextPayload | null;
  taskStatus: Record<string, TaskStatus>;
  gate: { kind: string; path?: string } | null;
  running: boolean;
}

const empty: NewtonState = {
  models: [],
  thread: [],
  stages: { current: null, done: [] },
  context: null,
  taskStatus: {},
  gate: null,
  running: false,
};

export function useNewton() {
  const [state, setState] = useState<NewtonState>(empty);
  const runId = useRef<string | null>(null);
  const es = useRef<EventSource | null>(null);
  const projectShown = useRef(false);
  const answered = useRef(false);

  useEffect(() => {
    fetchModels().then((models) => {
      const pref = models.find((m) => m.includes("qwen")) ?? models[0] ?? null;
      setState((s) => ({ ...s, models }));
      if (pref) setModel(pref);
    });
    return () => es.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [model, setModel] = useState<string | null>(null);

  const push = (item: ThreadItem) =>
    setState((s) => ({ ...s, thread: [...s.thread, item] }));

  const markStage = (name: string) => {
    const base = name.split("·")[0].trim();
    setState((s) => {
      let stages = s.stages;
      if (STAGES.includes(base)) {
        const idx = STAGES.indexOf(base);
        stages = { current: base, done: STAGES.slice(0, idx) };
      }
      return { ...s, stages, thread: [...s.thread, { kind: "stage", name }] };
    });
  };

  const dispatch = useCallback((ch: string, p: any) => {
    switch (ch) {
      case "stage":
        markStage(p as string);
        break;
      case "context":
        setState((s) => ({ ...s, context: p as ContextPayload }));
        break;
      case "note":
        push({ kind: "note", text: p as string });
        break;
      case "plan":
        push({ kind: "plan", steps: p as string[] });
        break;
      case "diff":
        push({ kind: "diff", file: p.file, oldText: p.old, newText: p.new });
        break;
      // "report"/"section" carry the drafted content, but the pre-gate diff already shows
      // it (rendered as markdown for new .md files), so they need no separate thread item.
      case "verify":
        push({ kind: "verify", text: p as string });
        break;
      case "halt":
        push({ kind: "halt", text: p as string });
        break;
      case "gate": {
        const g = p as GatePayload;
        if (g.auto) push({ kind: "note", text: `auto-approved ${g.kind}` });
        else setState((s) => ({ ...s, gate: { kind: g.kind, path: g.args?.path } }));
        break;
      }
      case "blueprint":
        push({ kind: "blueprint", blueprint: p });
        break;
      case "backlog": {
        const tasks = p as BacklogTask[];
        const status: Record<string, TaskStatus> = {};
        tasks.forEach((t) => (status[t.id] = "pending"));
        setState((s) => ({
          ...s,
          taskStatus: status,
          thread: [...s.thread, { kind: "backlog", tasks }],
        }));
        break;
      }
      case "task": {
        if (p.status === "start") {
          setState((s) => ({
            ...s,
            taskStatus: { ...s.taskStatus, [p.id]: "running" },
            stages: { current: null, done: [] },
            thread: [...s.thread, { kind: "taskhead", id: p.id, title: p.title }],
          }));
        } else {
          setState((s) => ({
            ...s,
            taskStatus: { ...s.taskStatus, [p.id]: p.status as TaskStatus },
          }));
        }
        break;
      }
      case "answer":
        answered.current = true;
        push({ kind: "answer", text: p as string });
        break;
      case "project":
        projectShown.current = true;
        push({ kind: "final", ok: p.ok, text: p.answer });
        break;
      case "done":
        if (!projectShown.current && !answered.current && p.answer)
          push({ kind: "final", ok: p.ok, text: p.answer });
        setState((s) => ({ ...s, running: false, gate: null }));
        es.current?.close();
        break;
    }
  }, []);

  const run = useCallback(
    async (task: string, project: string, auto: boolean, mode: Mode, docType?: DocType) => {
      projectShown.current = false;
      answered.current = false;
      setState((s) => ({
        ...s,
        thread: [{ kind: "user", text: task }],
        stages: { current: null, done: [] },
        context: null,
        taskStatus: {},
        gate: null,
        running: true,
      }));
      const id = await startRun({ task, project, model, auto, mode, doc_type: docType });
      runId.current = id;
      const source = openEvents(id);
      es.current = source;
      source.onmessage = (e) => {
        const { ch, payload } = JSON.parse(e.data);
        dispatch(ch, payload);
      };
      source.onerror = () => setState((s) => ({ ...s, running: false }));
    },
    [model, dispatch]
  );

  const decideGate = useCallback(async (decision: boolean) => {
    if (runId.current) await approve(runId.current, decision);
    setState((s) => ({ ...s, gate: null }));
  }, []);

  const stop = useCallback(() => {
    es.current?.close();
    setState((s) => ({
      ...s,
      running: false,
      gate: null,
      thread: [...s.thread, { kind: "halt", text: "Stopped by you." }],
    }));
  }, []);

  return { state, model, setModel, run, decideGate, stop };
}
