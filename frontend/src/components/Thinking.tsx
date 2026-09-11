// Shown while a run is active and Newton is waiting on the local model. Local calls take
// many seconds, so a clear, animated "working" signal is essential — without it the app
// looks frozen.
export function Thinking({ stage }: { stage: string | null }) {
  const label = stage ? `Working · ${stage}` : "Working…";
  return (
    <div className="thinking" role="status" aria-live="polite">
      <span className="avatar">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
          <path d="M12 3 L21 19 H3 Z" fill="none" stroke="currentColor" strokeWidth="1.8" />
        </svg>
      </span>
      <span className="dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="tlabel">{label}</span>
    </div>
  );
}
