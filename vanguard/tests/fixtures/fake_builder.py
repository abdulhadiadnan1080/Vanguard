#!/usr/bin/env python3
"""Stand-in for the builder agent in tests. Reads AGENT_TASK.md on stdin like a real agent, then writes a REJECT
verdict (for GHSAs listed in STUB_REJECT) or a minimal valid task folder with check-only/producer-only variants."""
import json
import os
import re
import sys

job = sys.stdin.read()
task_dir = re.search(r"put the task folder at `([^`]+)`", job).group(1)
ghsa = re.search(r"^# Candidate (\S+)", open("brief.md").read(), re.M).group(1)
if ghsa in os.environ.get("STUB_REJECT", ""):
    json.dump({"verdict": "REJECT", "reason": "stub: the check calls the producer directly"}, open("verdict.json", "w"))
    sys.exit(0)
slug = os.path.basename(task_dir)
files = {
    "task.toml": '[task]\nname = "tasks/%s"\n\n[metadata]\nghsa = "%s"\nlanguage = "python"\n'
                 'repo = "https://github.com/acme/lib"\nfix_commit = "0123456789abcdef0123456789abcdef01234567"\n\n'
                 '[environment]\nnetwork_mode = "no-network"\n' % (slug, ghsa),
    "instruction.md": "An attacker can read another user's saved session.\n",
    "environment/Dockerfile": "FROM python:3.12-slim AS builder\nRUN git clone x /src && rm -rf /src/.git\n"
                              "FROM python:3.12-slim\nCOPY --from=builder /src /app\n",
    "solution/solve.sh": "#!/bin/bash\ngit apply fix.patch\n",
    "solution/fix.patch": "diff --git a/lib/check.py b/lib/check.py\n+a\ndiff --git a/lib/store.py b/lib/store.py\n+b\n",
    "solution/variants/check_only.patch": "diff --git a/lib/check.py b/lib/check.py\n+a\n",
    "solution/variants/producer_only.patch": "diff --git a/lib/store.py b/lib/store.py\n+b\n",
    "tests/test.sh": "#!/bin/bash\necho 1.0 > /logs/verifier/reward.txt\n",
}
for p, text in files.items():
    os.makedirs(os.path.dirname(os.path.join(task_dir, p)), exist_ok=True)
    with open(os.path.join(task_dir, p), "w") as f:
        f.write(text)
json.dump({"verdict": "BUILD", "reason": "stub build"}, open("verdict.json", "w"))
