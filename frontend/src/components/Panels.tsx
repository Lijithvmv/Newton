import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  createSkill,
  fetchComponents,
  fetchKnowledge,
  fetchKnowledgeFile,
  fetchMemory,
  fetchSkill,
  fetchSkills,
  fetchWiki,
  fetchWikiPage,
  type Component,
  type KnowledgeFile,
  type SkillFile,
} from "../api";
import { Icon } from "./icons";

/** Mission Control — browse the project's markdown notebook, rendered. */
export function KnowledgePanel() {
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [content, setContent] = useState("");

  useEffect(() => {
    fetchKnowledge().then((f) => {
      setFiles(f);
      const first = f.find((x) => x.name === "progress") ?? f[0];
      if (first) select(first.path);
    });
  }, []);
  const select = (p: string) => {
    setActive(p);
    fetchKnowledgeFile(p).then(setContent);
  };

  return (
    <div className="panel knowledge">
      <div className="panelhead">
        <Icon name="book" size={17} />
        <h2>Mission Control</h2>
        <span className="psub">the project's notebook — kept current each session</span>
      </div>
      <div className="kbody">
        <div className="klist">
          {files.map((f) => (
            <button key={f.path} className={active === f.path ? "on" : ""} onClick={() => select(f.path)}>
              <Icon name="file" size={14} />
              <span>{f.path}</span>
            </button>
          ))}
        </div>
        <div className="kcontent md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

/** The self-maintaining wiki — curated knowledge pages Newton writes as it learns patterns. */
export function WikiPanel() {
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [content, setContent] = useState("");

  useEffect(() => {
    fetchWiki().then((f) => {
      setFiles(f);
      if (f[0]) select(f[0].path);
    });
  }, []);
  const select = (p: string) => {
    setActive(p);
    fetchWikiPage(p).then(setContent);
  };

  return (
    <div className="panel knowledge">
      <div className="panelhead">
        <Icon name="layers" size={17} />
        <h2>Wiki</h2>
        <span className="psub">curated knowledge — Newton writes a page when it learns a reusable pattern</span>
      </div>
      {files.length === 0 ? (
        <div className="panelbody">
          <div className="pempty">
            No wiki pages yet. When a task establishes a reusable pattern, Newton records it here —
            or curate pages by hand with <code>newton-wiki</code>.
          </div>
        </div>
      ) : (
        <div className="kbody">
          <div className="klist">
            {files.map((f) => (
              <button key={f.path} className={active === f.path ? "on" : ""} onClick={() => select(f.path)}>
                <Icon name="file" size={14} />
                <span>{f.name}</span>
              </button>
            ))}
          </div>
          <div className="kcontent md">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

/** Skills — reusable procedure playbooks the Conductor follows (SKILL.md). */
export function SkillsPanel() {
  const [files, setFiles] = useState<SkillFile[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [adding, setAdding] = useState(false);

  const load = (selectPath?: string) =>
    fetchSkills().then((f) => {
      setFiles(f);
      const target = selectPath ?? active ?? f[0]?.path;
      if (target) select(target);
    });
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const select = (p: string) => {
    setActive(p);
    fetchSkill(p).then(setContent);
  };

  return (
    <div className="panel knowledge">
      <div className="panelhead">
        <Icon name="zap" size={17} />
        <h2>Skills</h2>
        <span className="psub">reusable procedure playbooks the Conductor follows on matching tasks</span>
        <button className="headbtn" onClick={() => setAdding((a) => !a)}>
          {adding ? "Close" : "+ Add skill"}
        </button>
      </div>
      {adding && (
        <SkillForm
          onDone={(file) => {
            setAdding(false);
            load(file);
          }}
        />
      )}
      {files.length === 0 && !adding ? (
        <div className="panelbody">
          <div className="pempty">
            No skills yet. Add one with <b>+ Add skill</b> above (or <code>newton-skill add &lt;name&gt;</code>) —
            Newton loads the most relevant one into its plan for a matching task, and writes its own
            when a task teaches it a reusable procedure.
          </div>
        </div>
      ) : (
        files.length > 0 && (
          <div className="kbody">
            <div className="klist">
              {files.map((f) => (
                <button key={f.path} className={active === f.path ? "on" : ""} onClick={() => select(f.path)}
                        title={f.desc || f.name}>
                  <Icon name="file" size={14} />
                  <span>{f.name}</span>
                </button>
              ))}
            </div>
            <div className="kcontent md">
              {/* Strip the SKILL.md frontmatter block so it doesn't render as raw text. */}
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {content.replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "")}
              </ReactMarkdown>
            </div>
          </div>
        )
      )}
    </div>
  );
}

/** Inline form to author a new skill from the web app. */
function SkillForm({ onDone }: { onDone: (file?: string) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setError(null);
    setSaving(true);
    const r = await createSkill(name, description, body);
    setSaving(false);
    if (r.ok) onDone(r.file);
    else setError(r.error || "could not save the skill");
  };

  return (
    <div className="skillform">
      <label>Name</label>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. add-cli-flag" />
      <label>When to use it</label>
      <input
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="one line naming the trigger — e.g. adding a --flag to a CLI script"
      />
      <label>Procedure</label>
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={6}
        placeholder={"the steps to follow, as markdown — e.g.\n1. Add the argparse argument\n2. Thread it into the function\n3. Add a test that exercises the flag"}
      />
      {error && <div className="skillerr">{error}</div>}
      <div className="skillactions">
        <button className="primary" onClick={save} disabled={saving || !name.trim() || !body.trim()}>
          {saving ? "Saving…" : "Save skill"}
        </button>
        <button onClick={() => onDone()}>Cancel</button>
      </div>
    </div>
  );
}

/** What Newton remembers — completed tasks recorded to .newton/memory.jsonl. */
export function MemoryPanel() {
  const [entries, setEntries] = useState<any[]>([]);
  useEffect(() => {
    fetchMemory().then(setEntries);
  }, []);
  return (
    <div className="panel">
      <div className="panelhead">
        <Icon name="database" size={17} />
        <h2>Memory</h2>
        <span className="psub">{entries.length} remembered task{entries.length === 1 ? "" : "s"}</span>
      </div>
      <div className="panelbody">
        {entries.length === 0 && (
          <div className="pempty">No remembered tasks yet. Completed tasks are recorded here so Newton can recall them.</div>
        )}
        {entries.map((e, i) => (
          <div className="memcard" key={i}>
            <div className="memreq">{e.request}</div>
            {e.files?.length ? (
              <div className="memmeta">
                <Icon name="file" size={12} /> {e.files.join(", ")}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The component roadmap and what's built. */
export function ComponentsPanel() {
  const [comps, setComps] = useState<Component[]>([]);
  useEffect(() => {
    fetchComponents().then(setComps);
  }, []);
  const done = comps.filter((c) => c.status === "done").length;
  return (
    <div className="panel">
      <div className="panelhead">
        <Icon name="box" size={17} />
        <h2>Components</h2>
        <span className="psub">{done} of {comps.length} built</span>
      </div>
      <div className="panelbody compgrid">
        {comps.map((c, i) => (
          <div className={`compcard ${c.status}`} key={i}>
            <div className="ctop">
              <span className="cname">{c.name}</span>
              <span className={`cstatus ${c.status}`}>
                <Icon name={c.status === "done" ? "check" : "settings"} size={12} /> {c.status}
              </span>
            </div>
            <div className="cdesc">{c.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
