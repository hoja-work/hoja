/**
 * The four write actions for a linked pull request, as buttons.
 *
 * The merge button is never allowed to lie:
 *   - it stays disabled until `reviewGate` has answered, because a gate nobody read is not a gate;
 *   - the confirm control prints the exact sha and the check counts it is about to merge;
 *   - the server re-reads the gate and re-checks that sha, so a stale tab produces a refusal
 *     rather than a merge;
 *   - on any error the row is left alone and the error is shown, including GitHub's own hint.
 */
import { useCallback, useEffect, useState } from "react";

type Gql = (query: string, variables?: Record<string, unknown>) => Promise<any>;

export type ReviewGate = {
  sha: string;
  mergeable: boolean | null;
  mergeState: string | null;
  checksState: string | null;
  checksTotal: number | null;
  checksPassing: number | null;
  checksFailing: number | null;
  checksPending: number | null;
  /** GitHub refused the check read (app lacks Checks / Commit statuses) — not "no checks". */
  checksUnavailable: boolean | null;
  state: string | null;
  refusalCode: string | null;
  refusalMessage: string | null;
};

type Props = {
  linkId: string;
  gql: Gql;
  /** Called only after the server confirmed the write, so the ledger can refresh from truth. */
  onChanged?: (result: {
    action: string;
    actor?: string | null;
    mergeSha?: string | null;
  }) => void;
};

const GATE_QUERY = `query ReviewGate($id:String!){reviewGate(linkId:$id){
  sha mergeable mergeState checksState checksTotal checksPassing checksFailing checksPending
  checksUnavailable state refusalCode refusalMessage}}`;

function shortSha(sha?: string | null): string {
  return sha ? sha.slice(0, 7) : "unknown";
}

function gateLine(gate: ReviewGate): string {
  const parts = [
    `sha ${shortSha(gate.sha)}`,
    gate.checksUnavailable
      ? "checks unreadable by this GitHub app"
      : gate.checksState === "unknown" || gate.checksState === "none"
        ? "no checks"
        : `${gate.checksPassing ?? 0}/${gate.checksTotal ?? 0} checks passing`,
    gate.mergeState ? `branch ${gate.mergeState}` : "branch state unknown",
  ];
  return parts.join(" · ");
}

const BUTTON =
  "inline-flex items-center justify-center h-8 px-3 rounded-md border border-line bg-panel text-[13px] text-foreground hover:bg-subtle disabled:opacity-50 disabled:cursor-not-allowed pointer-coarse:h-11";
const PRIMARY =
  "inline-flex items-center justify-center h-8 px-3 rounded-md border border-accent bg-accent text-[13px] text-white disabled:opacity-50 disabled:cursor-not-allowed pointer-coarse:h-11";

