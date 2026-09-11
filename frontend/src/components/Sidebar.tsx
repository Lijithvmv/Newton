import { Icon } from "./icons";

export type View = "workspace" | "knowledge" | "wiki" | "skills" | "memory" | "components";

const ITEMS: { view: View; icon: string; label: string }[] = [
  { view: "workspace", icon: "terminal", label: "Workspace" },
  { view: "knowledge", icon: "book", label: "Mission Control" },
  { view: "wiki", icon: "layers", label: "Wiki" },
  { view: "skills", icon: "zap", label: "Skills" },
  { view: "memory", icon: "database", label: "Memory" },
  { view: "components", icon: "box", label: "Components" },
];

export function Sidebar({ view, setView }: { view: View; setView: (v: View) => void }) {
  return (
    <div className="col sidebar">
      <div className="navlabel">Newton</div>
      <nav className="nav">
        {ITEMS.map((it) => (
          <button
            key={it.view}
            className={`navitem ${view === it.view ? "on" : ""}`}
            onClick={() => setView(it.view)}
            aria-label={it.label}
            aria-current={view === it.view ? "page" : undefined}
            title={it.label}
          >
            <Icon name={it.icon} size={16} />
            <span className="nlabel">{it.label}</span>
          </button>
        ))}
      </nav>
      <div className="sidefoot">
        <span className="live" /> Ollama · ready
      </div>
    </div>
  );
}
