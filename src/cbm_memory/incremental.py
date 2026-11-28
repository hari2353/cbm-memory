"""Incremental reindex (cross-pollinated from Discord/Shopify change capture).

Given the set of changed file paths and a prior index, returns only the files
that need reindexing, so a reindex run touches the minimum. Pure Python.
"""

from __future__ import annotations


def changed_files(
    changed: set[str] | list[str], indexed: set[str] | list[str]
) -> list[str]:
    """Files that are new or changed (in changed but not in indexed, or in both)."""
    changed_set = set(changed)
    # New or modified: in changed. Removed: in indexed but not changed (skip).
    return sorted(changed_set)


def plan_reindex(changed: set[str] | list[str], indexed: set[str] | list[str]) -> dict:
    """Plan which files to reindex, update, or delete."""
    changed_set = set(changed)
    indexed_set = set(indexed)
    to_reindex = sorted(changed_set)  # new or modified
    to_delete = sorted(indexed_set - changed_set)  # removed
    unchanged = sorted(indexed_set & changed_set)  # in both (reindex to be safe)
    return {
        "to_reindex": to_reindex,
        "to_delete": to_delete,
        "unchanged": unchanged,
        "total": len(to_reindex) + len(to_delete),
    }
