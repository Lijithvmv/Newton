import type { ContextPayload } from "../types";
import { Icon } from "./icons";

// The signature panel: exactly what the assembler put in the model's window this stage.
export function ContextInspector({ ctx }: { ctx: ContextPayload | null }) {
  const used = ctx?.used ?? 0;
  const budget = ctx?.budget ?? 6000;
  return (
    <div className="col inspector">
      <div className="ih">
        <div className="t"><Icon name="search" size={15} /> Context Inspector</div>
        <div className="s">What's in the model's window — this stage.</div>
        <div className="now">{ctx ? `◉ ${ctx.stage}` : "idle"}</div>
        <div className="meter">
          <div className="mr">
            <span>Window budget</span>
            <span>
              <b>{used}</b> / {budget} tok
            </span>
          </div>
          <div className="track">
            <div
              className="fill"
              style={{ width: `${Math.min(100, (100 * used) / Math.max(1, budget))}%` }}
            />
          </div>
        </div>
      </div>
      <div className="blocks">
        {ctx?.blocks.map(([kind, label, tok, pinned], i) => (
          <div className={`blk ${pinned ? "pin" : ""}`} key={i}>
            <div className="bt">
              <span className={`kind k-${kind}`}>{kind}</span>
              <span className="lab">{label}</span>
              <span className="tok">{tok}t</span>
            </div>
          </div>
        ))}
        {ctx?.dropped.map(([kind, label, tok], i) => (
          <div className="blk drop" key={`d${i}`}>
            <div className="bt">
              <span className={`kind k-${kind}`}>{kind}</span>
              <span className="lab">{label} — dropped</span>
              <span className="tok">{tok}t</span>
            </div>
          </div>
        ))}
      </div>
      <div className="ifoot">
        Newton assembles this per stage — pinned blocks carry across stages; over-budget
        sources are dropped. This is the control a small local model needs to stay on task.
      </div>
    </div>
  );
}
