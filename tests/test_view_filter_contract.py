"""The web client's filter chips write documents the server's view grammar accepts.

``frontend/src/views/filterModel.ts`` is the only translation from the chip bar to the
view grammar, and ``hoja_server/views.py`` is the only parser. Each side has its own
table tests; this one joins them: node serializes a chip for every field and operator
the bar offers (single value, several values, the empty value where the field has one, "overdue",
with every operator of the field, even one the menu would not offer for those values)
and the server's ``parse_filters`` must accept every document. A chip the bar
offers but the server refuses would be a filter the person can build and never save.

Skipped when node (22+, for ``--experimental-strip-types``) is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hoja_server import views as viewdocs

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"

# One sample per field type, chosen to be valid for every property of that type.
SCRIPT = r"""
import {ISSUE_FIELDS, PROJECT_FIELDS, newChip, chipsToFilterTree, emptyGroup}
  from './src/views/filterModel.ts';

const samples = (kind, spec) => {
  switch (spec.type) {
    case 'relation': return [['parent'], ['sub'], ['top']];
    case 'content': case 'text': return [['crash']];
    case 'date': return [['2026-01-31'], ['-P1W'], ['today'],
      ...(['dueDate', 'targetDate'].includes(spec.server) ? [['overdue']] : [])];
    case 'number': return [[3], [1, 2]];
    case 'labels': return [['Bug'], ['Bug', 'UI']];
    case 'enum':
      if (spec.key === 'priority') return [[1], [1, 2]];
      if (spec.key === 'statusType') return [['started'], ['started', 'completed']];
      if (spec.key === 'health') return [['onTrack'], ['atRisk', 'offTrack']];
      return [];
    default:
      if (spec.server === 'team') return [['ENG'], ['ENG', 'myTeams']];
      if (['assignee', 'creator', 'subscriber', 'lead', 'members'].includes(spec.server))
        return [['me'], ['me', 'someone@example.test']];
      return [['Todo'], ['Todo', 'In Progress']];
  }
};

const out = [];
for (const [kind, fields] of [['issue', ISSUE_FIELDS], ['project', PROJECT_FIELDS]]) {
  for (const spec of fields) {
    const valueSets = samples(kind, spec);
    if (spec.nullable) valueSets.push([null], [null, ...valueSets[0]]);
    for (const values of valueSets) {
      // A chip can also hold an operator its values no longer allow (the value changed
      // after the operator was picked); effectiveOp must still write a valid document.
      for (const op of spec.ops) {
        if (values.length > 1 && ['date', 'text', 'content', 'relation'].includes(spec.type)) continue;
        const tree = chipsToFilterTree(newChip(spec.key, values, op, fields), fields);
        out.push({kind, field: spec.key, op, values, tree});
      }
    }
  }
  // Every chip of the kind, ANDed and ORed, in one document.
  const group = (conj) => ({...emptyGroup(conj), items: fields
    .filter((spec) => samples(kind, spec).length)
    .map((spec) => newChip(spec.key, samples(kind, spec)[0], spec.ops[0], fields))});
  for (const conj of ['and', 'or'])
    out.push({kind, field: '*', op: conj, values: [], tree: chipsToFilterTree(group(conj), fields)});
}
console.log(JSON.stringify(out));
"""


def _node_samples() -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
    if int(version.lstrip("v").split(".")[0] or 0) < 22:
        pytest.skip(f"node {version.strip()} cannot strip TypeScript types")
    result = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings",
         "--import", "./scripts/ts-resolve.mjs", "--input-type=module", "-e", SCRIPT],
        cwd=FRONTEND, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_every_chip_the_filter_bar_offers_is_valid_server_grammar() -> None:
    samples = _node_samples()
    fields = {(s["kind"], s["field"]) for s in samples}
    # The bar's whole catalog is exercised, not a subset that happens to parse.
    assert len({f for k, f in fields if k == "issue"}) >= 20
    assert len({f for k, f in fields if k == "project"}) >= 10
    refused = []
    for sample in samples:
        tree = sample["tree"]
        assert tree, sample
        where = f"{sample['kind']} {sample['field']} {sample['op']} {sample['values']}"
        try:
            parsed = viewdocs.parse_filters(sample["kind"], tree)
        except viewdocs.ViewError as exc:
            refused.append(f"{where}: {tree} refused at {exc.field}: {exc}")
            continue
        # The parser may normalise a value (Linear's "onTrack" is stored as "on track"),
        # but what it stores must read back unchanged.
        assert viewdocs.parse_filters(sample["kind"], parsed) == parsed, where
    assert not refused, "\n".join(refused)
