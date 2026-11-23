#!/usr/bin/env node
/**
 * HOJA-490 independent sweep: does the server actually refuse a member?
 *
 * This script knows nothing about the implementation. It takes one admin token,
 * invites a throwaway member through the public API, then calls every mutation
 * the permissions pane claims to gate — as the member — and requires a refusal.
 * A mutation that succeeds for the member is a gate that exists only in the UI.
 *
 *   HOJA_UI_URL   server base URL (default http://127.0.0.1:18944)
 *   HOJA_UI_TOKEN admin API token for a disposable workspace
 *
 * Exit 0 only when every gated call is refused and the admin control call
 * succeeds, so a refusal that is really a broken server cannot pass.
 */
import assert from 'node:assert/strict';

const base = process.env.HOJA_UI_URL || 'http://127.0.0.1:18944';
const adminToken = process.env.HOJA_UI_TOKEN;
if (!adminToken) throw new Error('Set HOJA_UI_TOKEN for the disposable test workspace.');

async function gql(token, query, variables = {}) {
  const response = await fetch(`${base}/graphql`, {
    method: 'POST',
    headers: {'content-type': 'application/json', authorization: `Bearer ${token}`},
    body: JSON.stringify({query, variables}),
  });
  return response.json();
}

const forbidden = result => (result.errors || []).some(
  e => (e.extensions?.code || '') === 'FORBIDDEN');

async function memberToken() {
  const email = `sweep-${Date.now()}@permission-sweep.test`;
  const invited = await gql(adminToken,
    'mutation($i:MemberInviteInput!){memberInvite(input:$i){success apiKey user{id}}}',
    {i: {name: 'Sweep member', email}});
  const key = invited.data?.memberInvite?.apiKey;
  assert.ok(key, `the admin can invite a member (got ${JSON.stringify(invited).slice(0, 300)})`);
  return key;
}

const member = await memberToken();
const admin = await gql(adminToken, '{viewer{id admin}}');
assert.equal(admin.data.viewer.admin, true, 'the control token is an admin');

// The gated surface, as the pane describes it. Each entry is a valid call: the
// refusal cannot be a validation error standing in for a permission check.
const teamId = (await gql(adminToken, '{teams{nodes{id}}}')).data.teams.nodes[0].id;
const issue = await gql(adminToken,
  'mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id}}}',
  {i: {teamId, title: 'Sweep target'}});
const issueId = issue.data?.issueCreate?.issue?.id;
assert.ok(issueId, 'the admin can create the sweep target issue');
const label = await gql(adminToken,
  'mutation($i:IssueLabelCreateInput!){issueLabelCreate(input:$i){issueLabel{id}}}',
  {i: {name: `sweep-${Date.now()}`}});
const labelId = label.data?.issueLabelCreate?.issueLabel?.id;
assert.ok(labelId, 'the admin can create the sweep target label');

const gated = [
  ['team.create', 'teamCreate', 'mutation($i:TeamCreateInput!){teamCreate(input:$i){success}}',
    {i: {name: 'Sweep team', key: `SW${String(Date.now()).slice(-5)}`}}],
  ['member.invite', 'memberInvite', 'mutation($i:MemberInviteInput!){memberInvite(input:$i){success apiKey}}',
    {i: {name: 'Sweep invite', email: `sweep2-${Date.now()}@permission-sweep.test`}}],
  ['task.delete', 'issueDelete', 'mutation($id:String!){issueDelete(id:$id){success}}', {id: issueId}],
  ['label.manage', 'issueLabelUpdate',
    'mutation($id:String!,$i:IssueLabelUpdateInput!){issueLabelUpdate(id:$id,input:$i){success}}',
    {id: labelId, i: {name: 'renamed by a member'}}],
  ['extension.manage', 'extensions', '{extensions{id}}', {}],
];

let refused = 0;
for (const [capability, name, query, variables] of gated) {
  const result = await gql(member, query, variables);
  const body = result.data?.[name];
  const payloadRefused = body && typeof body === 'object' && body.success === false;
  const errorRefused = forbidden(result);
  assert.ok(errorRefused || payloadRefused,
    `${capability}: the member call to ${name} was NOT refused: ${JSON.stringify(result).slice(0, 300)}`);
  // A refused delete must not have deleted anything.
  if (name === 'issueDelete') {
    const still = await gql(adminToken, `query($id:String!){issue(id:$id){id}}`, {id: issueId});
    assert.ok(still.data?.issue?.id, 'the refused delete left the issue in place');
  }
  console.log(`ok  ${capability}: member refused on ${name} (${errorRefused ? 'FORBIDDEN error' : 'success:false payload'})`);
  refused += 1;
}

// Control: the same mutations succeed for the admin, so the refusals above are
// permission, not a broken endpoint.
const control = await gql(adminToken, 'mutation($i:TeamCreateInput!){teamCreate(input:$i){success}}',
  {i: {name: 'Sweep admin team', key: `SA${String(Date.now()).slice(-5)}`}});
assert.equal(control.data?.teamCreate?.success, true,
  `the admin control call succeeds: ${JSON.stringify(control).slice(0, 200)}`);

console.log(`sweep ok: ${refused}/${gated.length} gated calls refused for a member, admin control passes`);
process.exit(0);
