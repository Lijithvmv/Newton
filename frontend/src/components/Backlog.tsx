import type { BacklogTask, TaskStatus } from "../types";
import { Icon } from "./icons";

/** The Plan — the loop engine's dependency-ordered steps, each turning from a number into a
 *  check (or a cross) as it completes. The heart of the Build experience. */
export function Backlog({
  tasks,
  status,
}: {
  tasks: BacklogTask[];
  status: Record<string, TaskStatus>;
}) {
  const done = tasks.filter((t) => (status[t.id] ?? "pending") === "done").length;
  const running = tasks.some((t) => (status[t.id] ?? "pending") === "running");

  return (
    <div className="plan">
      <div className="plan-head">
        <Icon name="list" size={14} />
        <span className="plan-title">Plan</span>
        <span className="plan-count">
          {done} / {tasks.length} steps
        </span>
        {running && <span className="plan-live">building…</span>}
      </div>
      <ol className="plan-steps">
        {tasks.map((t, i) => {
          const st = status[t.id] ?? "pending";
          return (
            <li className={`plan-step s-${st}`} key={t.id}>
              <span className="step-marker" aria-hidden="true">
                {st === "done" ? (
                  <Icon name="check" size={12} />
                ) : st === "failed" ? (
                  <Icon name="x" size={12} />
                ) : (
                  <span className="step-num">{i + 1}</span>
                )}
              </span>
              <span className="step-title">{t.title}</span>
              {t.file && <code className="step-file">{t.file}</code>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
