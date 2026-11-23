"""Serve Hoja with a fake tailnet, for scripts/test-tailnet-connect.cjs (HOJA-602). Not shipped.

The Tailscale API and Hoja's node are faked; "SSH" runs the command on this machine in the
throwaway HOME the test gives (FAKE_TAILNET_HOME), so the real installer really installs,
enrolls and starts a runner, which then shows up as the tailnet device ``office-mac``. The
device ``build-box`` answers like a machine without Tailscale SSH turned on.

    python scripts/fake_tailnet_serve.py serve --db ... --port ...
"""
from __future__ import annotations

import os
import subprocess
import sys

from hoja_server import cli, tailnet

SECRET = "tskey-client-kfake-SECRET"
TOKEN = "tskey-api-kfake-TOKEN123"
POLICY = {"text": open(os.path.join(os.path.dirname(__file__), "..", "tests", "agent", "fixtures",
                                    "tailscale_default_policy.hujson")).read(), "writes": 0}
DEVICES = [
    {"nodeId": "n-office", "name": "office-mac.tail1234.ts.net", "hostname": "office-mac",
     "os": "linux", "addresses": ["100.64.0.7"], "connectedToControl": True, "tags": [],
     "sshEnabled": True, "user": "ada@example.test"},
    {"nodeId": "n-studio", "name": "studio.tail1234.ts.net", "hostname": "studio",
     "os": "macOS", "addresses": ["100.64.0.10"], "connectedToControl": True, "tags": [],
     "sshEnabled": False},
    {"nodeId": "n-build", "name": "build-box.tail1234.ts.net", "hostname": "build-box",
     "os": "linux", "addresses": ["100.64.0.8"], "connectedToControl": True, "tags": []},
    {"nodeId": "n-pc", "name": "gamepc.tail1234.ts.net", "hostname": "gamepc",
     "os": "windows", "addresses": ["100.64.0.11"], "connectedToControl": True, "tags": []},
    {"nodeId": "n-phone", "name": "pixel.tail1234.ts.net", "hostname": "pixel",
     "os": "android", "addresses": ["100.64.0.9"], "connectedToControl": False, "tags": []},
]
_up = []


class API:
    def __init__(self, client_id, secret):
        self.secret = secret

    def devices(self):
        flag = os.environ.get("FAKE_TAILNET_SSH_ON")
        if flag and os.path.exists(flag):  # the person ran `sudo tailscale set --ssh` on studio
            next(d for d in DEVICES if d["hostname"] == "studio")["sshEnabled"] = True
        if self.secret != SECRET:
            raise tailnet.TailnetError("Tailscale refused this OAuth client. Check the client "
                                       "ID and secret, and that the client is not revoked.")
        return [tailnet._device(d) for d in DEVICES]

    def set_tags(self, device_id, tags):
        for d in DEVICES:
            if d["nodeId"] == device_id:
                d["tags"] = sorted(tags)


class TokenAPI(API):
    def __init__(self, token):
        super().__init__(None, SECRET)
        self.token = token

    def devices(self):
        if self.token != TOKEN:
            raise tailnet.TailnetError("Tailscale refused the token: it may have expired. Create "
                                       "a new one and paste it again.")
        return super().devices()

    def get_policy(self):
        return POLICY["text"], '"etag-%d"' % POLICY["writes"]

    def validate_policy(self, text):
        tailnet.standard_json(text)

    def set_policy(self, text, etag):
        POLICY["text"], POLICY["writes"] = text, POLICY["writes"] + 1

    def create_oauth_client(self, description, scopes, tags):
        return "kfake", SECRET


class Node:
    def __init__(self, org):
        pass

    def status(self):
        return {"state": "running" if _up else "stopped",
                "name": "hoja-acme.tail1234.ts.net" if _up else None}

    def up(self, secret, hostname):
        _up.append(hostname)
        return self.status()

    def down(self):
        _up.clear()


def ssh(node, address, user, command, *, on_output, timeout=0):
    if address == "100.64.0.8":
        raise tailnet.TailnetError("The machine answered, but not with Tailscale SSH. Turn it "
                                   "on once there: sudo tailscale set --ssh")
    home = os.environ["FAKE_TAILNET_HOME"]
    proc = subprocess.Popen(["bash", "-c", command], cwd=home, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env={
                                "PATH": os.environ["PATH"], "HOME": home,
                                "HOJA_RUNNER_SERVICE": "none", "HOJA_RUNNER_NAME": "office-mac",
                                "HOJA_CODER_MODEL_FACTORY": "coder_scripted_model:model",
                                "PYTHONPATH": os.environ.get("FAKE_TAILNET_PYTHONPATH", "")})
    for line in proc.stdout:
        on_output(line.rstrip("\n"))
    return proc.wait(), "ssh-ed25519 fake"


tailnet.api_factory, tailnet.node_factory, tailnet.ssh = API, Node, ssh
tailnet.token_api_factory = TokenAPI
sys.exit(cli.main(sys.argv[1:]))
