"""Fail the commit if anything staged in git contains a real client, chauffeur,
passenger or address string from the analytics data.

This repository is public. The data file itself is gitignored, but names leak in
other ways — a chauffeur quoted as an example in a code comment, a route pasted
into a docstring, a client used in a test fixture. This checks the actual staged
blobs rather than the working tree, so it sees exactly what would be published.

    python backend/analytics/check_staged_pii.py

Exits non-zero on a hit. Wire it into .git/hooks/pre-commit to make it automatic:

    #!/bin/sh
    python business-limousine-crm/backend/analytics/check_staged_pii.py || exit 1

Needs backend/analytics/dashboard_data.json present to know what to look for; if
the real export is not on this machine the check cannot run and says so rather
than passing silently.
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import json
from make_sample import collect_identities  # noqa: E402

REAL = os.path.join(HERE, "dashboard_data.json")

# Strings that look like identities to collect_identities but are not: Waynium
# vocabulary and malformed entries that collide with ordinary words. Each one is
# a value that legitimately appears in source, so matching on it is pure noise.
ALLOWLIST = {
    "Interne",            # Waynium's code for "our own fleet", not a person
    ". Jean", ". Bilal",  # malformed name rows; collide with placeholder text
    ". AL KHEDER",
}


def main():
    if not os.path.exists(REAL):
        print("dashboard_data.json is not on this machine — cannot check staged "
              "content against real identities.")
        print("Run the pricing pipeline first, or review the diff by hand.")
        return 2

    identities = {i for i in collect_identities(json.load(open(REAL, encoding="utf-8")))
                  if i not in ALLOWLIST}

    files = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True,
    ).stdout.split()

    if not files:
        print("Nothing staged.")
        return 0

    hits = []
    for path in files:
        blob = subprocess.run(["git", "show", f":{path}"], capture_output=True).stdout
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary
        for ident in identities:
            if ident in text:
                hits.append((path, ident))

    print(f"Scanned {len(files)} staged file(s) against {len(identities)} real identities.")
    if not hits:
        print("Clean — nothing staged names a real client, chauffeur or passenger.")
        return 0

    print(f"\n{len(hits)} REAL IDENTITY STRING(S) STAGED:\n")
    for path, ident in hits:
        print(f"  {path}\n      {ident!r}")
    print("\nRemove or anonymise these before committing. If a match is a false "
          "positive (a Waynium code word, a placeholder), add it to ALLOWLIST in "
          f"{os.path.basename(__file__)} with a note saying why.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
