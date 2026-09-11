import { useEffect, useState } from "react";
import { browse, type BrowseResult } from "../api";
import { Icon } from "./icons";

/** Link a project folder from the local machine. The backend runs locally, so it browses
 *  the real filesystem: navigate into a folder, then "Link this folder". */
export function ProjectPicker({
  start,
  onPick,
  onClose,
}: {
  start: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [data, setData] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [pathEdit, setPathEdit] = useState(start);

  const go = (path: string) => {
    setLoading(true);
    browse(path).then((d) => {
      setData(d);
      setPathEdit(d.path);   // reflect the resolved path (handles cross-drive, fallbacks)
      setLoading(false);
    });
  };

  // Start from the current project if it's a real path, else the backend default (Desktop/home).
  useEffect(() => {
    go(start && start !== "." ? start : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  return (
    <div className="modalback" onClick={onClose}>
      <div className="modal picker" onClick={(e) => e.stopPropagation()}>
        <div className="pickhead">
          <Icon name="book" size={16} />
          <h3>Link a project folder</h3>
          <button className="pickx" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <input
          className="pickpath mono"
          value={pathEdit}
          onChange={(e) => setPathEdit(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") go(pathEdit.trim()); }}
          spellCheck={false}
          placeholder="type a path (e.g. D:\MyProject) and press Enter"
          aria-label="Folder path"
        />
        <div className="picklist">
          {data?.parent && (
            <button className="pickrow up" onClick={() => go(data.parent!)}>
              <span className="pickup">↑</span> <span>.. (up one level)</span>
            </button>
          )}
          {(data?.dirs ?? []).map((d) => (
            <button key={d.path} className="pickrow" onClick={() => go(d.path)} title={d.path}>
              <Icon name="book" size={14} />
              <span className="pickname">{d.name}</span>
              {d.project && <span className="pjbadge">project</span>}
            </button>
          ))}
          {!loading && data && data.dirs.length === 0 && (
            <div className="pickempty">No sub-folders here.</div>
          )}
        </div>
        <div className="pickfoot">
          <span className="pickhint">
            {data?.project
              ? "This folder looks like a project (git / NEWTON.md / manifest)."
              : "Open a folder, then link it. Newton will index everything inside."}
          </span>
          <button className="pickuse" disabled={!data} onClick={() => data && onPick(data.path)}>
            Link this folder
          </button>
        </div>
      </div>
    </div>
  );
}
