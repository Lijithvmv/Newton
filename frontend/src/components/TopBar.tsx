import type { Mode } from "../types";

export function TopBar({
  mode,
  setMode,
  models,
  model,
  setModel,
}: {
  mode: Mode;
  setMode: (m: Mode) => void;
  models: string[];
  model: string | null;
  setModel: (m: string) => void;
}) {
  // Five real, distinct modes — each tab maps to its own backend behaviour.
  const TABS: { label: string; mode: Mode; hint: string }[] = [
    { label: "Ask", mode: "chat", hint: "Ask questions about your code — no edits" },
    { label: "Code", mode: "task", hint: "Make a focused change to your code" },
    { label: "Build", mode: "project", hint: "Build a whole project from a goal" },
    { label: "Author", mode: "author", hint: "Draft a PRD, architecture, brainstorm or design" },
    { label: "Report", mode: "report", hint: "Generate a status / architecture / progress report" },
  ];
  return (
    <div className="top">
      <div className="brand">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
          <path d="M12 3 L21 19 H3 Z" fill="var(--accent-soft)" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
          <path d="M12 3 L12 19" stroke="currentColor" strokeWidth="1.1" opacity="0.55" />
        </svg>
        Newton
      </div>
      <div className="modes">
        {TABS.map((t) => (
          <button
            key={t.mode}
            className={mode === t.mode ? "on" : ""}
            onClick={() => setMode(t.mode)}
            title={t.hint}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="sp" />
      <span className="chip">
        <span className="live" />{" "}
        <select className="mono modelsel" value={model ?? ""} onChange={(e) => setModel(e.target.value)}>
          {(models.length ? models : ["(no models)"]).map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </span>
    </div>
  );
}
