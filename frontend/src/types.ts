// Domain + event types mirroring the backend's emit channels.

export type Mode = "chat" | "task" | "project" | "author" | "report";

// Document types the Author mode can draft.
export type DocType = "prd" | "architecture" | "brainstorm" | "design";

// Report types the Report mode can generate (sent as the run's `task`).
export type ReportType = "status" | "architecture" | "progress";

export interface RunRequest {
  task: string;
  project: string;
  model: string | null;
  auto: boolean;
  mode: Mode;
  doc_type?: DocType;
}

export interface ContextBlock {
  kind: string;
  label: string;
  tokens: number;
  pinned: boolean;
}

export interface ContextPayload {
  stage: string;
  used: number;
  budget: number;
  blocks: [string, string, number, boolean][]; // kind, label, tokens, pinned
  dropped: [string, string, number][];
}

export interface BacklogTask {
  id: string;
  title: string;
  file?: string;
  component?: string;
  depends_on?: string[];
}

export interface BlueprintComponent {
  name: string;
  layer: string;
  concern: string;
  files: string[];
}

export interface Blueprint {
  stack: string;
  summary: string;
  components: BlueprintComponent[];
}

export type TaskStatus = "pending" | "running" | "done" | "failed" | "blocked";

export interface GatePayload {
  kind: string;
  args: { path?: string };
  auto: boolean;
}

// One entry in the conversation stream.
export type ThreadItem =
  | { kind: "user"; text: string }
  | { kind: "stage"; name: string }
  | { kind: "note"; text: string }
  | { kind: "plan"; steps: string[] }
  | { kind: "diff"; file: string; oldText: string; newText: string }
  | { kind: "verify"; text: string }
  | { kind: "halt"; text: string }
  | { kind: "blueprint"; blueprint: Blueprint }
  | { kind: "backlog"; tasks: BacklogTask[] }
  | { kind: "taskhead"; id: string; title: string }
  | { kind: "answer"; text: string }
  | { kind: "final"; ok: boolean; text: string };

// Raw SSE envelope: { ch, payload }.
export interface SseEvent {
  ch: string;
  payload: unknown;
}