export function ReviewActions({ linkId, gql, onChanged }: Props) {
  const [gate, setGate] = useState<ReviewGate | null>(null);
  const [gateError, setGateError] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [notice, setNotice] = useState<string>("");
  const [body, setBody] = useState<string>("");
  const [composing, setComposing] = useState<"comment" | "request" | null>(
    null,
  );
  const [confirmingMerge, setConfirmingMerge] = useState(false);

  const loadGate = useCallback(() => {
    let live = true;
    setGateError("");
    gql(GATE_QUERY, { id: linkId })
      .then((d: any) => {
        if (live) setGate(d?.reviewGate ?? null);
      })
      .catch((e: Error) => {
        if (live) setGateError(e.message);
      });
    return () => {
      live = false;
    };
  }, [gql, linkId]);

  useEffect(() => {
    setGate(null);
    return loadGate();
  }, [loadGate]);

  const run = async (
    action: string,
    query: string,
    variables: Record<string, unknown>,
  ) => {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      const data = await gql(query, variables);
      const payload = data?.[action] ?? null;
      if (!payload) throw new Error("The server did not confirm this action.");
      setNotice(
        payload.mergeSha
          ? `Merged ${shortSha(payload.sha)} as ${shortSha(payload.mergeSha)}. ${payload.actor || ""}`.trim()
          : `${payload.action} recorded. ${payload.actor || ""}`.trim(),
      );
      setComposing(null);
      setConfirmingMerge(false);
      setBody("");
      onChanged?.(payload);
      loadGate();
    } catch (e) {
      // Never mirror the action locally: the row keeps whatever GitHub last confirmed.
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const approve = () =>
    run(
      "reviewApprove",
      `mutation($id:String!){reviewApprove(linkId:$id){action sha actor}}`,
      { id: linkId },
    );

  const comment = () =>
    run(
      "reviewComment",
      `mutation($id:String!,$body:String!){reviewComment(linkId:$id,body:$body){action sha actor}}`,
      { id: linkId, body },
    );

  const requestChanges = () =>
    run(
      "reviewRequestChanges",
      `mutation($id:String!,$body:String!){reviewRequestChanges(linkId:$id,body:$body){action sha actor}}`,
      { id: linkId, body },
    );

  const merge = () =>
    run(
      "reviewMerge",
      `mutation($id:String!,$sha:String!){reviewMerge(linkId:$id,sha:$sha){action sha mergeSha actor}}`,
      { id: linkId, sha: gate?.sha ?? "" },
    );

  const blocked = gate?.refusalCode ?? null;
  const gateReady = gate !== null && !gateError;

  return (
    <section
      className="flex flex-col gap-3"
      aria-label="Review actions"
      data-review-actions=""
    >
      <p
        className="m-0 text-xs text-muted tabular-nums"
        role="status"
        aria-live="polite"
      >
        {gateError ? (
          <span className="text-red-500">
            Could not read the merge gate: {gateError}
          </span>
        ) : gate ? (
          <span title={gate.sha}>{gateLine(gate)}</span>
        ) : (
          <span>Reading the merge gate…</span>
        )}
      </p>

      <div className="flex flex-wrap gap-2 max-sm:grid max-sm:grid-cols-2">
        <button
          type="button"
          className={BUTTON}
          onClick={approve}
          disabled={!gateReady || busy !== ""}
        >
          {busy === "reviewApprove" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          className={BUTTON}
          onClick={() =>
            setComposing(composing === "request" ? null : "request")
          }
          disabled={!gateReady || busy !== ""}
        >
          Request changes
        </button>
        <button
          type="button"
          className={BUTTON}
          onClick={() =>
            setComposing(composing === "comment" ? null : "comment")
          }
          disabled={!gateReady || busy !== ""}
        >
          Comment
        </button>
        <button
          type="button"
          className={`${PRIMARY} max-sm:col-span-2`}
          onClick={() => setConfirmingMerge(true)}
          disabled={!gateReady || busy !== "" || blocked !== null}
          title={
            blocked
              ? (gate?.refusalMessage ?? blocked)
              : `Merge ${shortSha(gate?.sha)}`
          }
        >
          Squash and merge
        </button>
      </div>

      {blocked ? (
        <p className="m-0 text-[13px] text-red-500" data-code={blocked}>
          {gate?.refusalMessage ??
            "This pull request cannot be merged right now."}
        </p>
      ) : null}

      {composing ? (
        <div className="flex flex-col gap-2 rounded-md border border-line bg-panel p-3">
          <textarea
            className="w-full min-h-20 rounded-md border border-line bg-transparent p-2 text-[13px] text-foreground resize-y pointer-coarse:text-base"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={
              composing === "request"
                ? "What needs to change?"
                : "Leave a comment"
            }
            rows={3}
            aria-label={
              composing === "request" ? "Requested changes" : "Comment"
            }
          />
          <div className="flex gap-2 max-sm:flex-col">
            <button
              type="button"
              className={PRIMARY}
              onClick={composing === "request" ? requestChanges : comment}
              disabled={!body.trim() || busy !== ""}
            >
              {composing === "request" ? "Submit request" : "Post comment"}
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={() => setComposing(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {confirmingMerge && gate ? (
        <div
          role="alertdialog"
          aria-label="Confirm merge"
          className="flex flex-col gap-2 rounded-md border border-red-500 bg-panel p-3"
        >
          <p className="m-0 text-[13px]">
            Merge commit{" "}
            <code className="font-mono text-xs break-all" title={gate.sha}>
              {gate.sha}
            </code>
          </p>
          <p className="m-0 text-xs text-muted">{gateLine(gate)}</p>
          <div className="flex gap-2 max-sm:flex-col">
            <button
              type="button"
              className={PRIMARY}
              onClick={merge}
              disabled={busy !== ""}
            >
              {busy === "reviewMerge" ? "Merging…" : "Confirm squash merge"}
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={() => setConfirmingMerge(false)}
              disabled={busy !== ""}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p className="m-0 text-[13px] text-red-500" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="m-0 text-xs text-muted" role="status">
          {notice}
        </p>
      ) : null}
    </section>
  );
}

export default ReviewActions;
