/**
 * The connection map: one item in the middle, what it is connected to on either side.
 *
 * Left is the task, middle the pull request, right everything that gates it (checks, the merge
 * gate, and sources that are not connected yet). Nodes are read from `connectionNodesOf` in
 * ./inbox.ts, so the map and the inbox cannot disagree about what is connected.
 */
import type { ConnectionNode } from "./inbox";

const TONE: Record<ConnectionNode["state"], string> = {
  ok: "border-line",
  warn: "border-red-500/60",
  missing: "border-amber-500/60",
  off: "border-dashed border-line",
};

const DOT: Record<ConnectionNode["state"], string> = {
  ok: "bg-green-500",
  warn: "bg-red-500",
  missing: "bg-amber-500",
  off: "border border-muted bg-transparent",
};

function Node({ node }: { node: ConnectionNode }) {
  return (
    <div
      className={`flex min-w-0 flex-col gap-0.5 rounded-md border bg-panel px-3 py-2 ${TONE[node.state]} ${node.state === "off" ? "text-muted" : ""}`}
      data-connection={node.kind}
      data-state={node.state}
    >
      <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted">
        <span
          aria-hidden="true"
          className={`size-2 flex-none rounded-full ${DOT[node.state]}`}
        />
        <span className="truncate">{node.detail}</span>
      </span>
      <span className="truncate text-[13px] font-medium text-foreground">
        {node.title}
      </span>
    </div>
  );
}

export function ConnectionMap({ nodes }: { nodes: ConnectionNode[] }) {
  const pick = (kinds: ConnectionNode["kind"][]) =>
    kinds
      .map((k) => nodes.find((n) => n.kind === k))
      .filter(Boolean) as ConnectionNode[];
  const left = pick(["task"]);
  const middle = pick(["pull-request"]);
  const right = pick([
    "checks",
    "merge-gate",
    "sentry",
    "github-review-request",
  ]);
  return (
    <section
      aria-label="Connections"
      className="flex flex-col gap-2"
      data-reviews-connection-map=""
    >
      <h3 className="m-0 text-xs font-semibold text-muted">Connections</h3>
      <div className="flex items-stretch">
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-2">
          {left.map((n) => (
            <Node key={n.kind} node={n} />
          ))}
        </div>
        <span
          aria-hidden="true"
          className="w-4 flex-none self-center border-t border-line"
        />
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-2">
          {middle.map((n) => (
            <Node key={n.kind} node={n} />
          ))}
        </div>
        <span
          aria-hidden="true"
          className="w-4 flex-none self-center border-t border-line"
        />
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-2">
          {right.map((n) => (
            <Node key={n.kind} node={n} />
          ))}
        </div>
      </div>
    </section>
  );
}

export default ConnectionMap;
