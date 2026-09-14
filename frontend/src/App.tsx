import { useEffect, useState } from "react";
import { Composer } from "./components/Composer";
import { ContextInspector } from "./components/ContextInspector";
import { GateCard } from "./components/GateCard";
import { Overview } from "./components/Overview";
import { ComponentsPanel, KnowledgePanel, MemoryPanel, SkillsPanel, WikiPanel } from "./components/Panels";
import { Sidebar, type View } from "./components/Sidebar";
import { Thread } from "./components/Thread";
import { TopBar } from "./components/TopBar";
import type { DocType, EffortLevel, Mode, ReportType } from "./types";
import { useNewton } from "./useNewton";

export default function App() {
  const { state, model, setModel, run, decideGate, stop } = useNewton();
  const [mode, setMode] = useState<Mode>("task");
  const [project, setProject] = useState<string>(() => {
    try { return localStorage.getItem("newton.project") || "."; } catch { return "."; }
  });
  useEffect(() => {
    try { localStorage.setItem("newton.project", project); } catch { /* private mode */ }
  }, [project]);
  const [auto, setAuto] = useState(false);
  const [docType, setDocType] = useState<DocType>("prd");
  const [reportType, setReportType] = useState<ReportType>("status");
  const [effort, setEffort] = useState<EffortLevel>("normal");
  const [view, setView] = useState<View>("overview");

  // Choosing a mode always returns to the workspace.
  const chooseMode = (m: Mode) => {
    setMode(m);
    setView("workspace");
  };

  return (
    <>
      <TopBar mode={mode} setMode={chooseMode} models={state.models} model={model} setModel={setModel} />
      <div className="body">
        <Sidebar view={view} setView={setView} />
        <div className="main">
          {view === "workspace" && (
            <>
              <div className="col thread">
                <div className="thead">
                  <h2>Workspace</h2>
                  <span className="badge">
                    {mode === "chat" ? "Ask" : mode === "project" ? "Build" : mode === "author" ? "Author" : mode === "report" ? "Report" : "Code"}
                  </span>
                </div>
                <Thread
                  thread={state.thread}
                  stages={state.stages}
                  taskStatus={state.taskStatus}
                  running={state.running}
                  mode={mode}
                  onExample={(t) => run(t, project, auto, mode, docType, effort)}
                />
                {state.gate && <GateCard gate={state.gate} onDecide={decideGate} />}
                <Composer
                  mode={mode}
                  project={project}
                  setProject={setProject}
                  auto={auto}
                  setAuto={setAuto}
                  docType={docType}
                  setDocType={setDocType}
                  reportType={reportType}
                  setReportType={setReportType}
                  effort={effort}
                  setEffort={setEffort}
                  running={state.running}
                  onRun={(t) => run(t, project, auto, mode, docType, effort)}
                  onStop={stop}
                  onSkills={() => setView("skills")}
                />
              </div>
              <ContextInspector ctx={state.context} />
            </>
          )}
          {view !== "workspace" && (
            <div className="viewfade" key={view}>
              {view === "overview" && <Overview model={model} onOpen={setView} onStart={chooseMode} />}
              {view === "knowledge" && <KnowledgePanel />}
              {view === "wiki" && <WikiPanel />}
              {view === "skills" && <SkillsPanel />}
              {view === "memory" && <MemoryPanel />}
              {view === "components" && <ComponentsPanel />}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
