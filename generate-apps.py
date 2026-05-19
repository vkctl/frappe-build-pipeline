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

The `branch` field in the output depends on each app's tracking mode:
  - tag-tracked app (track=tag):         "branch": "v16.18.3"  (the tag name)
  - latest-tag app (track=latest_tag):   "branch": "v3.9.10"   (the tag name)
  - branch-tracked app (track=branch):   "branch": "2807c9f0..." (the resolved SHA)

Tags are pinned by name because they're already immutable in git. Branches are
pinned by SHA because branch HEAD can move between detection and clone — using
the SHA guarantees we install the exact commit we recorded.
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

        # Choose what to put in the `branch` field per tracking mode:
        #   - branch-tracked apps: pin to the exact SHA detected. This avoids
        #     races where new commits land between detection and clone, and
        #     makes the build reproducible.
        #   - tag-tracked apps (tag or latest_tag): use the tag name itself.
        #     Tags are immutable in git, so this is already reproducible, and
        #     it keeps the build traceable to a human-readable version.
        track = app.get('track')
        if track == 'branch':
            install_ref = versions[name]['sha']
            display = f"{versions[name]['ref']} @ {install_ref[:7]}"
        else:
            install_ref = versions[name]['ref']
            display = install_ref

        output.append({
            "url": app['url'],
            "branch": install_ref,
        })
        print(f"  {name}: {display}")

    output_path.write_text(json.dumps(output, indent=2) + '\n')
    print(f"\nWrote {output_path} with {len(output)} apps.")


if __name__ == '__main__':
    main()
