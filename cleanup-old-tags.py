#!/usr/bin/env python3
"""
Prune old GHCR image versions for the 'frappe_stack' package.

Rolling window: one pool of build images tagged 16-build-YYYYMMDD-N, with three
moving pointer tags (16-staging-latest, 16-prod-latest, 16-prod-backup) that
point into that pool. We keep the last KEEP_COUNT builds and always protect any
build currently referenced by a pointer tag.

Legacy tags (16-staging-DATE-N, 16-prod-DATE-N from the old model) are also
collected and pruned, subject to the same pointer-protection rule.

Conservative approach: when in doubt, keep. Better to leave a stale tag than
to accidentally delete something a deploy depends on.
"""

import json
import os
import sys
import urllib.request
import urllib.error

API_ROOT = "https://api.github.com"
PACKAGE_NAME = "frappe_stack"
KEEP_COUNT = int(os.environ.get("KEEP_COUNT", "4"))
TOKEN = os.environ["GITHUB_TOKEN"]
OWNER = os.environ["GITHUB_REPOSITORY_OWNER"].lower()

POINTER_TAGS = {"16-staging-latest", "16-prod-latest", "16-prod-backup"}


def api(method: str, path: str) -> dict | list | None:
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
    versions = []
    page = 1
    while True:
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
    api("DELETE", f"/user/packages/container/{PACKAGE_NAME}/versions/{version_id}")


def sort_key(tag: str) -> str | None:
    """
    Return a lexicographically sortable key for prunable unique build tags.
    Handles current format (16-build-YYYYMMDD-N) and legacy formats
    (16-staging-YYYYMMDD-N, 16-prod-YYYYMMDD-N).
    Returns None for pointer tags or unrecognised formats.
    """
    if tag in POINTER_TAGS:
        return None
    for prefix in ("16-build-", "16-staging-", "16-prod-"):
        if tag.startswith(prefix):
            rest = tag[len(prefix):]  # YYYYMMDD-N
            parts = rest.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                # Zero-pad run number so lex sort == numeric sort.
                return f"{parts[0]}-{int(parts[1]):06d}"
    return None


def main() -> None:
    print(f"Listing versions of package '{PACKAGE_NAME}'...")
    versions = list_versions()
    print(f"Found {len(versions)} total versions.")

    # Pass 1: find which version IDs carry a pointer tag — always protected.
    pointer_version_ids: set[int] = set()
    for v in versions:
        tags = v.get("metadata", {}).get("container", {}).get("tags", []) or []
        if any(t in POINTER_TAGS for t in tags):
            pointer_version_ids.add(v["id"])

    # Pass 2: collect all prunable unique build tags with their sort keys.
    # (sort_key, tag_label, version_id)
    build_entries: list[tuple[str, str, int]] = []
    for v in versions:
        tags = v.get("metadata", {}).get("container", {}).get("tags", []) or []
        for tag in tags:
            key = sort_key(tag)
            if key is not None:
                build_entries.append((key, tag, v["id"]))

    build_entries.sort(key=lambda x: x[0], reverse=True)  # newest first
    print(f"\nFound {len(build_entries)} prunable unique tag(s):")

    preserve: set[int] = set(pointer_version_ids)
    candidates: set[int] = set()

    for i, (_, tag, vid) in enumerate(build_entries):
        protected_by_pointer = vid in pointer_version_ids
        keep = i < KEEP_COUNT or protected_by_pointer
        if keep:
            label = "KEEP (pointer)" if protected_by_pointer and i >= KEEP_COUNT else "KEEP"
            preserve.add(vid)
            print(f"  {label:<16} {tag} (version {vid})")
        else:
            candidates.add(vid)
            print(f"  PRUNE            {tag} (version {vid})")

    candidates -= preserve

    if not candidates:
        print("\nNothing to delete. Done.")
        return

    print(f"\nDeleting {len(candidates)} version(s)...")
    for vid in sorted(candidates):
        try:
            delete_version(vid)
            print(f"  deleted version {vid}")
        except Exception as e:
            print(f"  FAILED to delete version {vid}: {e}", file=sys.stderr)
    print("\nDone.")


if __name__ == "__main__":
    main()
