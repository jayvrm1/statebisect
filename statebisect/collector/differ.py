"""Snapshot comparison engine producing normalized diffs with gap awareness."""

from __future__ import annotations

import json
import uuid
from typing import Any

from .models import CollectionRun, DiffChangeType, SnapshotDiff, SnapshotRecord


def compare_snapshots(before_run: CollectionRun, after_run: CollectionRun) -> list[SnapshotDiff]:
    """Compare two collection snapshots and produce normalized configuration differences.

    Safety Guarantee:
        If a category had a coverage gap or was incomplete in either run, missing items
        in that category are marked UNKNOWN rather than falsely inferred as REMOVED or MODIFIED.
    """
    diffs: list[SnapshotDiff] = []

    # Map coverage gaps by category for both runs
    before_gaps_by_cat = {gap.category for gap in before_run.coverage_gaps}
    after_gaps_by_cat = {gap.category for gap in after_run.coverage_gaps}

    # Index records by object_key
    before_map: dict[str, SnapshotRecord] = {r.object_key: r for r in before_run.records}
    after_map: dict[str, SnapshotRecord] = {r.object_key: r for r in after_run.records}

    all_keys = sorted(set(before_map.keys()) | set(after_map.keys()))

    for key in all_keys:
        before_rec = before_map.get(key)
        after_rec = after_map.get(key)

        category = before_rec.category if before_rec else (after_rec.category if after_rec else "unknown")
        has_gap = (category in before_gaps_by_cat) or (category in after_gaps_by_cat)

        diff_id = f"diff-{uuid.uuid4().hex[:10]}"

        if before_rec and not after_rec:
            # Item present in before, absent in after
            if has_gap:
                diffs.append(
                    SnapshotDiff(
                        diff_id=diff_id,
                        before_run_id=before_run.run_id,
                        after_run_id=after_run.run_id,
                        category=category,
                        object_key=key,
                        change_type=DiffChangeType.UNKNOWN,
                        before_data=before_rec.data,
                        after_data=None,
                        status="INCONCLUSIVE",
                        notes=f"Item not seen in second snapshot, but category '{category}' had coverage gaps.",
                    )
                )
            else:
                diffs.append(
                    SnapshotDiff(
                        diff_id=diff_id,
                        before_run_id=before_run.run_id,
                        after_run_id=after_run.run_id,
                        category=category,
                        object_key=key,
                        change_type=DiffChangeType.REMOVED,
                        before_data=before_rec.data,
                        after_data=None,
                        status="CONFIRMED",
                        notes=f"Item '{key}' removed in subsequent snapshot.",
                    )
                )

        elif not before_rec and after_rec:
            # Item absent in before, present in after
            if has_gap:
                diffs.append(
                    SnapshotDiff(
                        diff_id=diff_id,
                        before_run_id=before_run.run_id,
                        after_run_id=after_run.run_id,
                        category=category,
                        object_key=key,
                        change_type=DiffChangeType.UNKNOWN,
                        before_data=None,
                        after_data=after_rec.data,
                        status="INCONCLUSIVE",
                        notes=f"Item appeared in second snapshot, but category '{category}' had prior coverage gaps.",
                    )
                )
            else:
                diffs.append(
                    SnapshotDiff(
                        diff_id=diff_id,
                        before_run_id=before_run.run_id,
                        after_run_id=after_run.run_id,
                        category=category,
                        object_key=key,
                        change_type=DiffChangeType.ADDED,
                        before_data=None,
                        after_data=after_rec.data,
                        status="CONFIRMED",
                        notes=f"Item '{key}' added in subsequent snapshot.",
                    )
                )

        elif before_rec and after_rec:
            # Present in both: check if modified
            if before_rec.data != after_rec.data:
                diffs.append(
                    SnapshotDiff(
                        diff_id=diff_id,
                        before_run_id=before_run.run_id,
                        after_run_id=after_run.run_id,
                        category=category,
                        object_key=key,
                        change_type=DiffChangeType.MODIFIED,
                        before_data=before_rec.data,
                        after_data=after_rec.data,
                        status="CONFIRMED",
                        notes=f"Configuration for '{key}' changed between snapshots.",
                    )
                )

    return diffs
