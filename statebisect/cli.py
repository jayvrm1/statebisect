"""Command-line interface (CLI) for StateBisect."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import traceback
from pathlib import Path
from typing import Any

from .collector import (
    COLLECTOR_VERSION,
    CollectionRun,
    DiffChangeType,
    WindowsCollector,
    compare_snapshots,
)
from .models import CancellationToken, InvestigationStatus, MinimizationResult
from .reporting import generate_json_report, generate_text_report
from .scenarios import (
    ScenarioValidationError,
    execute_scenario,
    load_scenario_file,
)
from .storage import Storage

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "statebisect.db"
DEFAULT_SCENARIOS_DIR = Path("scenarios")


def _resolve_safe_path(path_str: str, allow_create_parent: bool = False) -> Path:
    """Validate path against directory traversal and return resolved Path."""
    clean_str = path_str.replace("\\", "/")
    if ".." in clean_str.split("/"):
        raise ValueError(f"Path traversal ('..') is not permitted: '{path_str}'")
    p = Path(path_str).resolve()
    if allow_create_parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_storage(db_path_str: str | None = None, data_dir_str: str | None = None) -> Storage:
    """Initialize storage at requested path or default data directory."""
    if db_path_str:
        db_path = _resolve_safe_path(db_path_str, allow_create_parent=True)
    elif data_dir_str:
        data_dir = _resolve_safe_path(data_dir_str, allow_create_parent=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "statebisect.db"
    else:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = DEFAULT_DB_PATH.resolve()

    return Storage(db_path)


def cmd_list_scenarios(args: argparse.Namespace) -> int:
    """List declarative scenarios in specified directory."""
    dir_path = (
        _resolve_safe_path(args.dir) if args.dir else DEFAULT_SCENARIOS_DIR.resolve()
    )
    if not dir_path.is_dir():
        print(f"Error: Directory '{dir_path}' does not exist.", file=sys.stderr)
        return 1

    json_files = sorted(dir_path.glob("*.json"))
    if not json_files:
        print(f"No scenario (.json) files found in '{dir_path}'.")
        return 0

    print(f"Found {len(json_files)} scenario files in '{dir_path}':\n")
    print(f"{'Filename':<25} {'Scenario ID':<30} {'Status':<12} {'Changes'}")
    print("-" * 75)

    for f in json_files:
        try:
            sc = load_scenario_file(f)
            print(f"{f.name:<25} {sc.id:<30} {'VALID':<12} {len(sc.changes)}")
        except Exception as e:
            print(f"{f.name:<25} {'<invalid>':<30} {'INVALID':<12} {e}")

    return 0


def cmd_validate_scenario(args: argparse.Namespace) -> int:
    """Validate a scenario file strictly."""
    try:
        sc_path = _resolve_safe_path(args.file)
        scenario = load_scenario_file(sc_path)
        print("✓ Scenario is valid and safely formatted.")
        print(f"  ID           : {scenario.id}")
        print(f"  Name         : {scenario.name}")
        print(f"  Description  : {scenario.description}")
        print(f"  Schema Ver   : {scenario.schema_version}")
        print(f"  Baseline ID  : {scenario.baseline_id}")
        print(f"  Changes      : {len(scenario.changes)}")
        print(f"  Health Check : {scenario.health_check.type}")
        print(f"  SHA-256 Hash : {scenario.content_hash}")
        return 0
    except (ScenarioValidationError, FileNotFoundError, ValueError) as err:
        print(f"Validation Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1


def cmd_run_scenario(args: argparse.Namespace) -> int:
    """Run a scenario through the deterministic delta-debugging simulation."""
    storage = None
    try:
        sc_path = _resolve_safe_path(args.file)
        scenario = load_scenario_file(sc_path)
        storage = get_storage(args.db, args.data_dir)

        cancellation_token = CancellationToken()

        def sigint_handler(signum, frame):
            print("\n[!] Cancellation requested by user (Ctrl+C). Halting gracefully after current trial reset...")
            cancellation_token.cancel("User issued SIGINT")

        prev_handler = signal.signal(signal.SIGINT, sigint_handler)

        try:
            print(f"Starting investigation for scenario: '{scenario.name}' ({scenario.id})")
            print(f"Baseline: {scenario.baseline_id} | Candidate Changes: {len(scenario.changes)}")
            inv, result = execute_scenario(scenario, storage, cancellation_token=cancellation_token)

            print("\n" + generate_text_report(inv, result))

            if result.verdict == "SUCCESS":
                return 0
            elif result.verdict == "CANCELLED":
                return 130
            else:
                return 1
        finally:
            signal.signal(signal.SIGINT, prev_handler)

    except Exception as err:
        print(f"Run Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_inspect_investigation(args: argparse.Namespace) -> int:
    """Inspect stored investigation and trial history."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        inv = storage.get_investigation(args.id)
        if not inv:
            print(f"Error: Investigation '{args.id}' not found.", file=sys.stderr)
            return 1

        trials = storage.get_trials(args.id)
        audit_logs = storage.get_audit_logs(args.id)

        if args.format == "json":
            out_obj = {
                "investigation": {
                    "id": inv.id,
                    "target_system": inv.target_system,
                    "symptom": inv.symptom,
                    "status": inv.status.value,
                    "baseline_id": inv.baseline_id,
                    "scenario_id": inv.scenario_id,
                    "scenario_hash": inv.scenario_hash,
                    "engine_version": inv.engine_version,
                    "created_at": inv.created_at,
                    "updated_at": inv.updated_at,
                },
                "trials_count": len(trials),
                "trials": [
                    {
                        "trial_id": t.trial_id,
                        "outcome": t.outcome.value,
                        "selected_changes": t.selected_change_ids,
                        "duration_ms": t.duration_ms,
                        "check_type": t.check_type,
                        "check_version": t.check_version,
                        "attempts_count": len(t.attempts),
                        "health_output": t.health_output,
                    }
                    for t in trials
                ],
                "audit_logs": [
                    {
                        "timestamp": log.timestamp,
                        "level": log.level,
                        "event": log.event_type,
                        "message": log.message,
                    }
                    for log in audit_logs
                ],
            }
            print(json.dumps(out_obj, indent=2))
            return 0

        # Default text format
        print("=" * 80)
        print(f"INVESTIGATION: {inv.id}")
        print("=" * 80)
        print(f"Target System   : {inv.target_system}")
        print(f"Status          : {inv.status.value}")
        print(f"Symptom         : {inv.symptom}")
        print(f"Baseline ID     : {inv.baseline_id}")
        if inv.scenario_id:
            print(f"Scenario ID     : {inv.scenario_id}")
            print(f"Scenario Hash   : {inv.scenario_hash[:16]}...")
        print(f"Created At      : {inv.created_at}")
        print(f"Updated At      : {inv.updated_at}")
        print("-" * 80)
        print(f"TRIALS RECORDED : {len(trials)}")
        for t in trials:
            c_str = ",".join(t.selected_change_ids) if t.selected_change_ids else "<baseline>"
            atts_str = f"({len(t.attempts)} atts)" if t.attempts else "(1 att)"
            print(f"  {t.trial_id:<14} {t.outcome.value:<12} {t.duration_ms:>6.2f}ms  {atts_str:<9} [{c_str}]")
        print("-" * 80)
        print(f"AUDIT LOG EVENTS: {len(audit_logs)}")
        for log in audit_logs:
            print(f"  [{log.timestamp}] {log.level:<7} {log.event_type:<20} {log.message}")
        print("=" * 80)
        return 0

    except Exception as err:
        print(f"Inspection Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_export_report(args: argparse.Namespace) -> int:
    """Export an investigation report to text or JSON."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        inv = storage.get_investigation(args.id)
        if not inv:
            print(f"Error: Investigation '{args.id}' not found.", file=sys.stderr)
            return 1

        trials = storage.get_trials(args.id)
        # Reconstruct MinimizationResult from trials
        suspected: list[str] = []
        is_1_minimal = inv.status == InvestigationStatus.VERIFIED
        verdict = inv.status.value
        rationale = f"Investigation status: {inv.status.value}"
        if trials:
            # Last failing trial or minimal trial
            failing_trials = [t for t in trials if t.outcome.value == "FAIL"]
            if failing_trials:
                suspected = sorted(min(failing_trials, key=lambda t: len(t.selected_change_ids)).selected_change_ids)

        result = MinimizationResult(
            investigation_id=inv.id,
            suspected_set=suspected,
            is_1_minimal=is_1_minimal,
            total_trials=len(trials),
            verdict=verdict,
            rationale=rationale,
            trials=trials,
        )

        if args.format == "json":
            content = json.dumps(generate_json_report(inv, result), indent=2)
        else:
            content = generate_text_report(inv, result)

        if args.output:
            out_path = _resolve_safe_path(args.output, allow_create_parent=True)
            out_path.write_text(content, encoding="utf-8")
            print(f"Report exported to '{out_path}'.")
        else:
            print(content)

        return 0

    except Exception as err:
        print(f"Export Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_collect_preview(args: argparse.Namespace) -> int:
    """Preview read-only collection sources, lookback window, and limits."""
    collector = WindowsCollector()
    lookback = args.lookback_minutes * 60 if getattr(args, "lookback_minutes", None) is not None else args.lookback_sec
    preview = collector.preview(
        lookback_sec=lookback,
        max_records=args.max_records,
    )
    print("=" * 80)
    print(f"STATEBISECT WINDOWS COLLECTOR PREVIEW (v{preview['collector_version']})")
    print("=" * 80)
    print("READ-ONLY GUARANTEE   : Read-only access only. Zero host modifications.")
    print(f"Host Platform         : {preview['os_platform']}")
    print(f"Intended Lookback     : {lookback}s")
    print(f"Record Limit / Source : {args.max_records}")
    print(f"Total Sources         : {preview['sources_count']}")
    print("-" * 80)
    print(f"{'Source ID':<20} {'Category':<12} {'Perms Required':<16} {'Description'}")
    print("-" * 80)
    for src in preview["sources"]:
        print(
            f"{src['source_id']:<20} {src['category']:<12} {src['permission_requirement']:<16} {src['description'][:30]}"
        )
    print("=" * 80)
    print("Note: All sources are read-only. No host modifications will occur during collection.")
    return 0


def cmd_collect_windows(args: argparse.Namespace) -> int:
    """Execute opt-in read-only collection on Windows host and persist snapshot."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        collector = WindowsCollector()
        lookback = args.lookback_minutes * 60 if getattr(args, "lookback_minutes", None) is not None else args.lookback_sec

        print(f"Starting read-only Windows collection (lookback={lookback}s, max_records={args.max_records})...")
        run = collector.collect(
            lookback_sec=lookback,
            max_records_per_source=args.max_records,
        )

        storage.save_collection_run(run)

        print("\n" + "=" * 80)
        print("COLLECTION RUN SUMMARY")
        print("=" * 80)
        print(f"Run ID            : {run.run_id}")
        print(f"Status            : {run.status}")
        print(f"OS Platform       : {run.os_platform}")
        print(f"Machine ID        : {run.machine_id}")
        print(f"Duration          : {run.duration_ms:.2f} ms")
        print(f"Total Records     : {len(run.records)}")
        print(f"Coverage Gaps     : {len(run.coverage_gaps)}")
        print("-" * 80)
        print(f"{'Source ID':<20} {'Category':<12} {'Status':<10} {'Records':<8} {'Gaps':<6} {'Notes'}")
        print("-" * 80)
        for s_id, s_info in run.summary.get("sources", {}).items():
            print(
                f"{s_id:<20} {s_info['category']:<12} {s_info['status']:<10} {s_info['record_count']:<8} {s_info['gap_count']:<6} {s_info['notes'][:20]}"
            )
        if run.coverage_gaps:
            print("-" * 80)
            print("COVERAGE GAPS OBSERVED:")
            for g in run.coverage_gaps:
                print(f"  [{g.category}] {g.source_id}: {g.reason}")
        print("=" * 80)
        print("Notice: Collected records are stored locally for correlation and diagnosis.")
        return 0

    except Exception as err:
        print(f"Collection Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_list_collections(args: argparse.Namespace) -> int:
    """List historical snapshot collection runs."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        runs = storage.list_collection_runs()
        if not runs:
            print("No collection runs found in database.")
            return 0

        print(f"Found {len(runs)} collection runs:\n")
        print(f"{'Run ID':<22} {'Created At':<26} {'Status':<10} {'Records':<8} {'Gaps'}")
        print("-" * 75)
        for r in runs:
            rec_count = r.summary.get("total_records", len(r.records))
            gap_count = r.summary.get("total_gaps", len(r.coverage_gaps))
            print(f"{r.run_id:<22} {r.created_at:<26} {r.status:<10} {rec_count:<8} {gap_count}")
        return 0
    except Exception as err:
        print(f"Error listing collections: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_inspect_collection(args: argparse.Namespace) -> int:
    """Inspect a collection run by ID."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        run = storage.get_collection_run(args.id)
        if not run:
            print(f"Error: Collection run '{args.id}' not found.", file=sys.stderr)
            return 1

        if args.format == "json":
            out_obj = {
                "run_id": run.run_id,
                "created_at": run.created_at,
                "finished_at": run.finished_at,
                "collector_version": run.collector_version,
                "os_platform": run.os_platform,
                "machine_id": run.machine_id,
                "status": run.status,
                "duration_ms": run.duration_ms,
                "summary": run.summary,
                "total_records": len(run.records),
                "total_gaps": len(run.coverage_gaps),
                "records_sample": [
                    {
                        "category": r.category,
                        "object_key": r.object_key,
                        "data": r.data,
                    }
                    for r in run.records[:10]
                ],
                "coverage_gaps": [
                    {
                        "source_id": g.source_id,
                        "category": g.category,
                        "reason": g.reason,
                        "impact": g.impact,
                    }
                    for g in run.coverage_gaps
                ],
            }
            print(json.dumps(out_obj, indent=2))
            return 0

        print("=" * 80)
        print(f"COLLECTION RUN: {run.run_id}")
        print("=" * 80)
        print(f"Timestamp       : {run.created_at}")
        print(f"Status          : {run.status}")
        print(f"OS Platform     : {run.os_platform}")
        print(f"Machine ID      : {run.machine_id}")
        print(f"Total Records   : {len(run.records)}")
        print(f"Coverage Gaps   : {len(run.coverage_gaps)}")
        print("-" * 80)
        print("RECORDS SUMMARY BY CATEGORY:")
        cat_counts: dict[str, int] = {}
        for r in run.records:
            cat_counts[r.category] = cat_counts.get(r.category, 0) + 1
        for cat, cnt in sorted(cat_counts.items()):
            print(f"  {cat:<18}: {cnt} items")
        if run.coverage_gaps:
            print("-" * 80)
            print("COVERAGE GAPS:")
            for g in run.coverage_gaps:
                print(f"  [{g.category}] {g.source_id}: {g.reason}")
        print("=" * 80)
        return 0

    except Exception as err:
        print(f"Inspection Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_compare_snapshots(args: argparse.Namespace) -> int:
    """Compare two collection runs and output normalized configuration diffs."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        before_run = storage.get_collection_run(args.before_id)
        if not before_run:
            print(f"Error: Baseline collection '{args.before_id}' not found.", file=sys.stderr)
            return 1
        after_run = storage.get_collection_run(args.after_id)
        if not after_run:
            print(f"Error: Target collection '{args.after_id}' not found.", file=sys.stderr)
            return 1

        diffs = compare_snapshots(before_run, after_run)
        storage.save_snapshot_diffs(diffs)

        if args.format == "json":
            out_obj = {
                "before_run_id": before_run.run_id,
                "after_run_id": after_run.run_id,
                "total_differences": len(diffs),
                "differences": [
                    {
                        "category": d.category,
                        "object_key": d.object_key,
                        "change_type": d.change_type.value,
                        "status": d.status,
                        "notes": d.notes,
                        "before_data": d.before_data,
                        "after_data": d.after_data,
                    }
                    for d in diffs
                ],
            }
            print(json.dumps(out_obj, indent=2))
            return 0

        print("=" * 80)
        print("SNAPSHOT COMPARISON MATRIX")
        print("=" * 80)
        print(f"Before Run  : {before_run.run_id} ({before_run.created_at})")
        print(f"After Run   : {after_run.run_id} ({after_run.created_at})")
        print(f"Total Diffs : {len(diffs)}")
        print("-" * 80)
        if not diffs:
            print("No differences observed between snapshots.")
        else:
            print(f"{'Category':<14} {'Change Type':<12} {'Object Key':<35} {'Notes'}")
            print("-" * 80)
            for d in diffs:
                print(f"{d.category:<14} {d.change_type.value:<12} {d.object_key[:34]:<35} {d.notes[:16]}")
        print("=" * 80)
        print("Warning: Observed configuration differences are correlations, not proven causes.")
        print("Controlled experiments and verification are required to establish causation.")
        return 0

    except Exception as err:
        print(f"Comparison Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def build_parser() -> argparse.ArgumentParser:
    """Construct top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="statebisect",
        description="StateBisect: Deterministic Windows configuration investigation system.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose error output and tracebacks.")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommand to execute")

    # list-scenarios
    p_list = subparsers.add_parser("list-scenarios", help="List declarative scenario files.")
    p_list.add_argument("dir", nargs="?", default=None, help="Directory containing scenarios (default: ./scenarios).")

    # validate-scenario
    p_val = subparsers.add_parser("validate-scenario", help="Strictly validate a scenario file.")
    p_val.add_argument("file", help="Path to scenario JSON file.")

    # run-scenario
    p_run = subparsers.add_parser("run-scenario", help="Run a scenario investigation trial.")
    p_run.add_argument("file", help="Path to scenario JSON file.")
    p_run.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_run.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # inspect-investigation
    p_ins = subparsers.add_parser("inspect-investigation", help="Inspect an investigation by ID.")
    p_ins.add_argument("id", help="Investigation ID to inspect.")
    p_ins.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_ins.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_ins.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # export-report
    p_exp = subparsers.add_parser("export-report", help="Export investigation report.")
    p_exp.add_argument("id", help="Investigation ID to export.")
    p_exp.add_argument("--format", choices=["text", "json"], required=True, help="Export format (text or json).")
    p_exp.add_argument("--output", "-o", default=None, help="Optional output file path.")
    p_exp.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_exp.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # collect-preview
    p_prev = subparsers.add_parser("collect-preview", help="Preview read-only collection sources and limits.")
    p_prev.add_argument("--lookback-sec", type=int, default=3600, help="Intended lookback window in seconds (default: 3600).")
    p_prev.add_argument("--lookback-minutes", type=int, default=None, help="Intended lookback window in minutes.")
    p_prev.add_argument("--max-records", type=int, default=100, help="Intended record limit per source (default: 100).")

    # collect-windows
    p_col = subparsers.add_parser("collect-windows", help="Run read-only Windows collection and save snapshot.")
    p_col.add_argument("--lookback-sec", type=int, default=3600, help="Lookback window in seconds (default: 3600).")
    p_col.add_argument("--lookback-minutes", type=int, default=None, help="Lookback window in minutes.")
    p_col.add_argument("--max-records", type=int, default=100, help="Record limit per source (default: 100).")
    p_col.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_col.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # list-collections
    p_lcol = subparsers.add_parser("list-collections", help="List saved snapshot collection runs.")
    p_lcol.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_lcol.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # inspect-collection
    p_icol = subparsers.add_parser("inspect-collection", help="Inspect a collection run and its records.")
    p_icol.add_argument("id", help="Collection run ID to inspect.")
    p_icol.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_icol.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_icol.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # compare-snapshots
    p_comp = subparsers.add_parser("compare-snapshots", help="Compare two collection snapshots.")
    p_comp.add_argument("before_id", help="Baseline collection run ID.")
    p_comp.add_argument("after_id", help="Target collection run ID.")
    p_comp.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_comp.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_comp.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI main entry point."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    if args.command == "list-scenarios":
        return cmd_list_scenarios(args)
    elif args.command == "validate-scenario":
        return cmd_validate_scenario(args)
    elif args.command == "run-scenario":
        return cmd_run_scenario(args)
    elif args.command == "inspect-investigation":
        return cmd_inspect_investigation(args)
    elif args.command == "export-report":
        return cmd_export_report(args)
    elif args.command == "collect-preview":
        return cmd_collect_preview(args)
    elif args.command == "collect-windows":
        return cmd_collect_windows(args)
    elif args.command == "list-collections":
        return cmd_list_collections(args)
    elif args.command == "inspect-collection":
        return cmd_inspect_collection(args)
    elif args.command == "compare-snapshots":
        return cmd_compare_snapshots(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
