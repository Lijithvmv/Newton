import { useEffect, useState } from "react";
import {
  fetchComponents,
  fetchKnowledge,
  fetchMemory,
  fetchRuns,
  fetchSavings,
  fetchSkills,
  fetchWiki,
  type RunActivity,
  type Savings,
} from "../api";
import type { Mode } from "../types";
import { Icon } from "./icons";
import type { View } from "./Sidebar";

// One-click starts — jump into the workspace in a given mode. First is the dark primary CTA.
const STARTS: { mode: Mode; icon: string; label: string }[] = [
  { mode: "task", icon: "terminal", label: "New code task" },
  { mode: "project", icon: "box", label: "Build a project" },
  { mode: "chat", icon: "chat", label: "Ask a question" },
  { mode: "author", icon: "pencil", label: "Draft a doc" },
  { mode: "report", icon: "file", label: "Report" },
];

/** Home / Overview — a card grid that surfaces everything Newton has (savings, memory gate, skills,
 *  components, notebook, wiki, model) with live counts, each linking into its panel. */
interface OvCard {
  key: string;
  icon: string;
  tint: string;
  title: string;
  desc: string;
  label: string;
  value: string;
  go?: View;
  highlight?: boolean;
}

export function Overview({
  model,
  onOpen,
  onStart,
}: {
  model: string | null;
  onOpen: (v: View) => void;
  onStart: (m: Mode) => void;
}) {
  const [s, setS] = useState<Savings | null>(null);
  const [mem, setMem] = useState(0);
  const [skills, setSkills] = useState(0);
  const [comp, setComp] = useState({ done: 0, total: 0 });
  const [notes, setNotes] = useState(0);
  const [wiki, setWiki] = useState(0);
  const [recent, setRecent] = useState<any[]>([]);
  const [runs, setRuns] = useState<RunActivity[]>([]);

  useEffect(() => {
    // Runs poll live so an in-progress build updates in the feed; the rest load once.
    const loadRuns = () => fetchRuns().then(setRuns);
    loadRuns();
    const id = setInterval(loadRuns, 3000);
    fetchSavings().then(setS);
    fetchMemory().then((e) => {
      setMem(e.length);
      setRecent(e.slice(0, 6)); // newest first (the API reverses)
    });
    fetchSkills().then((f) => setSkills(f.length));
    fetchComponents().then((c) =>
      setComp({ done: c.filter((x) => x.status === "done").length, total: c.length })
    );
    fetchKnowledge().then((f) => setNotes(f.length));
    fetchWiki().then((f) => setWiki(f.length));
    return () => clearInterval(id);
  }, []);

  const dollars = !s
    ? "$0.00"
    : s.saved_usd >= 0.01
    ? `$${s.saved_usd.toFixed(2)}`
    : `$${s.saved_usd.toFixed(4)}`;

  const cards: OvCard[] = [
    { key: "ws", icon: "terminal", tint: "accent", title: "Workspace", go: "workspace",
      desc: "Ask, Code, Build, Author and Report — where Newton plans, edits and verifies your work.",
      label: "Modes", value: "5 modes" },
    { key: "sv", icon: "zap", tint: "good", title: "Cloud cost avoided",
      desc: "What Newton's local, free inference would have cost on a cloud API — its zero-token-cost edge.",
      label: `vs ${s?.baseline_model ?? "cloud"}`, value: `${s?.estimated ? "≈ " : ""}${dollars} saved` },
    { key: "mem", icon: "database", tint: "warn", title: "Memory", go: "memory",
      desc: "Cross-session memory with provenance trust and time-decay, so Newton recalls the right context.",
      label: "Remembered", value: `${mem}` },
    { key: "sk", icon: "list", tint: "accent", title: "Skills", go: "skills",
      desc: "Reusable procedure playbooks the loop follows on matching tasks — add your own, or let Newton learn them.",
      label: "Playbooks", value: `${skills}` },
    { key: "cmp", icon: "box", tint: "accent", title: "Components", go: "components",
      desc: "The engine's building blocks — conductors, whole-repo retrieval, memory, wiki and reports.",
      label: "Built", value: `${comp.done} / ${comp.total}` },
    { key: "mc", icon: "book", tint: "accent", title: "Mission Control", go: "knowledge",
      desc: "The project notebook — progress, decisions and architecture, kept current every session.",
      label: "Notes", value: `${notes}` },
    { key: "wk", icon: "layers", tint: "violet", title: "Wiki", go: "wiki",
      desc: "Curated knowledge pages Newton writes for itself when it learns a reusable pattern.",
      label: "Pages", value: `${wiki}` },
    { key: "md", icon: "settings", tint: "slate", title: "Local model",
      desc: "Newton runs entirely on your machine — no cloud, no API keys, fully offline and private.",
      label: "Engine", value: model ?? "—" },
  ];

  const body = (c: OvCard) => (
    <>
      <div className="ovtop">
        <span className={`ovicon t-${c.tint}`}><Icon name={c.icon} size={17} /></span>
        <h3>{c.title}</h3>
      </div>
      <p className="ovdesc">{c.desc}</p>
      <div className="ovstat">
        <span className="ovlabel">{c.label}</span>
        <span className="ovval">{c.value}</span>
      </div>
      {c.go && <div className="ovgo">Open →</div>}
    </>
  );

  const modeLabel: Record<string, string> = {
    chat: "Ask", task: "Code", project: "Build", author: "Author", report: "Report",
  };
  // In-progress + lately-finished runs first (they poll live), then older remembered tasks.
  const activity = [
    ...runs.map((r) => ({
      key: "r" + r.id,
      status: r.status,
      title: r.task || `${modeLabel[r.mode] ?? r.mode} run`,
      sub: `${modeLabel[r.mode] ?? r.mode}${
        r.status === "running" ? " · in progress" : r.status === "failed" ? " · failed" : ""
      }`,
      onClick: () => onOpen("workspace"),
    })),
    ...recent.map((e, i) => ({
      key: "m" + i,
      status: "mem",
      title: e.request ?? e.text ?? "Completed task",
      sub: e.files?.length ? e.files.join(" · ") : "remembered",
      onClick: () => onOpen("memory"),
    })),
  ].slice(0, 8);

  return (
    <div className="panel">
      <div className="panelhead">
        <Icon name="list" size={19} />
        <h2>Overview</h2>
        <span className="psub">everything Newton has, at a glance — fully local</span>
      </div>
      <div className="panelbody">
        <div className="qstart">
          <div className="qslabel"><Icon name="zap" size={13} /> Start working</div>
          <div className="qsrow">
            {STARTS.map((q, i) => (
              <button key={q.mode} className={`qsbtn ${i === 0 ? "primary" : ""}`} onClick={() => onStart(q.mode)}>
                <Icon name={q.icon} size={16} /> {q.label}
              </button>
            ))}
          </div>
        </div>

        <div className="ovgrid">
          {cards.map((c) =>
            c.go ? (
              <button key={c.key} className={`ovcard link ${c.highlight ? "hot" : ""}`} onClick={() => onOpen(c.go!)}>
                {body(c)}
              </button>
            ) : (
              <div key={c.key} className="ovcard">{body(c)}</div>
            )
          )}
        </div>

        <div className="recent">
          <div className="recthead"><Icon name="list" size={14} /> Recent activity</div>
          {activity.length === 0 ? (
            <div className="recempty">No activity yet — runs appear here live as you start them.</div>
          ) : (
            <div className="reclist">
              {activity.map((a) => (
                <button key={a.key} className="recrow" onClick={a.onClick}>
                  <span className={`recicon ${a.status === "running" ? "run" : a.status === "failed" ? "bad" : ""}`}>
                    <Icon name={a.status === "running" ? "settings" : a.status === "failed" ? "alert" : "check"} size={14} />
                  </span>
                  <span className="recmain">
                    <span className="rectitle">{a.title}</span>
                    <span className="recmeta">
                      {a.status === "running" && <b className="runningtag">running</b>}
                      {a.sub}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
