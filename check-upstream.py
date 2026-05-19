#!/usr/bin/env python3
"""
Check upstream Frappe app repos for changes since the last successful build.

Reads:
  - upstream-apps.json   (which repos to watch and how)
  - .state/last-build.json   (what we built from last time; may not exist on first run)

Writes:
  - .state/current-versions.json   (what's upstream right now, for the workflow to commit if a build succeeds)
  - GITHUB_OUTPUT variable `changed=true|false`
  - GITHUB_OUTPUT variable `summary=<human-readable diff>`

Uses GITHUB_TOKEN from env for API auth (avoids the 60/hr unauthenticated rate limit).
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

GITHUB_API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
STATE_DIR = Path(".state")
LAST_BUILD = STATE_DIR / "last-build.json"
CURRENT = STATE_DIR / "current-versions.json"
CONFIG = Path("upstream-apps.json")


def gh_api(path: str) -> dict | list:
    """Call the GitHub API and return parsed JSON. Exits hard on auth/repo errors."""
    req = urllib.request.Request(f"{GITHUB_API}{path}")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f"ERROR: GitHub API returned {e.code} for {path}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def resolve_version(app: dict) -> dict:
    """For one app, return {ref, sha} representing its current upstream state."""
    repo = app["repo"]
    track = app["track"]

    if track == "branch":
        # Get the head commit SHA of the named branch.
        branch = app["branch"]
        data = gh_api(f"/repos/{repo}/branches/{branch}")
        return {"ref": branch, "sha": data["commit"]["sha"]}

    if track == "latest_tag":
        # Get the most recent tag, whatever it's called.
        tags = gh_api(f"/repos/{repo}/tags?per_page=1")
        if not tags:
            print(f"ERROR: {repo} has no tags", file=sys.stderr)
            sys.exit(1)
        return {"ref": tags[0]["name"], "sha": tags[0]["commit"]["sha"]}

    if track == "tag":
        # Get tags matching a prefix. We paginate up to 100 tags — enough for any sane release cadence.
        prefix = app["tag_prefix"]
        tags = gh_api(f"/repos/{repo}/tags?per_page=100")
        matching = [t for t in tags if t["name"].startswith(prefix)]
        if not matching:
            print(f"ERROR: {repo} has no tags starting with '{prefix}'", file=sys.stderr)
            sys.exit(1)
        # GitHub returns tags newest-first, so matching[0] is the latest matching tag.
        return {"ref": matching[0]["name"], "sha": matching[0]["commit"]["sha"]}

    print(f"ERROR: unknown track type '{track}' for {repo}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    config = json.loads(CONFIG.read_text())
    last = json.loads(LAST_BUILD.read_text()) if LAST_BUILD.exists() else {"apps": {}}
    last_apps = last.get("apps", {})

    current = {}
    diff_lines = []
    changed = False

    for app in config["apps"]:
        name = app["name"]
        try:
            version = resolve_version(app)
        except Exception as e:
            print(f"ERROR resolving {name}: {e}", file=sys.stderr)
            sys.exit(1)

        current[name] = {"repo": app["repo"], **version}

        previous = last_apps.get(name)
        if previous is None:
            diff_lines.append(f"+ {name}: new ({version['ref']} @ {version['sha'][:7]})")
            changed = True
        elif previous.get("sha") != version["sha"]:
            diff_lines.append(
                f"~ {name}: {previous.get('ref','?')} @ {previous.get('sha','?')[:7]} "
                f"-> {version['ref']} @ {version['sha'][:7]}"
            )
            changed = True
        else:
            diff_lines.append(f"= {name}: {version['ref']} @ {version['sha'][:7]} (unchanged)")

    STATE_DIR.mkdir(exist_ok=True)
    CURRENT.write_text(json.dumps({"apps": current}, indent=2) + "\n")

    summary = "\n".join(diff_lines)
    print(summary)

    # Write workflow outputs.
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
            # Multi-line outputs use a heredoc-style delimiter.
            f.write("summary<<EOF_SUMMARY\n")
            f.write(summary + "\n")
            f.write("EOF_SUMMARY\n")


if __name__ == "__main__":
    main()
