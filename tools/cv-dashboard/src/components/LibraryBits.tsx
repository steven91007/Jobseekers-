// Notion links and library variants: CV blocks, the keyword card and the Ask Claude drawer share these.
import type { BulletRef, ItemLibrary, LibraryVariant, TargetLibrary } from "../../shared/types";

export function NotionLink({ url, label = "Notion", title }: { url: string | null | undefined; label?: string; title?: string }) {
  if (!url) return null;
  return (
    <a className="notion-link" href={url} target="_blank" rel="noreferrer" title={title ?? url} onClick={(e) => e.stopPropagation()}>
      {label} ↗
    </a>
  );
}

/** The item's own page, else its project's page. */
export function ItemNotionLink({ lib }: { lib: ItemLibrary | undefined }) {
  if (!lib) return null;
  if (lib.notionUrl) return <NotionLink url={lib.notionUrl} />;
  if (lib.projectNotionUrl)
    return <NotionLink url={lib.projectNotionUrl} label={`Notion ${lib.projectCode ?? "project"}`} title={`No page for this row yet; project page: ${lib.projectName ?? ""}`} />;
  return null;
}

const KIND_LABEL: Record<LibraryVariant["kind"], string> = {
  lead: "lead",
  title: "title",
  context: "context",
  scope: "scope",
  label: "label",
  spelling: "spelling",
};

/** Instruction the "Use" button pre-fills for `code` (bullet, project, scope or skill-row code). */
export function variantInstruction(code: string, v: LibraryVariant): string {
  switch (v.kind) {
    case "lead":
      return `Switch ${code} to lead variant ${v.ord}: "${v.text}"`;
    case "title":
      return `Switch card ${code} to title variant ${v.ord}: "${v.text}"`;
    case "context":
      return `Switch card ${code} to context variant ${v.ord}: "${v.text}"`;
    case "scope":
      return `Switch the scope line to ${v.code}: "${v.text}"`;
    case "label":
      return `Switch skill row ${code} to label variant ${v.ord}: "${v.text}"`;
    case "spelling":
      return `In skill row ${code}, switch "${v.item}" to spelling ${v.ord}: "${v.text}"`;
  }
}

export function swapInstruction(b: BulletRef, lead: { ord: number; text: string }): string {
  return `Replace the weakest bullet with ${b.code} using lead variant ${lead.ord}: "${lead.text}"`;
}

export function VariantList({
  variants,
  action,
  onPick,
  disabled,
}: {
  variants: LibraryVariant[];
  action: string;
  onPick: (v: LibraryVariant) => void;
  disabled?: boolean;
}) {
  let lastItem: string | undefined;
  return (
    <ul className="variants">
      {variants.map((v, i) => {
        const head = v.kind === "spelling" && v.item !== lastItem ? v.item : null;
        lastItem = v.kind === "spelling" ? v.item : undefined;
        return (
          <li key={`${v.kind}-${v.item ?? ""}-${v.ord}-${i}`} className={v.current ? "current" : ""}>
            {head && <div className="v-item">{head}</div>}
            <div className="v-row">
              <span className="v-kind mono">
                {KIND_LABEL[v.kind]} {v.kind === "scope" ? v.code : v.ord}
              </span>
              <span className="v-text">
                {v.text}
                {v.current && <span className="v-cur"> · on this CV</span>}
                {(v.conf || v.note) && <span className="faint"> · {[v.conf, v.note].filter(Boolean).join(" · ")}</span>}
              </span>
              <button className="btn-sm" disabled={disabled || v.current} onClick={() => onPick(v)}>
                {action}
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function BulletRefList({
  refs,
  action,
  onPick,
  disabled,
}: {
  refs: BulletRef[];
  action: string;
  onPick: (b: BulletRef, lead: { ord: number; text: string }) => void;
  disabled?: boolean;
}) {
  return (
    <div className="bullet-refs">
      {refs.map((b) => (
        <div key={b.code} className="bullet-ref">
          <div className="br-head">
            <span className="badge badge-code">{b.code}</span>
            <NotionLink url={b.notionUrl} />
            {!b.notionUrl && <NotionLink url={b.projectNotionUrl} label={`Notion ${b.projectCode}`} />}
          </div>
          <ul className="variants">
            {b.leads.map((l) => (
              <li key={l.ord}>
                <div className="v-row">
                  <span className="v-kind mono">lead {l.ord}</span>
                  <span className="v-text">{l.text}</span>
                  <button className="btn-sm" disabled={disabled} onClick={() => onPick(b, l)}>
                    {action}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** Drawer panel: links + variants for the current target; picking inserts text into the instruction. */
export function LibraryPanel({ lib, onInsert, disabled }: { lib: TargetLibrary; onInsert: (text: string) => void; disabled?: boolean }) {
  const { item, keyword } = lib;
  const links = item?.skillItems?.filter((s) => s.notionUrl) ?? [];
  return (
    <details className="library-panel" open>
      <summary>
        Library
        {item && <ItemNotionLink lib={item} />}
        {item?.notionUrl && item.projectNotionUrl && item.projectNotionUrl !== item.notionUrl && (
          <NotionLink url={item.projectNotionUrl} label={`Project ${item.projectCode ?? ""}`} />
        )}
        {keyword && <NotionLink url={keyword.notionUrl} label="Keyword" />}
      </summary>
      {item?.bodyPreview && <div className="faint lib-body">{item.bodyPreview}</div>}
      {links.length > 0 && (
        <div className="lib-links">
          {links.map((s) => (
            <NotionLink key={s.ord} url={s.notionUrl} label={s.item} />
          ))}
        </div>
      )}
      {item && item.variants.length > 0 && (
        <VariantList variants={item.variants} action="Insert" disabled={disabled} onPick={(v) => onInsert(v.text)} />
      )}
      {keyword && keyword.bulletRefs.length > 0 && (
        <BulletRefList refs={keyword.bulletRefs} action="Insert" disabled={disabled} onPick={(b, l) => onInsert(`${b.code} lead ${l.ord}: "${l.text}"`)} />
      )}
      {keyword && keyword.skillRefs.length > 0 && (
        <div className="lib-links">
          Skills:{" "}
          {keyword.skillRefs.map((s) => (s.notionUrl ? <NotionLink key={s.name} url={s.notionUrl} label={s.name} /> : <span key={s.name}>{s.name}</span>))}
        </div>
      )}
      {!item?.variants.length && !keyword?.bulletRefs.length && <div className="faint">No stored variants for this target.</div>}
    </details>
  );
}
