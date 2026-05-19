#!/usr/bin/env python3
"""
Generate apps.json (Frappe Docker's input format) from upstream-apps.json
and the detector's output in .state/current-versions.json.

This is the single-source-of-truth generator: upstream-apps.json is the only
file listing apps. There is no APPS_JSON secret. Adding/removing apps means
editing upstream-apps.json.

Inputs:
  - upstream-apps.json (the app registry; committed in repo)
  - .state/current-versions.json (resolved refs from check-upstream.py)

Output:
  - apps.json (overwrites/creates; Frappe array-of-objects format)

The `branch` field in the output is the ref name the detector resolved:
  - tag-tracked app (track=tag):         "branch": "v16.18.3"   (the tag name)
  - latest-tag app (track=latest_tag):   "branch": "v3.9.10"    (the tag name)
  - branch-tracked app (track=branch):   "branch": "version-16" (the branch name)

Note: we don't pin to raw SHAs because Frappe's bench installer uses shallow
clones (git clone --depth 1 --branch <ref>), which don't accept arbitrary SHAs.
For branch-tracked apps this means there's a tiny race window where commits
landed between detection and clone — acceptable for our weekly cadence.
The detected SHA is still recorded in last-build.json for audit purposes.
"""

import json
import sys
from pathlib import Path


def main() -> None:
    config_path = Path('upstream-apps.json')
    versions_path = Path('.state/current-versions.json')
    output_path = Path('apps.json')

    if not config_path.exists():
        print(f"ERROR: {config_path} not found", file=sys.stderr)
        sys.exit(1)
    if not versions_path.exists():
        print(f"ERROR: {versions_path} not found "
              f"(check-upstream.py must run first)", file=sys.stderr)
        sys.exit(1)

    config = json.loads(config_path.read_text())
    versions = json.loads(versions_path.read_text())['apps']

    output = []
    for app in config['apps']:
        name = app['name']
        if name not in versions:
            print(f"ERROR: {name} is in upstream-apps.json but the detector "
                  f"didn't resolve a version for it. Check check-upstream.py output.",
                  file=sys.stderr)
            sys.exit(1)

        if 'url' not in app:
            print(f"ERROR: {name} in upstream-apps.json is missing the 'url' field. "
                  f"Add the GitHub URL Frappe should clone from.",
                  file=sys.stderr)
            sys.exit(1)

        # Use the resolved ref (tag name or branch name) for the `branch` field.
        # We can't use SHAs here even for branch-tracked apps because Frappe's
        # bench installer does `git clone --branch <ref> --depth 1`, and shallow
        # clones reject raw SHAs. Branch-tracked apps therefore have a small race
        # window (commits landing between detection and clone), but it's a ~5min
        # window once a week — acceptable for the simplicity.
        ref = versions[name]['ref']
        sha = versions[name]['sha']

        output.append({
            "url": app['url'],
            "branch": ref,
        })

        # Show SHA in the local log too, so we can audit what was actually built.
        print(f"  {name}: {ref} @ {sha[:7]}")

    output_path.write_text(json.dumps(output, indent=2) + '\n')
    print(f"\nWrote {output_path} with {len(output)} apps.")


if __name__ == '__main__':
    main()
