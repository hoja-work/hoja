"""Create N issues on a running backend so list views overflow. Usage: PROBE_URL=... PROBE_TOKEN=... python scripts/ui-probes/seed-many-issues.py 60"""
import json, os, sys, urllib.request

url = os.environ.get("PROBE_URL", "http://127.0.0.1:8797") + "/graphql"
token = os.environ["PROBE_TOKEN"]
count = int(sys.argv[1]) if len(sys.argv) > 1 else 60

def gql(query, variables=None):
    req = urllib.request.Request(url, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
    return json.load(urllib.request.urlopen(req, timeout=20))

teams = gql("query{teams(first:5){nodes{id key}}}")["data"]["teams"]["nodes"]
team = teams[0]["id"]
for i in range(count):
    r = gql("mutation($i:IssueCreateInput!){issueCreate(input:$i){success}}",
            {"i": {"teamId": team, "title": f"Probe issue {i + 1} for list scroll"}})
    if r.get("errors"):
        print(r["errors"][0]["message"]); break
print("created", count)
