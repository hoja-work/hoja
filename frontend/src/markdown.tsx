import React, {memo, useEffect, useState} from 'react';
import ReactMarkdown, {defaultUrlTransform, type Components} from 'react-markdown';
import remarkGfm from 'remark-gfm';

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
 */

export type MarkdownDensity = 'compact' | 'block';

// Bodies longer than this are shown verbatim: parsing a megabyte of markdown inside a list is
// worse than losing formatting, and the cap keeps a single bad paste from stalling the UI.
const MAX_PARSE_CHARS = 20000;
const CACHE_LIMIT = 512;

// Bare issue identifiers become chips outside code spans, fences and existing links.
const ISSUE_ID = /\b([A-Z][A-Z0-9]{1,9}-\d+)\b/g;
export function linkIssueIds(markdown: string): string {
  return markdown
    .split(/(```[\s\S]*?```|`[^`\n]*`|\[[^\]]*\]\([^)]*\))/)
    .map((part, i) => (i % 2 === 1 ? part : part.replace(ISSUE_ID, (id) => `[${id}](hoja-issue:${id})`)))
    .join('');
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('hoja_token');
  return token && token !== 'session' ? {authorization: `Bearer ${token}`} : {};
}

type IssueSummary = {identifier: string; title: string; state?: {name?: string; type?: string; color?: string} | null};
const ISSUE_CHIP_QUERY = 'query AgentIssueChip($id:String!){issue(id:$id){identifier title state{name type color}}}';

export function IssueChip({identifier}: {identifier: string}) {
  const [issue, setIssue] = useState<IssueSummary | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch('/graphql', {
      method: 'POST',
      credentials: 'include',
      headers: {'content-type': 'application/json', ...authHeaders()},
      body: JSON.stringify({query: ISSUE_CHIP_QUERY, variables: {id: identifier}}),
    })
      .then((r) => r.json())
      .then((d) => { if (!cancelled && d?.data?.issue) setIssue(d.data.issue as IssueSummary); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [identifier]);
  const color = issue?.state?.color || '#a1a1aa';
  return (
    <a className="agent-issue-chip" href={`/workspace/issue/${identifier}`} title={issue?.title ?? identifier}>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" aria-hidden="true">
        <circle cx="8" cy="8" r="6" />
        {issue?.state?.type === 'completed' && <path d="M5 8.2l2 2 4-4.4" stroke={color} />}
      </svg>
      <span className="agent-issue-chip-id">{identifier}</span>
      {issue && <span className="agent-issue-chip-title">{issue.title}</span>}
    </a>
  );
}

/* Page context owns h1/h2. A body may outline itself, so headings are demoted by two levels and
 * clamped at h6: `#` renders as h3, `##` as h4, and h5/h6 both land on h6. This keeps heading
 * order monotonic and guarantees no comment can outrank the page title. */
export function demoteHeading(tag: string): string {
  const level = Number(tag.slice(1));
  return `h${Math.min(6, (Number.isFinite(level) ? level : 6) + 2)}`;
}

const link: NonNullable<Components['a']> = ({href, children}) => {
  if (href && href.startsWith('hoja-issue:')) return <IssueChip identifier={href.slice('hoja-issue:'.length)} />;
  const issue = href?.match(/^\/workspace\/issue\/([A-Z][A-Z0-9]{1,9}-\d+)(?:\/|$)/);
  if (issue) return <IssueChip identifier={issue[1]} />;
  const external = !!href && !href.startsWith('/') && !href.startsWith('#');
  return (
    <a href={href} {...(external ? {target: '_blank', rel: 'noopener noreferrer nofollow'} : {})}>
      {children}
    </a>
  );
};

function heading(tag: 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6') {
  const Tag = demoteHeading(tag) as 'h3';
  return function MdHeading({children}: {children?: React.ReactNode}) {
    return <Tag className="md-h">{children}</Tag>;
  };
}

// Module-level constant so the element types are stable across renders (an inline `a` would
// remount its subtree on every keystroke in a composer that previews while typing).
const COMPONENTS: Components = {
  a: link,
  h1: heading('h1'),
  h2: heading('h2'),
  h3: heading('h3'),
  h4: heading('h4'),
  h5: heading('h5'),
  h6: heading('h6'),
  // Tables scroll inside their own box: a 5-column GFM table in a 320px comment must not
  // widen the page.
  table: ({children}) => <div className="md-table"><table>{children}</table></div>,
  img: ({src, alt, title}) => (
    <img className="md-img" src={src} alt={alt ?? ''} title={title} loading="lazy" decoding="async" />
  ),
  input: ({type, checked}) => type === 'checkbox'
    ? <input className="md-check" type="checkbox" checked={!!checked} disabled readOnly aria-label={checked ? 'Completed task' : 'Open task'} />
    : null,
};

/* react-markdown blanks any protocol outside http(s)/mailto/xmpp/irc, which also blanked the
 * `hoja-issue:` scheme the chips rely on (they rendered as `href=""`). Keep that one scheme and
 * defer everything else to the stock transform, so `javascript:` and `data:` stay stripped. */
const ISSUE_SCHEME = 'hoja-issue:';
function urlTransform(url: string): string {
  return url.startsWith(ISSUE_SCHEME) ? url : defaultUrlTransform(url);
}

const GFM = [remarkGfm];

type Stats = {parses: number; hits: number};
const stats: Stats = {parses: 0, hits: 0};
const cache = new Map<string, React.ReactNode>();

/** Parse counters, read by the browser tests to prove identical bodies are parsed once. */
export function markdownStats(): Stats {
  return {...stats};
}
export function resetMarkdownStats(): void {
  stats.parses = 0;
  stats.hits = 0;
}
if (typeof window !== 'undefined') {
  (window as unknown as {__hojaMarkdown?: {stats: Stats; reset: () => void}}).__hojaMarkdown = {
    stats,
    reset: resetMarkdownStats,
  };
}

function parse(text: string): React.ReactNode {
  if (text.length > MAX_PARSE_CHARS) {
    return <pre className="md-plain" data-md-plain="true">{text}</pre>;
  }
  return <ReactMarkdown remarkPlugins={GFM} components={COMPONENTS} urlTransform={urlTransform}>{linkIssueIds(text)}</ReactMarkdown>;
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
export const Markdown = memo(function Markdown({text, density = 'compact', issueLinks = true, className}: MarkdownProps) {
  const value = text ?? '';
  const key = `${issueLinks ? 'l' : 'n'}\u0000${value}`;
  // issueLinks changes the source, not the output shape, so encode it in the key only.
  const node = issueLinks ? render(value, key) : renderPlain(value, key);
  return (
    <div className={className ? `md md--${density} ${className}` : `md md--${density}`} data-md={density}>
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
  const node = text.length > MAX_PARSE_CHARS
    ? <pre className="md-plain" data-md-plain="true">{text}</pre>
    : <ReactMarkdown remarkPlugins={GFM} components={COMPONENTS} urlTransform={urlTransform}>{text}</ReactMarkdown>;
  if (cache.size >= CACHE_LIMIT) {
    const oldest = cache.keys().next();
    if (!oldest.done) cache.delete(oldest.value);
  }
  cache.set(`p${key}`, node);
  return node;
}

export default Markdown;
