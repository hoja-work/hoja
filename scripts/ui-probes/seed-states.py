"""Seed issues into named workflow states so state views (Backlog, Todo) are populated.
Usage: PROBE_URL=... PROBE_TOKEN=... python scripts/ui-probes/seed-states.py 30
Creates N issues per state with the given team's stateId; uses existing team, no secrets are printed."""
import json, os, sys, urllib.request

url = os.environ.get("PROBE_URL", "http://127.0.0.1:8797") + "/graphql"
token = os.environ["PROBE_TOKEN"]
count = int(sys.argv[1]) if len(sys.argv) > 1 else 30

def gql(query, variables=None):
    req = urllib.request.Request(url, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
    data = json.load(urllib.request.urlopen(req, timeout=20))
    if data.get("errors"):
        raise SystemExit(data["errors"][0]["message"])
    return data["data"]

team = gql("query{teams(first:5){nodes{id key states{nodes{id name}}}}}")["teams"]["nodes"][0]
states = {s["name"]: s["id"] for s in team["states"]["nodes"]}
for name in ("Backlog", "Todo"):
    for i in range(count):
        gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}",
            {"i": {"teamId": team["id"], "title": f"{name} probe {i + 1} — a fairly long title to exercise wrapping in the list", "stateId": states[name]}})
print("seeded", count, "per state:", "Backlog", "Todo")
