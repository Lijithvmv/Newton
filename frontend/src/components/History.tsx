import { useEffect, useState } from "react";
import { fetchRuns, type RunActivity } from "../api";
import { Icon } from "./icons";

const MODE_LABEL: Record<string, string> = {
  chat: "Ask", task: "Code", project: "Build", author: "Author", report: "Report",
};

function timeAgo(epochSeconds: number): string {
  if (!epochSeconds) return "";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/** History — every past run, persisted so it survives a restart. An interrupted Build (its plan not
 *  fully done) offers a Resume that continues from the checkpoint, not from scratch. */
export function History({ onResume }: { onResume: (r: RunActivity) => void }) {
  const [runs, setRuns] = useState<RunActivity[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const load = () => fetchRuns().then((r) => { setRuns(r); setLoaded(true); });
    load();
    const id = setInterval(load, 3000);         // a running build updates live
    return () => clearInterval(id);
  }, []);

  return (
    <div className="panel">
      <div className="panelhead">
        <Icon name="clock" size={19} />
        <h2>History</h2>
        <span className="psub">every run, kept across restarts — resume an interrupted build</span>
      </div>
      <div className="panelbody">
        {loaded && runs.length === 0 ? (
          <div className="recempty">No runs yet — start one from the Workspace and it appears here.</div>
        ) : (
          <div className="histlist">
            {runs.map((r) => {
              const label = MODE_LABEL[r.mode] ?? r.mode;
              const state = r.status === "running" ? "run" : r.ok ? "ok" : "bad";
              return (
                <div key={r.id} className={`histrow ${state}`}>
                  <span className={`histicon ${state}`}>
                    <Icon name={r.status === "running" ? "settings" : r.ok ? "check" : "alert"} size={15} />
                  </span>
                  <span className="histmain">
                    <span className="histtitle">{r.task || `${label} run`}</span>
                    <span className="histmeta">
                      <b className="histmode">{label}</b>
                      {r.mode === "project" && r.effort ? ` · ${r.effort}` : ""}
                      {r.project && r.project !== "." ? ` · ${r.project}` : ""}
                      {" · "}
                      {r.status === "running" ? <b className="runningtag">running</b>
                        : r.ok ? "done" : "failed"}
                      {r.finished ? ` · ${timeAgo(r.finished)}` : r.started ? ` · ${timeAgo(r.started)}` : ""}
                    </span>
                    {r.evidence && (r.evidence.checks > 0 || r.evidence.artifacts > 0) && (
                      <span className={`proofchip ${r.evidence.verified ? "ok" : "bad"}`}>
                        <Icon name={r.evidence.verified ? "check" : "alert"} size={11} />
                        {r.evidence.checks_passed}/{r.evidence.checks} checks · {r.evidence.artifacts} files
                      </span>
                    )}
                    {r.answer && r.status !== "running" && (
                      <span className="histanswer">{r.answer}</span>
                    )}
                  </span>
                  {r.resumable && (
                    <button className="resumebtn" onClick={() => onResume(r)} title="Continue this build from its checkpoint">
                      <Icon name="terminal" size={13} /> Resume
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
