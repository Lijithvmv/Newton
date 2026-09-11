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
