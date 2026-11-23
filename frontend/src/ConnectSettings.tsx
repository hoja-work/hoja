import { useEffect, useState } from "react";

async function gql(q: string) {
  const t = localStorage.getItem("hoja_token");
  const r = await fetch("/graphql", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(t && t !== "session" ? { authorization: `Bearer ${t}` } : {}),
    },
    body: JSON.stringify({ query: q }),
  });
  const b = await r.json();
  if (b.errors?.length) throw Error(b.errors[0].message);
  return b.data;
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  };
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-sm text-(--settings-muted)">{label}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-md border border-line bg-panel p-2 font-mono text-sm">
          {value}
        </code>
        <button
          type="button"
          className="inline-flex min-h-8 items-center justify-center rounded-md border border-line bg-panel px-3 py-2 text-sm text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground pointer-coarse:min-h-11"
          aria-label={`Copy ${label}`}
          onClick={copy}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {failed && (
        <span role="alert" className="text-sm text-(--settings-muted)">
          Copy failed. Select the text and copy it manually.
        </span>
      )}
    </div>
  );
}

/** Where this workspace is reached from agents and scripts: its own origin, from the API. */
export function ConnectSettings() {
  const [workspace, setWorkspace] = useState<{
    name: string;
    origin: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    gql("query ConnectOrigin{workspace{name origin}}")
      .then((d) => {
        if (live) setWorkspace(d.workspace);
      })
      .catch((e) => {
        if (live) setError((e as Error).message);
      });
    return () => {
      live = false;
    };
  }, []);
  const origin = workspace?.origin.replace(/\/+$/, "") || "";
  return (
    <section
      className="mb-8 flex min-w-0 flex-col gap-4 rounded-xl border border-line bg-panel p-4"
      aria-labelledby="connect-title"
    >
      <div className="flex flex-col gap-1">
        <h2 id="connect-title" className="text-base font-semibold">
          Connect
        </h2>
        <p className="text-sm text-(--settings-muted)">
          Use these addresses to connect agents, MCP clients and scripts to{" "}
          {workspace?.name || "this workspace"}. The Hoja CLI and MCP clients
          sign in through your browser; unattended scripts use an API key from
          below.
        </p>
      </div>
      {error && (
        <p role="alert" className="text-sm text-(--settings-muted)">
          Could not load this workspace's address: {error}
        </p>
      )}
      {!workspace && !error && (
        <p className="text-sm text-(--settings-muted)">Loading…</p>
      )}
      {workspace && (
        <>
          <CopyRow label="Sign in from a terminal" value={`hoja auth login ${origin}`} />
          <CopyRow label="MCP server URL" value={`${origin}/mcp`} />
          <CopyRow label="GraphQL endpoint" value={`${origin}/graphql`} />
          <CopyRow
            label="Add to Claude Code"
            value={`claude mcp add --transport http hoja ${origin}/mcp`}
          />
        </>
      )}
    </section>
  );
}
