/* Decisions live on the server (docs/decisions.md): an agent asks a question on an issue,
 * a human answers it in the product, and the waiting agent polls the same row. This module
 * is the only place the browser talks to the decisions API; the issue detail imports its
 * types and calls, and keeps its own presentation state.
 *
 * The wire helper is local rather than imported from main.tsx because main.tsx imports
 * issues.tsx which imports this module: keeping the fetch here avoids an import cycle. It
 * reads the same token and endpoint.
 */

export type DecisionMediaType = 'image' | 'video' | 'link';

export type DecisionOption = {
  key: string;
  label: string;
  description?: string;
  mediaUrl?: string | null;
  mediaType?: DecisionMediaType | string | null;
};

export type IssueDecision = {
  id: string;
  question: string;
  options: DecisionOption[];
  typedOptions?: DecisionOption[];
  context?: string | null;
  status: 'open' | 'answered' | 'canceled';
  answered: boolean;
  askedByAgent?: string | null;
  askedBy?: {id: string; name?: string} | null;
  answer?: string | null;
  chosenOption?: string | null;
  answeredBy?: {id: string; name?: string} | null;
  answeredAt?: string | null;
  createdAt?: string | null;
};

export type DecisionAnswer = {option?: string; answer?: string};

export const decisionFields =
  'id question options typedOptions{key label description mediaUrl mediaType} context status answered askedByAgent askedBy{id name} answer chosenOption answeredBy{id name} answeredAt createdAt';

async function request<T>(query: string, variables?: Record<string, unknown>): Promise<T> {
  const stored = localStorage.getItem('hoja_token');
  const token = stored && stored !== 'session' ? stored : null;
  const response = await fetch('/graphql', {
    method: 'POST',
    credentials: 'include',
    headers: {'content-type': 'application/json', ...(token ? {authorization: `Bearer ${token}`} : {})},
    body: JSON.stringify({query, variables}),
  });
  const body = await response.json();
  if (!response.ok || body.errors) throw new Error(body.errors?.[0]?.message || 'Request failed');
  return body.data as T;
}

export async function listDecisions(issueId: string, status?: 'open' | 'answered' | 'canceled'): Promise<IssueDecision[]> {
  const data = await request<{issueDecisions: IssueDecision[]}>(
    `query IssueDecisions($issueId:String!,$status:String){issueDecisions(issueId:$issueId,status:$status){${decisionFields}}}`,
    {issueId, status: status ?? null});
  return data.issueDecisions ?? [];
}

export async function createDecision(input: {
  issueId: string; question: string; options?: DecisionOption[];
  context?: string; agent?: string;
}): Promise<IssueDecision> {
  const data = await request<{issueDecisionCreate: {success: boolean; decision: IssueDecision}}>(
    `mutation IssueDecisionCreate($input:IssueDecisionCreateInput!){issueDecisionCreate(input:$input){success decision{${decisionFields}}}}`,
    {input});
  if (!data.issueDecisionCreate?.success) throw new Error('The decision was rejected');
  return data.issueDecisionCreate.decision;
}

/* Answering is idempotent server-side: replaying the same answer returns the stored row
 * with replayed: true instead of overwriting the human's choice. */
export async function answerDecision(id: string, answer: DecisionAnswer): Promise<{decision: IssueDecision; replayed: boolean}> {
  const data = await request<{issueDecisionAnswer: {success: boolean; replayed: boolean; decision: IssueDecision}}>(
    `mutation IssueDecisionAnswer($id:String!,$input:IssueDecisionAnswerInput!){issueDecisionAnswer(id:$id,input:$input){success replayed decision{${decisionFields}}}}`,
    {id, input: answer});
  if (!data.issueDecisionAnswer?.success) throw new Error('The answer was rejected');
  return {decision: data.issueDecisionAnswer.decision, replayed: data.issueDecisionAnswer.replayed};
}

export async function cancelDecision(id: string): Promise<IssueDecision> {
  const data = await request<{issueDecisionCancel: {success: boolean; decision: IssueDecision}}>(
    `mutation IssueDecisionCancel($id:String!){issueDecisionCancel(id:$id){success decision{${decisionFields}}}}`,
    {id});
  if (!data.issueDecisionCancel?.success) throw new Error('The decision could not be canceled');
  return data.issueDecisionCancel.decision;
}
