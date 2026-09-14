import { Icon } from "./icons";

export type View = "overview" | "workspace" | "knowledge" | "wiki" | "skills" | "memory" | "components";

type Item = { view: View; icon: string; label: string };

// Grouped nav, like the reference SaaS sidebars (CloseCRM / Opera).
const GROUPS: { label?: string; items: Item[] }[] = [
  {
    items: [
      { view: "overview", icon: "list", label: "Overview" },
      { view: "workspace", icon: "terminal", label: "Workspace" },
    ],
  },
  {
    label: "Knowledge",
    items: [
      { view: "knowledge", icon: "book", label: "Mission Control" },
      { view: "wiki", icon: "layers", label: "Wiki" },
      { view: "skills", icon: "zap", label: "Skills" },
      { view: "memory", icon: "database", label: "Memory" },
    ],
  },
  {
    label: "System",
    items: [{ view: "components", icon: "box", label: "Components" }],
  },
];

export function Sidebar({ view, setView }: { view: View; setView: (v: View) => void }) {
  return (
    <div className="col sidebar">
      <div className="navbrand">Newton</div>
      {GROUPS.map((g, gi) => (
        <div className="navgroup" key={gi}>
          {g.label && <div className="navlabel">{g.label}</div>}
          <nav className="nav">
            {g.items.map((it) => (
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
        </div>
      ))}
      <div className="sidefoot">
        <span className="live" /> Ollama · ready
      </div>
    </div>
  );
}
