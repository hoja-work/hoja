import { Popover } from "radix-ui";
import React, { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown, {
  defaultUrlTransform,
  type Components,
} from "react-markdown";
import remarkGfm from "remark-gfm";

/* One Markdown renderer for every user-authored body in Hoja.
 *
 * Why a single module: comments, project updates, descriptions and chat all quote the same
 * untrusted text, so the safety rules (no raw HTML, sanitised URLs, no document-level headings,
 * bounded image sizes) must exist once and be proven once by the CommonMark/GFM corpus.
 *
 * Two densities:
 *   compact — a body inside a list of other bodies (comment, update, chat message)
 *   block   — a body that owns its section (issue description, project description)
 *
 * What is deliberately NOT rendered: raw HTML. react-markdown drops it by default (no
 * rehype-raw), so `<img onerror=…>` stays inert text. Do not add rehype-raw here.
 *
 * HTML comments are the one exception to "inert text": they are removed. Agents and
 * integrations keep machine markers in them (`<!-- hoja-task:… -->`, the
 * `<!-- hoja-managed:start/end -->` block bounds), and every other Markdown renderer hides
 * them, so showing them as text only puts noise at the end of a description or comment.
 */

export type MarkdownDensity = "compact" | "block";

// Bodies longer than this are shown verbatim: parsing a megabyte of markdown inside a list is
// worse than losing formatting, and the cap keeps a single bad paste from stalling the UI.
const MAX_PARSE_CHARS = 20000;
const CACHE_LIMIT = 512;

// Bare issue identifiers become chips outside code spans, fences and existing links.
const ISSUE_ID = /\b([A-Z][A-Z0-9]{1,9}-\d+)\b/g;
export function linkIssueIds(markdown: string): string {
  return markdown
    .split(/(```[\s\S]*?```|`[^`\n]*`|\[[^\]]*\]\([^)]*\))/)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(ISSUE_ID, (id) => `[${id}](hoja-issue:${id})`),
    )
    .join("");
}

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("hoja_token");
  return token && token !== "session"
    ? { authorization: `Bearer ${token}` }
    : {};
}

type IssueSummary = {
  identifier: string;
  title: string;
  state?: { name?: string; type?: string; color?: string } | null;
};
const ISSUE_CHIP_QUERY =
  "query AgentIssueChip($id:String!){issue(id:$id){identifier title state{name type color}}}";

export function IssueChip({ identifier }: { identifier: string }) {
  const [open, setOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cancelClose = () => clearTimeout(closeTimer.current);
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setOpen(false), 180);
  };
  useEffect(() => () => clearTimeout(closeTimer.current), []);
  const [issue, setIssue] = useState<IssueSummary | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch("/graphql", {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        query: ISSUE_CHIP_QUERY,
        variables: { id: identifier },
      }),
    })
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled && d?.data?.issue)
          setIssue(d.data.issue as IssueSummary);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [identifier]);
  const color = issue?.state?.color || "#a1a1aa";
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
    <button type="button"
      onPointerEnter={(event) => { if (event.pointerType === "mouse") { cancelClose(); setOpen(true); } }}
      onPointerLeave={(event) => { if (event.pointerType === "mouse") scheduleClose(); }}
      className="inline-flex items-center border border-line no-underline whitespace-nowrap gap-1.5 max-w-[min(320px,100%)] align-middle pt-0.5 pr-2.5 pb-0.5 pl-2 mt-0 mr-0.5 mb-0 ml-0.5 border-solid rounded-[999px] bg-panel text-foreground text-[13px] leading-normal hover:bg-subtle"
      data-agent-issue-chip=""
      aria-label={`Preview ${identifier}`}
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 16 16"
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        aria-hidden="true"
        className="flex-none"
      >
        <circle cx="8" cy="8" r="6" />
        {issue?.state?.type === "completed" && (
          <path d="M5 8.2l2 2 4-4.4" stroke={color} />
        )}
      </svg>
      <span className="shrink-0 tabular-nums text-muted">{identifier}</span>
      {issue && (
        <span className="overflow-hidden text-ellipsis min-w-0">
          {issue.title}
        </span>
      )}
    </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="top" align="start" sideOffset={8} onOpenAutoFocus={(event) => event.preventDefault()} onPointerEnter={cancelClose} onPointerLeave={(event) => { if (event.pointerType === "mouse") scheduleClose(); }} className="z-150 w-80 max-w-[calc(100vw-24px)] rounded-xl border border-line bg-panel p-3 text-foreground shadow-(--shadow)" aria-label={`Issue preview: ${identifier}`}>
          <p className="m-0 text-[11px] text-muted">{identifier}</p>
          {issue?.state?.name && (
            <span className="mt-1.5 inline-flex items-center gap-1.5 rounded-full border border-line bg-subtle px-2 py-0.5 text-[11px] text-foreground">
              <span aria-hidden="true" className="size-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
              {issue.state.name}
            </span>
          )}
          <p className="mt-1 mb-3 whitespace-normal break-words text-[13px] font-medium">{issue?.title || identifier}</p>
<div className="flex justify-end items-center">
          <a href={`/workspace/issue/${identifier}`} className="inline-flex items-center  rounded-full border border-line shadow-xs hover:bg-neutral-100 px-2.5 py-1.5 text-[12px] text-foreground no-underline ">Open issue</a>
</div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/* Page context owns h1/h2. A body may outline itself, so headings are demoted by two levels and
 * clamped at h6: `#` renders as h3, `##` as h4, and h5/h6 both land on h6. This keeps heading
 * order monotonic and guarantees no comment can outrank the page title. */
export function demoteHeading(tag: string): string {
  const level = Number(tag.slice(1));
  return `h${Math.min(6, (Number.isFinite(level) ? level : 6) + 2)}`;
}

const link: NonNullable<Components["a"]> = ({ href, children }) => {
  if (href && href.startsWith("hoja-issue:"))
    return <IssueChip identifier={href.slice("hoja-issue:".length)} />;
  const issue = href?.match(
    /^\/workspace\/issue\/([A-Z][A-Z0-9]{1,9}-\d+)(?:\/|$)/,
  );
  if (issue) return <IssueChip identifier={issue[1]} />;
  const external = !!href && !href.startsWith("/") && !href.startsWith("#");
  return (
    <a
      className="text-foreground underline underline-offset-[3px] decoration-line hover:decoration-foreground"
      href={href}
      {...(external
        ? { target: "_blank", rel: "noopener noreferrer nofollow" }
        : {})}
    >
      {children}
    </a>
  );
};

function heading(tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6") {
  const Tag = demoteHeading(tag) as "h3";
  return function MdHeading({ children }: { children?: React.ReactNode }) {
    return (
      <Tag className="mt-[.9em] mb-[.35em] text-[1em] font-[650] leading-[1.35] tracking-[-.005em]">
        {children}
      </Tag>
    );
  };
}

// Module-level constant so the element types are stable across renders (an inline `a` would
// remount its subtree on every keystroke in a composer that previews while typing).
const COMPONENTS: Components = {
  a: link,
  h1: heading("h1"),
  h2: heading("h2"),
  h3: heading("h3"),
  h4: heading("h4"),
  h5: heading("h5"),
  h6: heading("h6"),
  // Tables scroll inside their own box: a 5-column GFM table in a 320px comment must not
  // widen the page.
  p: ({ children }) => <p className="mt-0 mb-[.7em] last:mb-0">{children}</p>,
  ul: ({ children }) => (
    <ul className="mt-[.3em] mb-[.7em] list-disc pl-[1.3em] [li>&]:mt-[.25em] [li>&]:mb-[.3em]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-[.3em] mb-[.7em] list-decimal pl-[1.3em] [li>&]:mt-[.25em] [li>&]:mb-[.3em]">
      {children}
    </ol>
  ),
  // A loose list (items made of paragraphs, e.g. question / answer / evidence) needs paragraph
  // spacing between its items, not the tight .2em of a one-line list. Task items hang their text
  // off the checkbox so a wrapped line lines up with the first one.
  li: ({ children, className }) => (
    <li
      className={`[li+&]:mt-[.2em] [li:has(>p)+&]:mt-[.8em] ${className === "task-list-item" ? "relative ml-[-1.3em] list-none pl-[1.55em]" : ""}`}
    >
      {children}
    </li>
  ),
  code: ({ children }) => (
    <code className="rounded-md bg-subtle px-[.35em] py-[.1em] font-[ui-monospace,SFMono-Regular,Menlo,monospace] text-[.88em] in-[pre]:p-0 in-[pre]:bg-transparent in-[pre]:text-[.85em] in-[pre]:leading-normal">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="my-[.5em] overflow-x-auto rounded-xl border border-line bg-subtle px-3.5 py-3">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-[.5em] border-l-2 border-line pl-3 text-muted">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-[.9em] border-0 border-t border-line" />,
  th: ({ children }) => (
    <th className="border border-line bg-subtle px-2.5 py-1.5 text-left font-semibold break-normal">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-line px-2.5 py-1.5 text-left break-normal">{children}</td>
  ),
  table: ({ children }) => (
    <div data-md-table className="my-[.5em] max-w-full overflow-x-auto">
      <table className="my-[.5em] border-collapse text-[.94em]">
        {children}
      </table>
    </div>
  ),
  img: ({ src, alt, title }) => (
    <img
      className="my-[.4em] h-auto max-w-full rounded-lg border border-line"
      src={src}
      alt={alt ?? ""}
      title={title}
      loading="lazy"
      decoding="async"
    />
  ),
  input: ({ type, checked }) =>
    type === "checkbox" ? (
      <input
        className="absolute top-[.3em] left-0 m-0 accent-accent"
        type="checkbox"
        checked={!!checked}
        disabled
        readOnly
        aria-label={checked ? "Completed task" : "Open task"}
      />
    ) : null,
};

/* react-markdown blanks any protocol outside http(s)/mailto/xmpp/irc, which also blanked the
 * `hoja-issue:` scheme the chips rely on (they rendered as `href=""`). Keep that one scheme and
 * defer everything else to the stock transform, so `javascript:` and `data:` stay stripped. */
const ISSUE_SCHEME = "hoja-issue:";
function urlTransform(url: string): string {
  return url.startsWith(ISSUE_SCHEME) ? url : defaultUrlTransform(url);
}

/* An `html` node that is nothing but comments (`<!-- … -->`, possibly several). Any other raw
 * HTML keeps its inert-text rendering. */
const COMMENT_ONLY = /^(?:\s*<!--[\s\S]*?-->)+\s*$/;
type MdNode = { type: string; value?: string; children?: MdNode[] };
function dropComments(node: MdNode): void {
  if (!node.children) return;
  node.children = node.children.filter(
    (child) => !(child.type === "html" && COMMENT_ONLY.test(child.value ?? "")),
  );
  node.children.forEach(dropComments);
}
/** Remark plugin: removes HTML comments from the tree before it is rendered. */
function remarkDropHtmlComments() {
  return (tree: MdNode) => dropComments(tree);
}

const GFM = [remarkGfm, remarkDropHtmlComments];

type Stats = { parses: number; hits: number };
const stats: Stats = { parses: 0, hits: 0 };
const cache = new Map<string, React.ReactNode>();

/** Parse counters, read by the browser tests to prove identical bodies are parsed once. */
export function markdownStats(): Stats {
  return { ...stats };
}
export function resetMarkdownStats(): void {
  stats.parses = 0;
  stats.hits = 0;
}
if (typeof window !== "undefined") {
  (
    window as unknown as {
      __hojaMarkdown?: { stats: Stats; reset: () => void };
    }
  ).__hojaMarkdown = {
    stats,
    reset: resetMarkdownStats,
  };
}

function parse(text: string): React.ReactNode {
  if (text.length > MAX_PARSE_CHARS) {
    return (
      <pre
        className="m-0 whitespace-pre-wrap wrap-break-word"
        data-md-plain="true"
      >
        {text}
      </pre>
    );
  }
  return (
    <ReactMarkdown
      remarkPlugins={GFM}
      components={COMPONENTS}
      urlTransform={urlTransform}
    >
      {linkIssueIds(text)}
    </ReactMarkdown>
  );
}

function render(text: string, key: string): React.ReactNode {
  const hit = cache.get(key);
  if (hit !== undefined) {
    stats.hits++;
    return hit;
  }
  stats.parses++;
  const node = parse(text);
  if (cache.size >= CACHE_LIMIT) {
    const oldest = cache.keys().next();
    if (!oldest.done) cache.delete(oldest.value);
  }
  cache.set(key, node);
  return node;
}

export type MarkdownProps = {
  text?: string | null;
  density?: MarkdownDensity;
  /** Set false for text that must stay literal, e.g. agent system-authored copy. */
  issueLinks?: boolean;
  className?: string;
};

/** Renders untrusted Markdown. Safe by construction: no raw HTML, sanitised URLs, no h1/h2. */
export const Markdown = memo(function Markdown({
  text,
  density = "compact",
  issueLinks = true,
  className,
}: MarkdownProps) {
  const value = text ?? "";
  const key = `${issueLinks ? "l" : "n"}\u0000${value}`;
  // issueLinks changes the source, not the output shape, so encode it in the key only.
  const node = issueLinks ? render(value, key) : renderPlain(value, key);
  return (
    <div
      className={
        `md md--${density}  ${density === "block" ? "text-[14px] leading-[1.65]" : "text-[13px] leading-[1.6]"}  ${className || ""}` +
        " min-w-0 text-foreground wrap-anywhere"
      }
      data-md={density}
    >
      {node}
    </div>
  );
});

function renderPlain(text: string, key: string): React.ReactNode {
  const hit = cache.get(`p${key}`);
  if (hit !== undefined) {
    stats.hits++;
    return hit;
  }
  stats.parses++;
  const node =
    text.length > MAX_PARSE_CHARS ? (
      <pre
        className="m-0 whitespace-pre-wrap wrap-break-word"
        data-md-plain="true"
      >
        {text}
      </pre>
    ) : (
      <ReactMarkdown
        remarkPlugins={GFM}
        components={COMPONENTS}
        urlTransform={urlTransform}
      >
        {text}
      </ReactMarkdown>
    );
  if (cache.size >= CACHE_LIMIT) {
    const oldest = cache.keys().next();
    if (!oldest.done) cache.delete(oldest.value);
  }
  cache.set(`p${key}`, node);
  return node;
}

export default Markdown;
