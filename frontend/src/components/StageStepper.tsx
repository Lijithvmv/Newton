import { Fragment } from "react";
import { STAGES, type StageProgress } from "../useNewton";

export function StageStepper({ stages }: { stages: StageProgress }) {
  return (
    <div className="stages">
      {STAGES.map((s, i) => {
        const cls = stages.done.includes(s)
          ? "done"
          : stages.current === s
          ? "active"
          : "pending";
        return (
          <Fragment key={s}>
            <div className={`stg ${cls}`}>
              <span className="node">{cls === "done" ? "✓" : cls === "active" ? "◉" : i + 1}</span>
              <span className="lbl">{s}</span>
            </div>
            {i < STAGES.length - 1 && (
              <span className="stg">
                <span className="bar" />
              </span>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
