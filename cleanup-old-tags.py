#!/usr/bin/env python3
"""
Prune old GHCR image versions for the 'frappe_stack' package, keeping only the most
recent N unique tags per environment (staging / prod), plus always preserving
moving pointers.

Why this exists instead of using actions/delete-package-versions:
  - That action operates on package "versions" (image digests). When you ask
    it to delete a version because of its tag, ALL tags on that image are lost.
    Our images often have both a staging tag and a prod tag (because prod
    promotion just retags the same image bytes). Deleting the version would
    take both with it.
  - We need: "untag this image's staging tag, but if the prod tag is still
    needed, keep the underlying image alive."

How this works:
  1. List all versions of the 'frappe_stack' package via GitHub API.
  2. For each version, look at its tags.
  3. Decide what to do with each tag:
     - Moving pointers (16-staging-latest, 16-prod-latest, 16-prod-backup): keep.
     - Recent unique tags (within KEEP_COUNT newest per env): keep.
     - Older unique tags: remove (just the tag, not necessarily the image).
  4. If a version has NO tags remaining after pruning, delete the version
     (GHCR auto-untags-and-removes-image then).
  5. If a version has tags remaining, leave it alone — GHCR doesn't support
     deleting individual tags via API, only whole versions. But that's fine:
     if a prod tag still references the image, we don't want it deleted anyway.

Reads:
  - GITHUB_TOKEN env var (workflow provides automatically)
  - GITHUB_REPOSITORY_OWNER env var (provided by workflow)
  - KEEP_COUNT env var (default 3)

Approach is conservative: when in doubt, keep. Better to leave a few extra
old tags than to accidentally break a deploy.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

API_ROOT = "https://api.github.com"
PACKAGE_NAME = "frappe_stack"
KEEP_COUNT = int(os.environ.get("KEEP_COUNT", "3"))
TOKEN = os.environ["GITHUB_TOKEN"]
OWNER = os.environ["GITHUB_REPOSITORY_OWNER"].lower()

# Tags that should ALWAYS be preserved (moving pointers, anyone depends on these).
ALWAYS_KEEP_TAGS = {
    "16-staging-latest",
    "16-prod-latest",
    "16-prod-backup",
}


def api(method: str, path: str) -> dict | list | None:
    """Call GitHub REST API. Returns parsed JSON or None for 204."""
    url = f"{API_ROOT}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status == 204:
                return None
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTP {e.code} on {method} {path}: {body}", file=sys.stderr)
        raise


def list_versions() -> list:
    """List all versions of the package. Paginates."""
    versions = []
    page = 1
    while True:
        # User packages endpoint. If you ever move this to an org, switch to /orgs/{org}/...
        path = f"/user/packages/container/{PACKAGE_NAME}/versions?per_page=100&page={page}"
        batch = api("GET", path)
        if not batch:
            break
        versions.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return versions


def delete_version(version_id: int) -> None:
    """Delete a package version by its numeric id."""
    api("DELETE", f"/user/packages/container/{PACKAGE_NAME}/versions/{version_id}")


def classify_tag(tag: str) -> tuple[str, str] | None:
    """
    Categorize a tag.
      Returns (env, sort_key) for prunable tags like 16-staging-20260523-1.
      Returns None for tags we don't manage (latest pointers, unknown formats).
    """
    if tag in ALWAYS_KEEP_TAGS:
        return None  # preserved unconditionally elsewhere
    # Match 16-staging-YYYYMMDD-N or 16-prod-YYYYMMDD-N
    for env in ("staging", "prod"):
        prefix = f"16-{env}-"
        if tag.startswith(prefix):
            rest = tag[len(prefix):]
            # rest should look like 20260523-1
            if "-" in rest and rest.split("-")[0].isdigit():
                # Sort key: date + run number, lexicographically sortable
                return (env, rest)
    return None


def main() -> None:
    print(f"Listing versions of package '{PACKAGE_NAME}'...")
    versions = list_versions()
    print(f"Found {len(versions)} total versions.")

    # Group prunable tags by environment, with their version IDs.
    # Each entry: (sort_key, tag, version_id)
    by_env: dict[str, list[tuple[str, str, int]]] = {"staging": [], "prod": []}

    for v in versions:
        vid = v["id"]
        tags = v.get("metadata", {}).get("container", {}).get("tags", []) or []
        for tag in tags:
            classified = classify_tag(tag)
            if classified is None:
                continue
            env, sort_key = classified
            by_env[env].append((sort_key, tag, vid))

    # For each env, sort newest-first and decide which tags fall outside KEEP_COUNT.
    # The N most recent unique tags are KEPT (so their version IDs go into the
    # "preserve" set). Older tags' version IDs go into the "candidate for deletion" set.
    preserve_version_ids: set[int] = set()
    deletion_candidates: set[int] = set()

    for env, items in by_env.items():
        items.sort(key=lambda x: x[0], reverse=True)  # newest first
        print(f"\n{env}: found {len(items)} unique-tag entries")
        for i, (sort_key, tag, vid) in enumerate(items):
            if i < KEEP_COUNT:
                preserve_version_ids.add(vid)
                print(f"  KEEP  {tag} (version {vid})")
            else:
                deletion_candidates.add(vid)
                print(f"  PRUNE {tag} (version {vid})")

    # Also preserve any version that currently has a moving pointer tag.
    # We must never delete the image those depend on.
    for v in versions:
        vid = v["id"]
        tags = v.get("metadata", {}).get("container", {}).get("tags", []) or []
        if any(t in ALWAYS_KEEP_TAGS for t in tags):
            if vid in deletion_candidates:
                deletion_candidates.discard(vid)
                print(f"\nProtected version {vid} from deletion (carries moving pointer: {tags})")
            preserve_version_ids.add(vid)

    # Also protect versions that have *both* a kept tag and an old tag.
    # (Already handled — preserve_version_ids takes precedence below.)
    deletion_candidates -= preserve_version_ids

    if not deletion_candidates:
        print("\nNothing to delete. Done.")
        return

    print(f"\nDeleting {len(deletion_candidates)} version(s)...")
    for vid in sorted(deletion_candidates):
        try:
            delete_version(vid)
            print(f"  deleted version {vid}")
        except Exception as e:
            print(f"  FAILED to delete version {vid}: {e}", file=sys.stderr)
    print("\nDone.")


if __name__ == "__main__":
    main()
