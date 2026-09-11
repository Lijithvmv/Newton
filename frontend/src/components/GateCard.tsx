import { useEffect, useRef } from "react";
import { Icon } from "./icons";

// The approval gate. Renders fixed above the composer so it is always visible, auto-
// focuses Approve, and binds Enter = approve / Esc = reject globally — the fix for the
// "I can't click/Enter the gate" problem.
export function GateCard({
  gate,
  onDecide,
}: {
  gate: { kind: string; path?: string };
  onDecide: (decision: boolean) => void;
}) {
  const okRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    okRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        onDecide(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onDecide(false);
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [gate, onDecide]);

  return (
    <div className="gate">
      <div className="gh">
        <Icon name="alert" size={14} /> Approve {gate.kind}? <span className="mono">{gate.path || ""}</span>
        <span className="ghint">Enter = approve · Esc = reject</span>
      </div>
      <div className="acts">
        <button ref={okRef} className="ok" onClick={() => onDecide(true)}>
          ✓ Approve
        </button>
        <button className="no" onClick={() => onDecide(false)}>
          Reject
        </button>
      </div>
    </div>
  );
}
