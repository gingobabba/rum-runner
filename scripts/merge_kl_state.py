#!/usr/bin/env python3
"""Merge KL Wines state from local state.json onto origin/main's state.json.

Used by the self-hosted job when a push is rejected because the public job
committed a newer state.json. Takes the remote as the base and applies
the local KL Wines state on top.
"""
import json
import subprocess
import sys

with open("state.json") as f:
    ours = json.load(f)

result = subprocess.run(
    ["git", "show", "origin/main:state.json"],
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print("Could not fetch origin/main:state.json — keeping local state", file=sys.stderr)
    sys.exit(0)

remote = json.loads(result.stdout)
remote.setdefault("retailers", {})["klwines"] = ours.get("retailers", {}).get("klwines", {})
remote["last_run"] = ours.get("last_run")

with open("state.json", "w") as f:
    json.dump(remote, f, indent=2)

print("Merged KL Wines state onto remote state.json")
