// Thin client over the Newton backend API. In dev, Vite proxies these to :8770.

import type { RunRequest } from "./types";

export async function fetchModels(): Promise<string[]> {
  try {
    const r = await fetch("/api/models");
    const d = await r.json();
    return (d.models as string[]) ?? [];
  } catch {
    return [];
  }
}

export async function startRun(req: RunRequest): Promise<string> {
  const r = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  const d = await r.json();
  return d.run_id as string;
}

export interface Savings {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  baseline_model: string;
  saved_usd: number;
  estimated: boolean;
  note: string;
}
/** The cloud-cost-avoided meter — what local (free) inference would have cost on a cloud API. */
export async function fetchSavings(): Promise<Savings | null> {
  try {
    const r = await fetch("/api/savings");
    return (await r.json()) as Savings;
  } catch {
    return null;
  }
}

export interface IntakeResult {
  ok: boolean;
  dest?: string;
  chars?: number;
  name?: string;
  error?: string;
}

export async function uploadDocument(file: File, project: string): Promise<IntakeResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("project", project);
  try {
    const r = await fetch("/api/intake", { method: "POST", body: form });
    return (await r.json()) as IntakeResult;
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function approve(runId: string, decision: boolean): Promise<void> {
  await fetch(`/api/approve/${runId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
}

export function openEvents(runId: string): EventSource {
  return new EventSource(`/api/events/${runId}`);
}

export interface KnowledgeFile {
  path: string;
  name: string;
}
export async function fetchKnowledge(): Promise<KnowledgeFile[]> {
  const r = await fetch("/api/knowledge");
  return (await r.json()).files ?? [];
}
export async function fetchKnowledgeFile(path: string): Promise<string> {
  const r = await fetch(`/api/knowledge/${path}`);
  return (await r.json()).content ?? "";
}

export async function fetchWiki(): Promise<KnowledgeFile[]> {
  const r = await fetch("/api/wiki");
  return (await r.json()).files ?? [];
}
export async function fetchWikiPage(name: string): Promise<string> {
  const r = await fetch(`/api/wiki/${name}`);
  return (await r.json()).content ?? "";
}

export interface BrowseEntry {
  name: string;
  path: string;
  project: boolean;
}
export interface BrowseResult {
  path: string;
  parent: string | null;
  project: boolean;
  home: string;
  dirs: BrowseEntry[];
}
export async function browse(path: string): Promise<BrowseResult> {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  const r = await fetch(`/api/browse${q}`);
  return await r.json();
}

export interface SkillFile {
  path: string;
  name: string;
  desc?: string;
}
export async function fetchSkills(): Promise<SkillFile[]> {
  const r = await fetch("/api/skills");
  return (await r.json()).files ?? [];
}
export async function fetchSkill(name: string): Promise<string> {
  const r = await fetch(`/api/skills/${name}`);
  return (await r.json()).content ?? "";
}
export async function createSkill(
  name: string,
  description: string,
  body: string
): Promise<{ ok: boolean; file?: string; error?: string }> {
  const r = await fetch("/api/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description, body }),
  });
  return r.json();
}

export interface Component {
  name: string;
  status: string;
  desc: string;
}
export async function fetchComponents(): Promise<Component[]> {
  const r = await fetch("/api/components");
  return (await r.json()).components ?? [];
}

export async function fetchMemory(): Promise<any[]> {
  const r = await fetch("/api/memory");
  return (await r.json()).entries ?? [];
}

export interface RunActivity {
  id: string;
  task: string;
  mode: string;
  status: "running" | "done" | "failed" | string;
  started: number;
  ok: boolean;
  project?: string;
  effort?: string;
  model?: string | null;
  resumable?: boolean;        // an interrupted Build that can be continued from its checkpoint
  answer?: string;
  finished?: number;
  evidence?: { verified: boolean; checks: number; checks_passed: number; artifacts: number; decisions?: number } | null;
}
/** Recent runs, newest first — persisted, so history survives a restart. */
export async function fetchRuns(): Promise<RunActivity[]> {
  try {
    const r = await fetch("/api/runs");
    return (await r.json()).runs ?? [];
  } catch {
    return [];
  }
}

export interface ProposedMemory {
  text: string;
  origin: string;
  status: string;
  ts?: number;
  files?: string[];
  symbols?: string[];
}
/** Quarantined memories (untrusted origin) awaiting the operator's activate/reject decision. */
export async function fetchProposedMemory(): Promise<ProposedMemory[]> {
  try {
    const r = await fetch("/api/memory/proposed");
    return (await r.json()).entries ?? [];
  } catch {
    return [];
  }
}
async function memoryDecision(action: "activate" | "reject" | "verify", match: string) {
  const r = await fetch(`/api/memory/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ match }),
  });
  return r.json() as Promise<{ ok: boolean; detail?: string }>;
}
/** Pass the activation gate: a quarantined memory becomes eligible for recall. */
export const activateMemory = (match: string) => memoryDecision("activate", match);
/** Decline a memory: never recalled (kept on disk for audit). */
export const rejectMemory = (match: string) => memoryDecision("reject", match);
/** Promote a memory to 'verified' — the operator confirming a fact so it outranks the rest. */
export const verifyMemory = (match: string) => memoryDecision("verify", match);
