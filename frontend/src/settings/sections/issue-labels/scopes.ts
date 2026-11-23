import { gql } from "../../ui";

export type LabelTeam = {
  id: string;
  name: string;
  key: string;
  parentTeamId?: string | null;
};
export type ScopedLabel = {
  id: string;
  name: string;
  color?: string | null;
  description?: string | null;
  /** The team that owns the label; null for a workspace label. */
  team: LabelTeam | null;
};
export type LabelCatalog = {
  labels: ScopedLabel[];
  teams: LabelTeam[];
  /** Label ids a team may use: its own, its ancestors', and every workspace label. */
  usableBy: (teamId: string | null) => Set<string>;
};

/* The API has no IssueLabel.team field. issueLabels(filter:{team}) returns a team's own
 * labels plus the ones it inherits, so one aliased query per team tells us who owns what:
 * a label no team lists is a workspace label; otherwise the owner is the listing team that
 * has no ancestor which also lists it. */
export async function loadLabelCatalog(): Promise<LabelCatalog> {
  const t = await gql<{ teams: { nodes: LabelTeam[] } }>(
    "query LabelTeams{teams(first:100){nodes{id name key parentTeamId}}}",
  );
  const teams = t.teams.nodes;
  const vars: Record<string, string> = {};
  const params = teams.map((team, i) => {
    vars[`t${i}`] = team.id;
    return `$t${i}:ID`;
  });
  const fields = teams.map(
    (_, i) =>
      `t${i}:issueLabels(filter:{team:{id:{eq:$t${i}}}},first:250){nodes{id}}`,
  );
  const d = await gql<Record<string, unknown>>(
    `query LabelCatalog${params.length ? `(${params.join(",")})` : ""}{issueLabelSettings{id name color description} ${fields.join(" ")}}`,
    vars,
  );
  const listedBy = new Map<string, Set<string>>();
  teams.forEach((team, i) => {
    const nodes = (d[`t${i}`] as { nodes: { id: string }[] } | null)?.nodes;
    for (const n of nodes || []) {
      if (!listedBy.has(n.id)) listedBy.set(n.id, new Set());
      listedBy.get(n.id)!.add(team.id);
    }
  });
  const byId = new Map(teams.map((x) => [x.id, x]));
  const ancestors = (id: string) => {
    const out: string[] = [];
    let cur = byId.get(id)?.parentTeamId;
    while (cur && !out.includes(cur)) {
      out.push(cur);
      cur = byId.get(cur)?.parentTeamId;
    }
    return out;
  };
  const raw = (d.issueLabelSettings || []) as Omit<ScopedLabel, "team">[];
  const labels: ScopedLabel[] = raw.map((l) => {
    const owners = listedBy.get(l.id);
    let team: LabelTeam | null = null;
    if (owners?.size) {
      const ownerId =
        [...owners].find((id) => !ancestors(id).some((a) => owners.has(a))) ||
        [...owners][0];
      team = byId.get(ownerId) || null;
    }
    return { ...l, team };
  });
  const usableBy = (teamId: string | null) => {
    const ids = new Set<string>();
    const lineage = teamId ? [teamId, ...ancestors(teamId)] : [];
    for (const l of labels)
      if (!l.team || lineage.includes(l.team.id)) ids.add(l.id);
    return ids;
  };
  return { labels, teams, usableBy };
}
