/**
 * The four write actions for a linked pull request, as buttons.
 *
 * It is a separate module on purpose: `main.tsx`'s Reviews view is being rewritten by the 1:1
 * parity work, so this file touches nothing there and takes the GraphQL helper as a prop instead
 * of importing it. Mounting it is one import and one element inside the review detail pane.
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

  useEffect(() => loadGate(), [loadGate]);

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
      {
        id: linkId,
      },
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
    <div
      className={styleClasses(
        "flex flex-col gap-2 pt-3 px-0 pb-0 border-t border-t-(--border-subtle,rgba(128,128,128,0.2))",
      )}
      data-style-hooks={styleHooks(
        "review-actions flex flex-col gap-[8px] pt-[12px] px-0 pb-0 border-t border-t-(--border-subtle,rgba(128,128,128,0.2))",
      )}
      data-blocked={blocked ?? undefined}
    >
      <div
        className={styleClasses(
          "text-[12px] text-(--text-secondary,#6b7280) tabular-nums",
        )}
        data-style-hooks={styleHooks(
          "review-actions-gate text-[12px] text-(--text-secondary,#6b7280) tabular-nums",
        )}
        role="status"
        aria-live="polite"
      >
        {gateError ? (
          <span
            className={styleClasses("text-[12px] text-(--danger,#d1453b) m-0")}
            data-style-hooks={styleHooks(
              "review-actions-error text-[12px] text-(--danger,#d1453b) m-0",
            )}
          >
            Could not read the merge gate: {gateError}
          </span>
        ) : gate ? (
          <span title={gate.sha}>{gateLine(gate)}</span>
        ) : (
          <span>Reading the merge gate…</span>
        )}
      </div>

      <div
        className={styleClasses(
          "flex flex-wrap gap-1.5 [&_button]:[font:inherit] [&_button]:text-[12px] [&_button]:py-1.25 [&_button]:px-2.5 [&_button]:rounded-md [&_button]:border [&_button]:border-solid [&_button]:border-(--border-subtle,rgba(128,128,128,0.3)) [&_button]:bg-transparent [&_button]:text-inherit [&_button]:cursor-pointer [&_button:disabled]:opacity-50 [&_button:disabled]:cursor-not-allowed",
        )}
        data-style-hooks={styleHooks(
          "review-actions-row flex flex-wrap gap-[6px] [&_button]:[font:inherit] [&_button]:text-[12px] [&_button]:py-[5px] [&_button]:px-[10px] [&_button]:rounded-[6px] [&_button]:border [&_button]:border-solid [&_button]:border-(--border-subtle,rgba(128,128,128,0.3)) [&_button]:bg-transparent [&_button]:text-inherit [&_button]:cursor-pointer [&_button:disabled]:opacity-50 [&_button:disabled]:cursor-not-allowed",
        )}
      >
        <button
          type="button"
          onClick={approve}
          disabled={!gateReady || busy !== ""}
        >
          {busy === "reviewApprove" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          onClick={() =>
            setComposing(composing === "request" ? null : "request")
          }
          disabled={!gateReady || busy !== ""}
        >
          Request changes
        </button>
        <button
          type="button"
          onClick={() =>
            setComposing(composing === "comment" ? null : "comment")
          }
          disabled={!gateReady || busy !== ""}
        >
          Comment
        </button>
        <button
          className={styleClasses(
            "bg-(--accent,#5e6ad2) border-transparent text-white",
          )}
          data-style-hooks={styleHooks(
            "review-actions-merge bg-(--accent,#5e6ad2) border-transparent text-white",
          )}
          type="button"
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
        <p
          className={styleClasses("text-[12px] text-(--danger,#d1453b) m-0")}
          data-style-hooks={styleHooks(
            "review-actions-blocked text-[12px] text-(--danger,#d1453b) m-0",
          )}
          data-code={blocked}
        >
          {gate?.refusalMessage ??
            "This pull request cannot be merged right now."}
        </p>
      ) : null}

      {composing ? (
        <div
          className={styleClasses(
            "[&_textarea]:w-full [&_textarea]:[font:inherit] [&_textarea]:text-[13px] [&_textarea]:p-2 [&_textarea]:rounded-md [&_textarea]:border [&_textarea]:border-solid [&_textarea]:border-(--border-subtle,rgba(128,128,128,0.3)) [&_textarea]:bg-transparent [&_textarea]:text-inherit [&_textarea]:resize-y",
          )}
          data-style-hooks={styleHooks(
            "review-actions-compose [&_textarea]:w-full [&_textarea]:[font:inherit] [&_textarea]:text-[13px] [&_textarea]:p-[8px] [&_textarea]:rounded-[6px] [&_textarea]:border [&_textarea]:border-solid [&_textarea]:border-(--border-subtle,rgba(128,128,128,0.3)) [&_textarea]:bg-transparent [&_textarea]:text-inherit [&_textarea]:resize-y",
          )}
        >
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={
              composing === "request"
                ? "What needs to change?"
                : "Leave a comment"
            }
            rows={3}
          />
          <button
            type="button"
            onClick={composing === "request" ? requestChanges : comment}
            disabled={!body.trim() || busy !== ""}
          >
            {composing === "request" ? "Submit request" : "Post comment"}
          </button>
        </div>
      ) : null}

      {confirmingMerge && gate ? (
        <div
          className={styleClasses(
            "flex flex-col gap-1.5 p-2.5 rounded-md border border-solid border-(--danger,#d1453b) [&_p]:m-0 [&_p]:text-[13px] [&_code]:font-[ui-monospace,monospace]",
          )}
          data-style-hooks={styleHooks(
            "review-actions-confirm flex flex-col gap-[6px] p-[10px] rounded-[6px] border border-solid border-(--danger,#d1453b) [&_p]:m-0 [&_p]:text-[13px] [&_code]:font-[ui-monospace,monospace]",
          )}
          role="alertdialog"
          aria-label="Confirm merge"
        >
          <p>
            Merge commit <code title={gate.sha}>{gate.sha}</code>
          </p>
          <p
            className={styleClasses(
              "text-(--text-secondary,#6b7280) text-[12px]",
            )}
            data-style-hooks={styleHooks(
              "review-actions-confirm-gate text-(--text-secondary,#6b7280) text-[12px]",
            )}
          >
            {gateLine(gate)}
          </p>
          <button type="button" onClick={merge} disabled={busy !== ""}>
            {busy === "reviewMerge" ? "Merging…" : "Confirm squash merge"}
          </button>
          <button
            type="button"
            onClick={() => setConfirmingMerge(false)}
            disabled={busy !== ""}
          >
            Cancel
          </button>
        </div>
      ) : null}

      {error ? (
        <p
          className={styleClasses("text-[12px] text-(--danger,#d1453b) m-0")}
          data-style-hooks={styleHooks(
            "review-actions-error text-[12px] text-(--danger,#d1453b) m-0",
          )}
        >
          {error}
        </p>
      ) : null}
      {notice ? (
        <p
          className={styleClasses(
            "text-[12px] text-(--text-secondary,#6b7280) m-0",
          )}
          data-style-hooks={styleHooks(
            "review-actions-notice text-[12px] text-(--text-secondary,#6b7280) m-0",
          )}
        >
          {notice}
        </p>
      ) : null}
    </div>
  );
}

export default ReviewActions;

// Legacy styling hooks are data attributes; only Tailwind utilities reach className.
const legacyStyleHooks = new Set([
  "lin-page",
  "lin-scroll",
  "lin-head",
  "lin-crumb",
  "crumb-link",
  "sep",
  "current",
  "crumb-id",
  "lin-head-actions",
  "lin-nav-count",
  "lin-icon-btn",
  "active",
  "bordered",
  "lin-tip",
  "lin-kbd",
  "lin-menu",
  "lin-menu-item",
  "danger",
  "lin-menu-icon",
  "lin-menu-label",
  "lin-menu-divider",
  "lin-popover",
  "lin-viewbar",
  "lin-tabs",
  "lin-tab",
  "lin-viewchips",
  "lin-chipbtn",
  "lin-toolbar",
  "lin-search",
  "spacer",
  "lin-filterbar",
  "lin-fchip",
  "lin-filtermenu",
  "fm-search",
  "fm-item",
  "fm-ic",
  "fm-val",
  "fm-sub",
  "fm-opt",
  "fm-back",
  "lin-display",
  "dp-sec",
  "dp-title",
  "dp-row",
  "dp-seg",
  "dp-foot",
  "lin-check",
  "lin-list",
  "lin-group",
  "lin-group-head",
  "chev",
  "count",
  "grow",
  "add",
  "lin-row",
  "selected",
  "rowcheck",
  "ident",
  "title",
  "meta",
  "date",
  "lin-status-age",
  "lin-status-age-tip",
  "lin-avatar",
  "lin-chip",
  "lin-chip-x",
  "lin-subissue-pill",
  "overdue",
  "lin-empty",
  "lin-multibar",
  "n",
  "div",
  "lin-loadmore",
  "lin-error",
  "lin-board",
  "lin-bcol-hidden",
  "lin-hidden-col",
  "show",
  "lin-bcol",
  "sp",
  "lin-bcards",
  "lin-bcard",
  "top",
  "avatar",
  "ttl",
  "row",
  "assignee-name",
  "chips",
  "lin-bempty",
  "lin-detail",
  "lin-detail-main",
  "lin-detail-title",
  "lin-detail-desc",
  "lin-detail-empty",
  "lin-desc-edit",
  "empty",
  "lin-detail-actions",
  "lin-subsec",
  "lin-subrow",
  "t",
  "pending",
  "lin-activity",
  "lin-subscribe-btn",
  "lin-event",
  "lin-comment",
  "lin-comment-edited",
  "md",
  "del",
  "lin-commentbox",
  "lin-attach-list",
  "lin-attach",
  "thumb",
  "lin-props",
  "lin-prop",
  "k",
  "v",
  "plain",
  "trunc",
  "sec",
  "lin-addrow",
  "lin-ts",
  "lin-sublist",
  "lin-dd",
  "lin-dd-panel",
  "lin-dd-opt",
  "lin-cycles-head",
  "lin-cycles-wrap",
  "lin-cycle-workbench",
  "lin-cycle-browser",
  "lin-cycle-nav-head",
  "lin-cycles-tl",
  "lin-cycles-spine",
  "lin-cycle-row",
  "lin-cycle-tmark",
  "now",
  "lin-cycle-body",
  "cname",
  "lin-cycle-name-text",
  "lin-cycle-select-chevron",
  "stats",
  "range",
  "lin-badge",
  "lin-cap",
  "hot",
  "lin-cycle-detail-btn",
  "lin-cycle-chart",
  "chart",
  "legend",
  "lr",
  "sw",
  "lin-cycle-preview",
  "lin-cycle-preview-content",
  "lin-cycle-preview-head",
  "lin-cycle-preview-kicker",
  "lin-cycle-live-dot",
  "lin-cycle-open",
  "lin-cycle-preview-progress",
  "lin-cycle-progress-ring",
  "lin-cycle-preview-stats",
  "lin-cycle-preview-chart",
  "lin-cycle-preview-work",
  "lin-burndown",
  "lin-cycle-issue-id",
  "lin-cycle-issue-title",
  "lin-cycle-preview-empty",
  "lin-cycle-preview-more",
  "lin-cycle-stat-label",
  "lin-cdetail",
  "lin-cdetail-main",
  "lin-cdetail-count",
  "lin-cpane",
  "cur",
  "adddoc",
  "datenote",
  "datebtn",
  "lin-progress-head",
  "lin-ctabs",
  "lin-ctab",
  "lin-statrow",
  "nm",
  "pc",
  "axis",
  "grid",
  "lin-csettings",
  "desc",
  "card",
  "f",
  "lin-auto-row",
  "row2",
  "lin-btn",
  "primary",
  "lin-label-tip",
  "t1",
  "t2",
  "t3",
  "lin-loading",
  "lin-note",
  "lin-card-ctl",
  "lin-card-btn",
  "lin-card-menu",
  "lin-card-due",
  "lin-card-pop",
  "lin-bcard-error",
  "lin-dd-right",
  "lin-action-notice",
  "error",
  "lin-df",
  "lin-decisions",
  "lin-decision",
  "lin-decision-kicker",
  "lin-decision-dismiss",
  "lin-decision-q",
  "lin-decision-context",
  "lin-decision-actions",
  "lin-decision-option",
  "lin-decision-free",
  "lin-decision-meta",
  "lin-decision-settled",
  "lin-decision-answer",
  "ttl-row",
  "lin-priority-chip",
  "ds-split",
  "ds-split-ledger",
  "ds-split-detail",
  "ds-scroll",
  "ds-scroll--list",
  "ds-scroll--detail",
  "ds-scroll--diff",
  "ds-tabstrip",
  "ds-tab",
  "ds-group-bar",
  "ds-list-row",
  "ds-list-row-main",
  "ds-prop",
  "ds-prop-label",
  "ds-prop-value",
  "ds-empty",
  "ds-composer",
  "ds-composer-input",
  "ds-composer-action",
  "review-actions",
  "review-actions-gate",
  "review-actions-row",
  "review-actions-merge",
  "review-actions-blocked",
  "review-actions-error",
  "review-actions-notice",
  "review-actions-compose",
  "review-actions-confirm",
  "review-actions-confirm-gate",
  "lin-label-chip",
  "lin-project-chip",
  "lin-cycle-chip",
  "lin-due-chip",
  "lin-priority",
  "lin-cycle-glyph",
  "lin-ring",
  "done",
]);
const styleClasses = (value?: string) =>
  value
    ?.split(/\s+/)
    .filter((token) => !legacyStyleHooks.has(token))
    .join(" ");
const styleHooks = (value?: string) =>
  value
    ?.split(/\s+/)
    .filter((token) => legacyStyleHooks.has(token))
    .join(" ") || undefined;
