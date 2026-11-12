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
import { useCallback, useEffect, useState } from 'react';

import './review-actions.css';

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
  state: string | null;
  refusalCode: string | null;
  refusalMessage: string | null;
};

type Props = {
  linkId: string;
  gql: Gql;
  /** Called only after the server confirmed the write, so the ledger can refresh from truth. */
  onChanged?: (result: { action: string; actor?: string | null; mergeSha?: string | null }) => void;
};

const GATE_QUERY = `query ReviewGate($id:String!){reviewGate(linkId:$id){
  sha mergeable mergeState checksState checksTotal checksPassing checksFailing checksPending
  state refusalCode refusalMessage}}`;

function shortSha(sha?: string | null): string {
  return sha ? sha.slice(0, 7) : 'unknown';
}

function gateLine(gate: ReviewGate): string {
  const parts = [
    `sha ${shortSha(gate.sha)}`,
    gate.checksState === 'unknown' || gate.checksState === 'none'
      ? 'no checks'
      : `${gate.checksPassing ?? 0}/${gate.checksTotal ?? 0} checks passing`,
    gate.mergeState ? `branch ${gate.mergeState}` : 'branch state unknown',
  ];
  return parts.join(' · ');
}

export function ReviewActions({ linkId, gql, onChanged }: Props) {
  const [gate, setGate] = useState<ReviewGate | null>(null);
  const [gateError, setGateError] = useState<string>('');
  const [busy, setBusy] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [notice, setNotice] = useState<string>('');
  const [body, setBody] = useState<string>('');
  const [composing, setComposing] = useState<'comment' | 'request' | null>(null);
  const [confirmingMerge, setConfirmingMerge] = useState(false);

  const loadGate = useCallback(() => {
    let live = true;
    setGateError('');
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

  const run = async (action: string, query: string, variables: Record<string, unknown>) => {
    setBusy(action);
    setError('');
    setNotice('');
    try {
      const data = await gql(query, variables);
      const payload = data?.[action] ?? null;
      if (!payload) throw new Error('The server did not confirm this action.');
      setNotice(
        payload.mergeSha
          ? `Merged ${shortSha(payload.sha)} as ${shortSha(payload.mergeSha)}. ${payload.actor || ''}`.trim()
          : `${payload.action} recorded. ${payload.actor || ''}`.trim(),
      );
      setComposing(null);
      setConfirmingMerge(false);
      setBody('');
      onChanged?.(payload);
      loadGate();
    } catch (e) {
      // Never mirror the action locally: the row keeps whatever GitHub last confirmed.
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const approve = () =>
    run('reviewApprove', `mutation($id:String!){reviewApprove(linkId:$id){action sha actor}}`, {
      id: linkId,
    });

  const comment = () =>
    run(
      'reviewComment',
      `mutation($id:String!,$body:String!){reviewComment(linkId:$id,body:$body){action sha actor}}`,
      { id: linkId, body },
    );

  const requestChanges = () =>
    run(
      'reviewRequestChanges',
      `mutation($id:String!,$body:String!){reviewRequestChanges(linkId:$id,body:$body){action sha actor}}`,
      { id: linkId, body },
    );

  const merge = () =>
    run(
      'reviewMerge',
      `mutation($id:String!,$sha:String!){reviewMerge(linkId:$id,sha:$sha){action sha mergeSha actor}}`,
      { id: linkId, sha: gate?.sha ?? '' },
    );

  const blocked = gate?.refusalCode ?? null;
  const gateReady = gate !== null && !gateError;

  return (
    <div className="review-actions" data-blocked={blocked ?? undefined}>
      <div className="review-actions-gate" role="status" aria-live="polite">
        {gateError ? (
          <span className="review-actions-error">Could not read the merge gate: {gateError}</span>
        ) : gate ? (
          <span title={gate.sha}>{gateLine(gate)}</span>
        ) : (
          <span>Reading the merge gate…</span>
        )}
      </div>

      <div className="review-actions-row">
        <button type="button" onClick={approve} disabled={!gateReady || busy !== ''}>
          {busy === 'reviewApprove' ? 'Approving…' : 'Approve'}
        </button>
        <button
          type="button"
          onClick={() => setComposing(composing === 'request' ? null : 'request')}
          disabled={!gateReady || busy !== ''}
        >
          Request changes
        </button>
        <button
          type="button"
          onClick={() => setComposing(composing === 'comment' ? null : 'comment')}
          disabled={!gateReady || busy !== ''}
        >
          Comment
        </button>
        <button
          type="button"
          className="review-actions-merge"
          onClick={() => setConfirmingMerge(true)}
          disabled={!gateReady || busy !== '' || blocked !== null}
          title={blocked ? (gate?.refusalMessage ?? blocked) : `Merge ${shortSha(gate?.sha)}`}
        >
          Squash and merge
        </button>
      </div>

      {blocked ? (
        <p className="review-actions-blocked" data-code={blocked}>
          {gate?.refusalMessage ?? 'This pull request cannot be merged right now.'}
        </p>
      ) : null}

      {composing ? (
        <div className="review-actions-compose">
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={
              composing === 'request' ? 'What needs to change?' : 'Leave a comment'
            }
            rows={3}
          />
          <button
            type="button"
            onClick={composing === 'request' ? requestChanges : comment}
            disabled={!body.trim() || busy !== ''}
          >
            {composing === 'request' ? 'Submit request' : 'Post comment'}
          </button>
        </div>
      ) : null}

      {confirmingMerge && gate ? (
        <div className="review-actions-confirm" role="alertdialog" aria-label="Confirm merge">
          <p>
            Merge commit <code title={gate.sha}>{gate.sha}</code>
          </p>
          <p className="review-actions-confirm-gate">{gateLine(gate)}</p>
          <button type="button" onClick={merge} disabled={busy !== ''}>
            {busy === 'reviewMerge' ? 'Merging…' : 'Confirm squash merge'}
          </button>
          <button type="button" onClick={() => setConfirmingMerge(false)} disabled={busy !== ''}>
            Cancel
          </button>
        </div>
      ) : null}

      {error ? <p className="review-actions-error">{error}</p> : null}
      {notice ? <p className="review-actions-notice">{notice}</p> : null}
    </div>
  );
}

export default ReviewActions;
