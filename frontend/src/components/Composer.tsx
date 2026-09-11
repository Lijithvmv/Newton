import { useLayoutEffect, useRef, useState } from "react";
import { uploadDocument } from "../api";
import type { DocType, Mode, ReportType } from "../types";
import { Icon } from "./icons";
import { ProjectPicker } from "./ProjectPicker";

const DOC_TYPES: DocType[] = ["prd", "architecture", "brainstorm", "design"];
const REPORT_TYPES: ReportType[] = ["status", "architecture", "progress"];

export function Composer({
  mode,
  project,
  setProject,
  auto,
  setAuto,
  docType,
  setDocType,
  reportType,
  setReportType,
  running,
  onRun,
  onStop,
  onSkills,
}: {
  mode: Mode;
  project: string;
  setProject: (p: string) => void;
  auto: boolean;
  setAuto: (a: boolean) => void;
  docType: DocType;
  setDocType: (d: DocType) => void;
  reportType: ReportType;
  setReportType: (r: ReportType) => void;
  running: boolean;
  onRun: (task: string) => void;
  onStop: () => void;
  onSkills?: () => void;
}) {
  const projName =
    !project || project === "."
      ? "Link folder"
      : project.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "Link folder";
  const [task, setTask] = useState("");
  const [intake, setIntake] = useState<string | null>(null);
  const [dragover, setDragover] = useState(false);
  const [picking, setPicking] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);

  const onFiles = async (files: FileList | null) => {
    if (!files || !files.length) return;
    for (const f of Array.from(files)) {
      const isImage = /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name);
      setIntake(isImage ? `Reading image ${f.name}… (this can take a moment)` : `Reading ${f.name}…`);
      const r = await uploadDocument(f, project);
      setIntake(r.ok ? `✓ Added ${r.dest} (${r.chars} chars) — now searchable` : `✕ ${r.name}: ${r.error}`);
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  // Auto-grow the textarea up to a cap, like a modern chat composer.
  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";                       // shrink to content before measuring
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [task]);

  // In report mode the run's "task" is the report type (no free-text topic); otherwise it's
  // the textarea contents.
  const submit = () => {
    const payload = mode === "report" ? reportType : task.trim();
    if (payload && !running) {
      onRun(payload);
      if (mode !== "report") setTask("");
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragover(false);
    onFiles(e.dataTransfer.files);
  };

  return (
    <div
      className={`composer ${dragover ? "dropping" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragover(true); }}
      onDragLeave={() => setDragover(false)}
      onDrop={onDrop}
    >
      <div className="toolbar">
        <button className="tool" onClick={() => setPicking(true)} title={`Linked folder: ${project}`}>
          <Icon name="folder" size={14} />
          <span className="toolname">{projName}</span>
        </button>
        <input
          ref={fileRef}
          type="file"
          hidden
          multiple
          accept=".pdf,.docx,.pptx,.xlsx,.html,.htm,.md,.txt,.csv,.json,.epub,.png,.jpg,.jpeg,.gif,.webp"
          onChange={(e) => onFiles(e.target.files)}
        />
        <button className="tool" onClick={() => fileRef.current?.click()} title="Attach documents and images">
          <Icon name="paperclip" size={14} /> Attach
        </button>
        {onSkills && (
          <button className="tool" onClick={onSkills} title="View and add skills">
            <Icon name="zap" size={14} /> Skills
          </button>
        )}
        {mode === "author" && (
          <select className="toolsel" value={docType} onChange={(e) => setDocType(e.target.value as DocType)} title="Document type">
            {DOC_TYPES.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        )}
        {mode === "report" && (
          <select className="toolsel" value={reportType} onChange={(e) => setReportType(e.target.value as ReportType)} title="Report type">
            {REPORT_TYPES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        )}
        <span className="sp" />
        {mode !== "chat" && mode !== "report" && (
          <label className="auto">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            <span>auto-approve</span>
          </label>
        )}
      </div>
      {intake && <div className="intakestatus">{intake}</div>}
      <div className={`cbox ${running ? "busy" : ""}`}>
        {mode === "report" ? (
          <div className="reporthint">
            Generate a <b>{reportType}</b> report from the whole project — reads files, git history,
            and the mission-control notes. Press send.
          </div>
        ) : (
          <textarea
            ref={ta}
            rows={1}
            value={task}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={
              mode === "chat"
                ? "Ask a question about your project…  e.g. How does the verify stage work?"
                : mode === "project"
                ? "Describe an application to build…  e.g. a task-tracker API with user auth, sessions, and a SQLite database"
                : mode === "author"
                ? `Topic for the ${docType}…  e.g. a frontend drag-drop upload for document intake`
                : "Describe a coding task…  e.g. Add a --json flag to scripts/wordcount.py"
            }
          />
        )}
        {running ? (
          <button className="stop" onClick={onStop} title="Stop">
            <span className="stopglyph" />
          </button>
        ) : (
          <button
            className="send"
            onClick={submit}
            disabled={mode !== "report" && !task.trim()}
            title="Send"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
      </div>
      <div className="hint">
        Newton stages the work and asks before every write · runs fully local ·{" "}
        <kbd>Enter</kbd> to send
      </div>
      {picking && (
        <ProjectPicker
          start={project}
          onPick={(p) => { setProject(p); setPicking(false); }}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  );
}
