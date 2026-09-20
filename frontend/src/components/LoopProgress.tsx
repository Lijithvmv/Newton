import type { LoopBudget, TaskStatus } from "../types";
import { Icon } from "./icons";

/** The loop's progress + BUDGET — so the user sees a build is bounded, not open-ended (loopx's
 *  "quota" idea). Progress (N of M steps) is derived from the plan + step events; the budget line is
 *  the effort ceiling the engine reports, made explicit instead of hidden in module constants. */
export function LoopProgress({
  taskStatus,
  budget,
  running,
}: {
  taskStatus: Record<string, TaskStatus>;
  budget: LoopBudget | null;
  running: boolean;
}) {
  const ids = Object.keys(taskStatus);
  const total = ids.length;
  if (total === 0 && !budget) return null;

  const done = ids.filter((k) => taskStatus[k] === "done").length;
  const failed = ids.filter((k) => taskStatus[k] === "failed" || taskStatus[k] === "blocked").length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div className="loopbar">
      <div className="loopbartop">
        <span className="loopcount">
          <Icon name="box" size={13} />
          {total > 0 ? <>Step <b>{done}</b> of <b>{total}</b></> : "Planning…"}
          {failed > 0 && <span className="loopfailed"> · {failed} to fix</span>}
        </span>
        {budget && (
          <span className="loopbudget" title="The loop is bounded — it won't run forever">
            <b>{budget.effort}</b> · up to {budget.attempts} tries/step · {budget.repairs} repairs · {budget.replans} replans
          </span>
        )}
      </div>
      {total > 0 && (
        <div className="looptrack">
          <div className={`loopfill ${running ? "run" : ""}`} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
