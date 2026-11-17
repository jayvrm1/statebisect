"""Command-line interface (CLI) for StateBisect."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

from .collector import (
    COLLECTOR_VERSION,
    CollectionRun,
    DiffChangeType,
    SnapshotDiff,
    WindowsCollector,
    compare_snapshots,
)
from .correlation import (
    CandidateChange,
    CandidateEvidenceType,
    CandidateRanker,
    CandidateReversibility,
    CandidateReviewRecord,
    CandidateReviewStatus,
    CorrelationEngine,
    CorrelationWindow,
    ScoreBreakdown,
)
from .adapters import (
    AdapterOperationResult,
    AdapterRegistry,
    AdapterType,
    BaseEnvironmentAdapter,
    BaselineRecord,
    EnvironmentRecord,
    EnvironmentStatus,
    FakeAdapter,
    HyperVAdapter,
    IsolatedTrialRecord,
    IsolatedTrialRunner,
    RegisteredActionRegistry,
    SimulationAdapter,
    TrialLifecycleState,
    WindowsSandboxEvaluation,
)
from .correlation.engine import parse_iso_or_user_time
from .delta_debugging import DeltaDebugger
from .models import (
    CancellationToken,
    Change,
    Investigation,
    InvestigationStatus,
    MinimizationResult,
    utc_now_iso,
)
from .monitor import (
    DEFAULT_DURATION_SEC,
    MAX_DURATION_SEC,
    MIN_DURATION_SEC,
    MonitoringEngine,
    MonitoringEvent,
    MonitoringSession,
)
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


def cmd_monitor_preview(args: argparse.Namespace) -> int:
    """Preview available monitoring sources, permissions, and duration limits."""
    engine = MonitoringEngine(allow_non_windows=True)
    prev = engine.preview()
    print("=" * 80)
    print("STATEBISECT TARGETED CHANGE MONITORING PREVIEW")
    print("=" * 80)
    print("READ-ONLY GUARANTEE   : Read-only access only. Zero host modifications.")
    print(f"Host Platform         : {prev['platform']}")
    print(f"Duration Limits       : Min {prev['min_duration_sec']}s, Default {prev['default_duration_sec']}s, Max {prev['max_duration_sec']}s")
    print(f"Max Events Buffer     : {prev['max_total_events']}")
    print(f"Total Sources         : {prev['sources_count']}")
    print("-" * 80)
    print(f"{'Source ID':<18} {'Category':<12} {'Supported':<10} {'Permissions':<22} {'Description'}")
    print("-" * 80)
    for src in prev["sources"]:
        supp_str = "YES" if src["supported"] else "NO"
        print(
            f"{src['source_id']:<18} {src['category']:<12} {supp_str:<10} {src['permission_requirement']:<22} {src['description'][:24]}"
        )
    print("=" * 80)
    print("Notice: Monitoring is temporary, opt-in, foreground-only, and bounded.")
    print("No startup tasks, scheduled jobs, or permanent background services are installed.")
    return 0


def cmd_monitor_start(args: argparse.Namespace) -> int:
    """Execute a foreground, bounded change monitoring session."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        engine = MonitoringEngine()

        duration = args.duration
        sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
        with_snapshots = args.with_snapshots

        print("=" * 80)
        print("STATEBISECT TARGETED CHANGE MONITORING SESSION")
        print("=" * 80)
        print(f"Target Duration   : {duration}s")
        print(f"Sources Requested : {', '.join(sources) if sources else 'All Default'}")
        print(f"Pre/Post Snapshot : {'YES' if with_snapshots else 'NO'}")
        print("Notice            : Monitoring is temporary and read-only.")
        print("                    Press Ctrl+C to safely stop and flush session at any time.")
        print("-" * 80)
        print(f"{'Timestamp (UTC)':<24} {'Source':<16} {'Channel / Path':<22} {'Event':<14} {'Attribution'}")
        print("-" * 80)

        token = CancellationToken()

        # Wire Ctrl+C signal safely
        old_sigint = signal.getsignal(signal.SIGINT)

        def sigint_handler(signum, frame):
            print("\n[Ctrl+C received - stopping monitoring session gracefully...]")
            token.cancel()

        signal.signal(signal.SIGINT, sigint_handler)

        def on_event(ev: MonitoringEvent) -> None:
            proc_info = f"PID:{ev.process_id}" if ev.process_id else "N/A"
            if ev.process_name:
                proc_info += f" ({ev.process_name})"
            print(f"{ev.timestamp_utc[:19]:<24} {ev.source_id:<16} {ev.channel_or_path[:21]:<22} {ev.event_code[:13]:<14} {proc_info}")

        try:
            session = engine.run_session(
                duration_sec=duration,
                sources_filter=sources,
                with_snapshots=with_snapshots,
                cancellation_token=token,
                storage=storage,
                event_callback=on_event,
            )
        finally:
            signal.signal(signal.SIGINT, old_sigint)

        print("\n" + "=" * 80)
        print("MONITORING SESSION SUMMARY")
        print("=" * 80)
        print(f"Session ID        : {session.session_id}")
        print(f"Status            : {session.status.value}")
        print(f"Stop Reason       : {session.stop_reason}")
        print(f"Started At        : {session.started_at}")
        print(f"Stopped At        : {session.stopped_at}")
        print(f"Elapsed Time      : {session.elapsed_sec:.2f}s (Requested: {session.duration_sec}s)")
        print(f"Total Events      : {session.total_events}")
        print(f"Sources Active    : {', '.join(session.sources_active) if session.sources_active else 'None'}")
        print(f"Coverage Gaps     : {len(session.coverage_gaps)}")
        if session.pre_snapshot_run_id:
            print(f"Pre-Snapshot ID   : {session.pre_snapshot_run_id}")
        if session.post_snapshot_run_id:
            print(f"Post-Snapshot ID  : {session.post_snapshot_run_id}")
        print("-" * 80)
        print("EVENTS BY SOURCE:")
        if not session.events_by_source:
            print("  No events captured.")
        else:
            for src_name, count in sorted(session.events_by_source.items()):
                print(f"  {src_name:<18}: {count} events")
        if session.coverage_gaps:
            print("-" * 80)
            print("COVERAGE GAPS / LIMITATIONS:")
            for g in session.coverage_gaps:
                print(f"  [{g.category}] {g.source_id}: {g.reason}")
        print("=" * 80)
        print("Warning: Monitoring events are temporal observations, not causal explanations.")
        print("Controlled experiments and verification are required to establish causation.")
        print("=" * 80)
        return 0

    except Exception as err:
        print(f"Monitoring Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_list_monitor_sessions(args: argparse.Namespace) -> int:
    """List recorded monitoring sessions in database."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        sessions = storage.list_monitoring_sessions()
        if not sessions:
            print("No monitoring sessions found in database.")
            return 0

        print(f"Found {len(sessions)} monitoring session(s):\n")
        print(f"{'Session ID':<18} {'Started At':<26} {'Status':<12} {'Elapsed':<10} {'Events':<8} {'Gaps'}")
        print("-" * 80)
        for s in sessions:
            print(f"{s.session_id:<18} {s.started_at:<26} {s.status.value:<12} {s.elapsed_sec:>6.2f}s    {s.total_events:<8} {len(s.coverage_gaps)}")
        return 0
    except Exception as err:
        print(f"Error listing sessions: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_inspect_monitor_session(args: argparse.Namespace) -> int:
    """Inspect a monitoring session and its observed events."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        session = storage.get_monitoring_session(args.id)
        if not session:
            print(f"Error: Monitoring session '{args.id}' not found.", file=sys.stderr)
            return 1

        if args.format == "json":
            out_obj = {
                "session_id": session.session_id,
                "created_at": session.created_at,
                "started_at": session.started_at,
                "stopped_at": session.stopped_at,
                "duration_sec": session.duration_sec,
                "elapsed_sec": session.elapsed_sec,
                "status": session.status.value,
                "stop_reason": session.stop_reason,
                "pre_snapshot_run_id": session.pre_snapshot_run_id,
                "post_snapshot_run_id": session.post_snapshot_run_id,
                "sources_requested": session.sources_requested,
                "sources_active": session.sources_active,
                "total_events": session.total_events,
                "events_by_source": session.events_by_source,
                "loss_indicators": session.loss_indicators,
                "coverage_gaps": [
                    {
                        "gap_id": g.gap_id,
                        "source_id": g.source_id,
                        "category": g.category,
                        "reason": g.reason,
                        "impact": g.impact,
                    }
                    for g in session.coverage_gaps
                ],
                "events": [
                    {
                        "event_id": e.event_id,
                        "source_id": e.source_id,
                        "category": e.category,
                        "timestamp_utc": e.timestamp_utc,
                        "source_timestamp": e.source_timestamp,
                        "channel_or_path": e.channel_or_path,
                        "event_code": e.event_code,
                        "process_id": e.process_id,
                        "process_name": e.process_name,
                        "process_provenance": e.process_provenance.value,
                        "payload": e.payload,
                    }
                    for e in session.events
                ],
            }
            print(json.dumps(out_obj, indent=2))
            return 0

        print("=" * 80)
        print(f"MONITORING SESSION: {session.session_id}")
        print("=" * 80)
        print(f"Status            : {session.status.value}")
        print(f"Stop Reason       : {session.stop_reason}")
        print(f"Started At        : {session.started_at}")
        print(f"Stopped At        : {session.stopped_at}")
        print(f"Duration (Elapsed): {session.elapsed_sec:.2f}s / {session.duration_sec}s requested")
        print(f"Total Events      : {session.total_events}")
        print(f"Coverage Gaps     : {len(session.coverage_gaps)}")
        if session.pre_snapshot_run_id:
            print(f"Pre-Snapshot ID   : {session.pre_snapshot_run_id}")
        if session.post_snapshot_run_id:
            print(f"Post-Snapshot ID  : {session.post_snapshot_run_id}")
        print("-" * 80)
        print("EVENTS BY SOURCE:")
        for src_name, cnt in sorted(session.events_by_source.items()):
            print(f"  {src_name:<18}: {cnt} items")
        if session.coverage_gaps:
            print("-" * 80)
            print("COVERAGE GAPS:")
            for g in session.coverage_gaps:
                print(f"  [{g.category}] {g.source_id}: {g.reason}")
        print("-" * 80)
        print(f"OBSERVED EVENTS TIMELINE ({len(session.events)} events):")
        for ev in session.events[:25]:
            proc_info = f"PID:{ev.process_id}" if ev.process_id else "N/A"
            if ev.process_name:
                proc_info += f" ({ev.process_name})"
            prov_str = f"[{ev.process_provenance.value}]"
            print(f"  [{ev.timestamp_utc[:19]}] {ev.source_id:<14} {ev.channel_or_path[:20]:<21} {ev.event_code:<12} {proc_info:<16} {prov_str}")
        if len(session.events) > 25:
            print(f"  ... and {len(session.events) - 25} more events. Use --format json to view full event array.")
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


def cmd_create_investigation(args: argparse.Namespace) -> int:
    """Create a new investigation record with symptom, target, and optional onset time."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        inv_id = f"inv-{uuid.uuid4().hex[:12]}"

        onset_utc_str = None
        if args.onset:
            dt_parsed = parse_iso_or_user_time(args.onset)
            if dt_parsed:
                onset_utc_str = dt_parsed.isoformat()

        inv = Investigation(
            id=inv_id,
            symptom=args.symptom,
            target_system=args.target or "",
            status=InvestigationStatus.NEW,
            onset_time_utc=onset_utc_str,
            onset_time_user=args.onset,
            onset_timezone=args.timezone or "UTC",
            user_notes=args.notes or "",
        )
        storage.save_investigation(inv)
        storage.log_audit(
            investigation_id=inv_id,
            event_type="INVESTIGATION_CREATED",
            message=f"Created investigation for symptom: {args.symptom}",
            details={"symptom": args.symptom, "target": args.target, "onset": args.onset},
        )
        print("=" * 80)
        print(f"INVESTIGATION CREATED: {inv_id}")
        print("=" * 80)
        print(f"Symptom      : {inv.symptom}")
        print(f"Target System: {inv.target_system or '(None specified)'}")
        print(f"Onset Time   : {inv.onset_time_user or '(Untimed / None)'} (UTC: {inv.onset_time_utc or 'N/A'})")
        print(f"Timezone     : {inv.onset_timezone}")
        print(f"Status       : {inv.status.value}")
        if inv.user_notes:
            print(f"Notes        : {inv.user_notes}")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"Error creating investigation: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_generate_candidates(args: argparse.Namespace) -> int:
    """Generate and rank candidate changes from snapshot diffs and monitoring events."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        inv = storage.get_investigation(args.investigation_id)
        if not inv:
            print(f"Error: Investigation '{args.investigation_id}' not found.", file=sys.stderr)
            return 1

        engine = CorrelationEngine(storage)

        # Determine onset time
        onset_input = args.onset or inv.onset_time_user
        tz_input = args.timezone or inv.onset_timezone or "UTC"
        pre_sec = args.pre_onset_sec if args.pre_onset_sec is not None else 3600
        post_sec = args.post_onset_sec if args.post_onset_sec is not None else 300

        window = engine.build_correlation_window(
            onset_time_str=onset_input,
            pre_onset_sec=pre_sec,
            post_onset_sec=post_sec,
            timezone_str=tz_input,
        )

        diffs: list[SnapshotDiff] = []
        if args.before_run and args.after_run:
            diffs = storage.get_snapshot_diffs(args.before_run, args.after_run)
            if not diffs:
                # Attempt to compare on the fly if stored diffs don't exist
                run_a = storage.get_collection_run(args.before_run)
                run_b = storage.get_collection_run(args.after_run)
                if run_a and run_b:
                    diffs = compare_snapshots(run_a, run_b)
                    storage.save_snapshot_diffs(diffs)

        events: list[MonitoringEvent] = []
        gaps: list[dict[str, Any]] = []
        if args.monitor_session:
            sess = storage.get_monitoring_session(args.monitor_session)
            if sess:
                events = sess.events
                gaps = [{"category": g.category, "source_id": g.source_id, "reason": g.reason} for g in sess.coverage_gaps]

        candidates = engine.generate_candidates(
            investigation_id=inv.id,
            target_system=inv.target_system,
            window=window,
            snapshot_diffs=diffs,
            monitoring_events=events,
            coverage_gaps=gaps,
        )

        storage.save_candidates(inv.id, candidates)
        storage.log_audit(
            investigation_id=inv.id,
            event_type="CANDIDATES_GENERATED",
            message=f"Generated {len(candidates)} candidate change(s) with ranking version {engine.ranker.version}",
            details={"candidate_count": len(candidates), "ranking_version": engine.ranker.version},
        )

        if args.format == "json":
            out_list = [
                {
                    "rank": c.rank,
                    "candidate_id": c.candidate_id,
                    "category": c.category,
                    "object_key": c.object_key,
                    "evidence_type": c.evidence_type.value,
                    "score": c.score,
                    "score_breakdown": c.score_breakdown.to_dict(),
                    "first_observed_utc": c.first_observed_utc,
                    "temporal_offset_sec": c.temporal_offset_sec,
                    "review_status": c.review_status.value,
                    "is_active": c.is_active,
                    "process_name": c.process_name,
                    "provenance": c.process_provenance.value,
                }
                for c in candidates
            ]
            print(json.dumps(out_list, indent=2))
            return 0

        print("=" * 80)
        print(f"CANDIDATE CHANGES GENERATED FOR: {inv.id}")
        print("=" * 80)
        print(f"Ranking Engine Version : {engine.ranker.version} (deterministic)")
        if window.is_untimed_fallback:
            print("Correlation Window     : UNTIMED FALLBACK (No onset time provided; ranking reflects evidence & target)")
        else:
            print(f"Correlation Window     : [{window.window_start_utc}] to [{window.window_end_utc}] (Onset: {window.onset_time_utc})")
        print(f"Total Candidates Found : {len(candidates)}")
        print("-" * 80)
        if not candidates:
            print("No candidates identified from provided snapshot diffs or monitoring events.")
        else:
            print(f"{'Rank':<5} {'Score':<7} {'Candidate ID':<22} {'Evidence':<16} {'Category':<14} {'Object Key'}")
            print("-" * 80)
            for c in candidates:
                print(f"{c.rank:<5} {c.score:>5.1f}  {c.candidate_id:<22} {c.evidence_type.value:<16} {c.category:<14} {c.object_key[:25]}")
        print("=" * 80)
        print("Caution: Candidate scores indicate correlation and experiment priority only.")
        print("Candidate ranking does NOT prove causation. Use isolated experiments to verify.")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"Candidate Generation Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_list_candidates(args: argparse.Namespace) -> int:
    """List candidate changes for an investigation."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        candidates = storage.get_candidates(args.investigation_id, active_only=args.active_only)
        if not candidates:
            print(f"No candidates found for investigation '{args.investigation_id}'.")
            return 0

        if args.format == "json":
            out_list = [
                {
                    "rank": c.rank,
                    "candidate_id": c.candidate_id,
                    "score": c.score,
                    "review_status": c.review_status.value,
                    "is_active": c.is_active,
                    "evidence_type": c.evidence_type.value,
                    "category": c.category,
                    "object_key": c.object_key,
                    "description": c.description,
                }
                for c in candidates
            ]
            print(json.dumps(out_list, indent=2))
            return 0

        print(f"Found {len(candidates)} candidate(s) for investigation '{args.investigation_id}':\n")
        print(f"{'Rank':<5} {'Score':<7} {'Candidate ID':<22} {'Status':<10} {'Evidence':<16} {'Category':<12} {'Key'}")
        print("-" * 85)
        for c in candidates:
            status_str = "ACTIVE" if c.is_active else "EXCLUDED"
            print(f"{c.rank:<5} {c.score:>5.1f}  {c.candidate_id:<22} {status_str:<10} {c.evidence_type.value:<16} {c.category:<12} {c.object_key[:25]}")
        return 0
    except Exception as err:
        print(f"Error listing candidates: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_inspect_candidate(args: argparse.Namespace) -> int:
    """Detailed inspection of a candidate change, score breakdown, and review audit history."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        cand = storage.get_candidate(args.id)
        if not cand:
            print(f"Error: Candidate '{args.id}' not found.", file=sys.stderr)
            return 1

        reviews = storage.get_candidate_reviews(args.id)

        if args.format == "json":
            out_obj = {
                "candidate_id": cand.candidate_id,
                "investigation_id": cand.investigation_id,
                "rank": cand.rank,
                "score": cand.score,
                "score_breakdown": cand.score_breakdown.to_dict(),
                "evidence_type": cand.evidence_type.value,
                "category": cand.category,
                "object_key": cand.object_key,
                "display_name": cand.display_name,
                "description": cand.description,
                "first_observed_utc": cand.first_observed_utc,
                "last_observed_utc": cand.last_observed_utc,
                "temporal_offset_sec": cand.temporal_offset_sec,
                "before_value": cand.before_value,
                "after_value": cand.after_value,
                "process_id": cand.process_id,
                "process_name": cand.process_name,
                "process_provenance": cand.process_provenance.value,
                "source_ids": cand.source_ids,
                "evidence_ids": cand.evidence_ids,
                "coverage_gaps": cand.coverage_gaps,
                "review_status": cand.review_status.value,
                "is_active": cand.is_active,
                "reversibility": cand.reversibility.value,
                "reviews": [
                    {
                        "review_id": r.review_id,
                        "decision": r.decision,
                        "previous_status": r.previous_status.value,
                        "new_status": r.new_status.value,
                        "reason": r.reason,
                        "reviewed_at": r.reviewed_at,
                    }
                    for r in reviews
                ],
            }
            print(json.dumps(out_obj, indent=2))
            return 0

        print("=" * 80)
        print(f"CANDIDATE CHANGE INSPECTION: {cand.candidate_id}")
        print("=" * 80)
        print(f"Investigation ID : {cand.investigation_id}")
        print(f"Rank / Priority  : #{cand.rank} (Score: {cand.score:.2f})")
        print(f"Review Status    : {cand.review_status.value} (Active in Experiments: {cand.is_active})")
        print(f"Evidence Type    : {cand.evidence_type.value}")
        print(f"Category / Key   : [{cand.category}] {cand.object_key}")
        print(f"Observed Time    : {cand.first_observed_utc} (Offset: {cand.temporal_offset_sec}s)")
        print(f"Sources          : {', '.join(cand.source_ids)}")
        print(f"Process Info     : {cand.process_name or 'N/A'} (PID: {cand.process_id or 'N/A'}, Provenance: {cand.process_provenance.value})")
        print("-" * 80)
        print("SCORE BREAKDOWN (DETERMINISTIC):")
        sb = cand.score_breakdown
        print(f"  Evidence Type Weight    : +{sb.evidence_type_score:.1f} pts")
        print(f"  Temporal Proximity      : +{sb.proximity_score:.1f} pts")
        print(f"  Target Relevance        : +{sb.target_relevance_score:.1f} pts")
        print(f"  Process Provenance      : +{sb.provenance_score:.1f} pts")
        print(f"  Corroboration Bonus     : +{sb.corroboration_score:.1f} pts")
        print(f"  Coverage Gap Penalty    : {sb.gap_penalty:.1f} pts")
        print(f"  Total Score             : {sb.total_score:.2f} pts")
        print(f"  Scoring Rationale       : {sb.rationale}")
        print("-" * 80)
        print(f"BEFORE VALUE: {cand.before_value}")
        print(f"AFTER VALUE : {cand.after_value}")
        if cand.uncertainty_note:
            print(f"UNCERTAINTY : {cand.uncertainty_note}")
        if cand.coverage_gaps:
            print("-" * 80)
            print("RELEVANT COVERAGE GAPS:")
            for g in cand.coverage_gaps:
                print(f"  [{g.get('category')}] {g.get('source_id')}: {g.get('reason')}")
        if reviews:
            print("-" * 80)
            print(f"USER REVIEW AUDIT TRAIL ({len(reviews)} review(s)):")
            for r in reviews:
                print(f"  [{r.reviewed_at}] {r.previous_status.value} -> {r.new_status.value}: {r.reason}")
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


def cmd_review_candidate(args: argparse.Namespace) -> int:
    """Explicitly include or exclude a candidate change with audited reason."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        cand = storage.get_candidate(args.candidate_id)
        if not cand:
            print(f"Error: Candidate '{args.candidate_id}' not found.", file=sys.stderr)
            return 1

        is_include = args.include or (not args.exclude)
        new_status = CandidateReviewStatus.INCLUDED if is_include else CandidateReviewStatus.EXCLUDED
        is_active = (new_status == CandidateReviewStatus.INCLUDED)

        if not args.reason or not args.reason.strip():
            print("Error: An explicit review --reason must be provided.", file=sys.stderr)
            return 1

        review_rec = CandidateReviewRecord(
            review_id=f"rev-{uuid.uuid4().hex[:12]}",
            candidate_id=cand.candidate_id,
            investigation_id=cand.investigation_id,
            decision="INCLUDE" if is_active else "EXCLUDE",
            previous_status=cand.review_status,
            new_status=new_status,
            reason=args.reason.strip(),
            reviewed_at=utc_now_iso(),
        )

        storage.update_candidate_review_status(cand.candidate_id, new_status, is_active)
        storage.record_candidate_review(review_rec)
        storage.log_audit(
            investigation_id=cand.investigation_id,
            event_type="CANDIDATE_REVIEWED",
            message=f"Candidate {cand.candidate_id} marked as {new_status.value}: {args.reason}",
            details={"candidate_id": cand.candidate_id, "decision": review_rec.decision, "reason": args.reason},
        )

        print("=" * 80)
        print(f"CANDIDATE REVIEW RECORDED: {cand.candidate_id}")
        print("=" * 80)
        print(f"Previous Status : {cand.review_status.value}")
        print(f"New Status      : {new_status.value} (Active: {is_active})")
        print(f"Audit Reason    : {args.reason}")
        print(f"Reviewed At     : {review_rec.reviewed_at}")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"Candidate Review Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


# ============================================================================
# Phase 7: Isolated Adapter and Trial Runner CLI Commands
# ============================================================================

def cmd_list_adapters(args: argparse.Namespace) -> int:
    """List available isolated environment adapters and their readiness."""
    registry = AdapterRegistry.get_default()
    adapters_info = []

    # Simulation Adapter
    sim = registry.get_adapter("simulation")
    adapters_info.append({
        "name": "simulation",
        "type": sim.get_adapter_type().value if sim else "SIMULATION",
        "available": sim.is_available() if sim else True,
        "is_disposable": True,
        "is_simulation": True,
        "is_default": True,
        "description": "Deterministic in-memory simulated workspace. Safe for all platforms and default.",
    })

    # Fake Adapter
    fake = registry.get_adapter("fake")
    adapters_info.append({
        "name": "fake",
        "type": fake.get_adapter_type().value if fake else "FAKE",
        "available": fake.is_available() if fake else True,
        "is_disposable": True,
        "is_simulation": True,
        "is_default": False,
        "description": "Configurable test-double adapter supporting fault injection and call history.",
    })

    # Hyper-V Adapter
    hyperv = registry.get_adapter("hyperv")
    hv_avail = hyperv.is_available() if hyperv else False
    adapters_info.append({
        "name": "hyperv",
        "type": hyperv.get_adapter_type().value if hyperv else "HYPER_V",
        "available": hv_avail,
        "is_disposable": True,
        "is_simulation": False,
        "is_default": False,
        "description": "Hyper-V VM adapter using standard checkpoints. Requires registered disposable VM.",
    })

    if getattr(args, "format", "text") == "json":
        print(json.dumps(adapters_info, indent=2))
        return 0

    print("=" * 80)
    print("STATEBISECT ISOLATED ENVIRONMENT ADAPTERS")
    print("=" * 80)
    print(f"{'Adapter':<12} {'Type':<12} {'Available':<10} {'Simulation':<12} {'Default':<8} {'Description'}")
    print("-" * 80)
    for info in adapters_info:
        avail_str = "YES" if info["available"] else "NO"
        sim_str = "SIMULATION" if info["is_simulation"] else "REAL-VM"
        def_str = "[DEFAULT]" if info["is_default"] else ""
        print(f"{info['name']:<12} {info['type']:<12} {avail_str:<10} {sim_str:<12} {def_str:<8} {info['description'][:30]}")
    print("=" * 80)
    print("Notice: SimulationAdapter is active by default. Zero host configuration changes.")
    return 0


def cmd_sandbox_evaluation(args: argparse.Namespace) -> int:
    """Display the technical evaluation of Windows Sandbox for isolated trials."""
    report = WindowsSandboxEvaluation.get_evaluation_report()
    if getattr(args, "format", "text") == "json":
        print(json.dumps(report, indent=2))
        return 0

    print("=" * 80)
    print("WINDOWS SANDBOX TECHNICAL EVALUATION FOR STATEBISECT")
    print("=" * 80)
    print(f"Technology Evaluated : {report.get('adapter_type', 'WINDOWS_SANDBOX')}")
    print(f"Evaluation Date      : {report.get('decision_date', '2026-11-17')}")
    print(f"Primary Source       : {report.get('primary_source', '')}")
    print(f"Verdict / Status     : {report.get('status', 'DEFERRED')}")
    print("-" * 80)
    print("REASONS FOR DEFERRAL:")
    for reason in report.get("reasons_deferred", []):
        print(f"  * {reason}")
    print("-" * 80)
    print(f"RECOMMENDATION:\n  {report.get('recommendation', '')}")
    print("=" * 80)
    return 0


def cmd_list_environments(args: argparse.Namespace) -> int:
    """List registered environments in StateBisect storage."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        adapter_type = None
        if getattr(args, "adapter", None):
            try:
                adapter_type = AdapterType(args.adapter.upper())
            except ValueError:
                print(f"Error: Unknown adapter '{args.adapter}'. Choose from: simulation, hyperv, fake.", file=sys.stderr)
                return 1

        envs = storage.list_environments(adapter_type=adapter_type)

        # If empty and no adapter filter or simulation filter, seed default simulation environment in storage
        if not envs and (adapter_type is None or adapter_type == AdapterType.SIMULATION):
            sim_env = EnvironmentRecord(
                environment_id="sim-env-default",
                adapter_type=AdapterType.SIMULATION,
                display_name="Default Simulation Workspace",
                is_disposable=True,
                is_host=False,
                status=EnvironmentStatus.REGISTERED,
                configuration={"description": "Default in-memory simulated environment."},
            )
            storage.register_environment(sim_env)
            sim_base = BaselineRecord(
                baseline_id="baseline-001",
                environment_id="sim-env-default",
                display_name="Default Clean Baseline",
                checksum_or_state="sim-initial-clean",
            )
            storage.register_baseline(sim_base)
            envs = [sim_env]

        if getattr(args, "format", "text") == "json":
            print(json.dumps([e.to_dict() for e in envs], indent=2))
            return 0

        print("=" * 80)
        print("REGISTERED EXPERIMENT ENVIRONMENTS")
        print("=" * 80)
        if not envs:
            print("No environments registered. Use 'register-environment' to add one.")
            return 0

        print(f"{'Environment ID':<18} {'Adapter':<12} {'Status':<12} {'Disposable':<10} {'Sim':<6} {'Name'}")
        print("-" * 80)
        for env in envs:
            disp_str = "YES" if env.is_disposable else "NO"
            sim_str = "YES" if env.is_simulation else "NO"
            print(f"{env.environment_id:<18} {env.adapter_type.value:<12} {env.status.value:<12} {disp_str:<10} {sim_str:<6} {env.display_name}")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"List Environments Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_inspect_environment(args: argparse.Namespace) -> int:
    """Inspect environment details, baselines, and recent trials."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        env = storage.get_environment(args.environment_id)
        if not env and args.environment_id == "sim-env-default":
            # auto-register default
            env = EnvironmentRecord(
                environment_id="sim-env-default",
                adapter_type=AdapterType.SIMULATION,
                display_name="Default Simulation Workspace",
                is_disposable=True,
                is_host=False,
                status=EnvironmentStatus.REGISTERED,
                configuration={"description": "Default in-memory simulated environment."},
            )
            storage.register_environment(env)
            storage.register_baseline(
                BaselineRecord(
                    baseline_id="baseline-001",
                    environment_id="sim-env-default",
                    display_name="Default Clean Baseline",
                    checksum_or_state="sim-initial-clean",
                )
            )

        if not env:
            print(f"Error: Environment '{args.environment_id}' not found in storage.", file=sys.stderr)
            return 1

        baselines = storage.list_baselines(args.environment_id)
        recent_trials = storage.get_isolated_trials(environment_id=args.environment_id, limit=5)

        if getattr(args, "format", "text") == "json":
            data = {
                "environment": env.to_dict(),
                "baselines": [b.to_dict() for b in baselines],
                "recent_trials": [t.to_dict() for t in recent_trials],
            }
            print(json.dumps(data, indent=2))
            return 0

        print("=" * 80)
        print(f"ENVIRONMENT INSPECTION: {env.environment_id}")
        print("=" * 80)
        print(f"Name           : {env.display_name}")
        print(f"Adapter Type   : {env.adapter_type.value}")
        print(f"Status         : {env.status.value}")
        print(f"Is Disposable  : {env.is_disposable} (Refuses host: {not env.is_host})")
        print(f"Is Simulation  : {env.is_simulation}")
        print(f"Description    : {env.configuration.get('description', '')}")
        print(f"Created At     : {env.created_at}")
        print("-" * 80)
        print("REGISTERED BASELINES:")
        if not baselines:
            print("  (None registered)")
        else:
            for b in baselines:
                print(f"  * [{b.baseline_id}] {b.display_name} (Checkpoint: '{b.checksum_or_state}')")
        print("-" * 80)
        print("RECENT ISOLATED TRIALS (up to 5):")
        if not recent_trials:
            print("  (No trials executed yet)")
        else:
            for t in recent_trials:
                print(f"  * [{t.trial_id}] {t.lifecycle_state.value} -> Outcome: {t.outcome.value} (Duration: {t.duration_ms:.1f}ms)")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"Inspect Environment Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_register_environment(args: argparse.Namespace) -> int:
    """Explicitly register a disposable experiment environment."""
    storage = None
    try:
        if args.is_host:
            print("Error: Refusing to register host machine as an experiment environment.", file=sys.stderr)
            return 1

        adapter_name = args.adapter.lower()
        if adapter_name not in ("simulation", "hyperv", "fake"):
            print(f"Error: Unknown adapter '{args.adapter}'. Must be one of: simulation, hyperv, fake.", file=sys.stderr)
            return 1

        if adapter_name == "hyperv" and not args.is_disposable:
            print("Error: Hyper-V environments must be explicitly disposable. Pass --is-disposable to confirm.", file=sys.stderr)
            return 1

        storage = get_storage(args.db, args.data_dir)
        env_type = AdapterType(adapter_name.upper())

        rec = EnvironmentRecord(
            environment_id=args.id,
            adapter_type=env_type,
            display_name=args.name,
            is_disposable=True,
            is_host=False,
            status=EnvironmentStatus.REGISTERED,
            configuration={"description": args.description or ""},
        )
        storage.register_environment(rec)

        registry = AdapterRegistry.get_default()
        adapter = registry.get_adapter(adapter_name)
        if adapter_name == "hyperv" and adapter is not None:
            res = adapter.register_disposable_vm(args.id, args.name)
            if not res.success:
                print(f"Warning: Adapter registration notice: {res.message}")

        print("=" * 80)
        print(f"ENVIRONMENT REGISTERED: {rec.environment_id}")
        print("=" * 80)
        print(f"Name         : {rec.display_name}")
        print(f"Adapter      : {rec.adapter_type.value}")
        print(f"Disposable   : {rec.is_disposable}")
        print(f"Simulation   : {rec.is_simulation}")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"Register Environment Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_register_baseline(args: argparse.Namespace) -> int:
    """Register a clean baseline checkpoint for an environment."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        env = storage.get_environment(args.environment)
        if not env:
            print(f"Error: Environment '{args.environment}' not registered. Register it first.", file=sys.stderr)
            return 1

        base = BaselineRecord(
            baseline_id=args.baseline_id,
            environment_id=args.environment,
            display_name=args.name,
            checksum_or_state=args.checkpoint_ref or "",
        )
        storage.register_baseline(base)

        print("=" * 80)
        print(f"BASELINE REGISTERED: {base.baseline_id}")
        print("=" * 80)
        print(f"Environment  : {base.environment_id}")
        print(f"Name         : {base.display_name}")
        print(f"Checkpoint   : {base.checksum_or_state}")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"Register Baseline Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_verify_baseline(args: argparse.Namespace) -> int:
    """Verify baseline checkpoint restore and health on an environment."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        registry = AdapterRegistry.get_default()
        adapter = registry.get_adapter(args.adapter)
        if adapter is None:
            print(f"Error: Unknown adapter '{args.adapter}'.", file=sys.stderr)
            return 1

        print(f"Restoring baseline '{args.baseline}' on environment '{args.environment}'...")
        r_restore = adapter.restore_baseline(args.environment, args.baseline)
        if not r_restore.success:
            print(f"FAILED: Baseline restore failed: {r_restore.error_reason or 'Restore failed'}", file=sys.stderr)
            storage.update_environment_status(args.environment, EnvironmentStatus.CORRUPTED)
            return 1

        print("Verifying baseline health check...")
        r_verify = adapter.verify_baseline(args.environment, args.baseline)
        if not r_verify.success:
            print(f"FAILED: Baseline health verification failed: {r_verify.error_reason or 'Verification failed'}", file=sys.stderr)
            storage.update_environment_status(args.environment, EnvironmentStatus.CORRUPTED)
            return 1

        storage.update_environment_status(args.environment, EnvironmentStatus.VERIFIED)
        print("=" * 80)
        print(f"BASELINE VERIFICATION PASSED: {args.baseline} on {args.environment}")
        print("=" * 80)
        print(f"Restore Duration : {r_restore.duration_ms:.1f}ms")
        print(f"Verify Duration  : {r_verify.duration_ms:.1f}ms")
        print(f"Status           : VERIFIED")
        print("=" * 80)
        return 0
    except Exception as err:
        print(f"Verify Baseline Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_run_trial(args: argparse.Namespace) -> int:
    """Execute a single isolated trial with baseline verification before and after."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        registry = AdapterRegistry.get_default()
        adapter = registry.get_adapter(args.adapter)
        if adapter is None:
            print(f"Error: Unknown adapter '{args.adapter}'.", file=sys.stderr)
            return 1

        # Ensure investigation exists for foreign key constraint
        inv = storage.get_investigation(args.investigation)
        if not inv:
            inv = Investigation(
                id=args.investigation,
                symptom="Isolated trial execution",
                target_system=args.environment,
                status=InvestigationStatus.NEW,
                baseline_id=args.baseline,
            )
            storage.save_investigation(inv)

        # Parse changes
        selected_changes = [c.strip() for c in args.changes.split(",") if c.strip()] if args.changes else []

        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id=args.environment,
            baseline_id=args.baseline,
            storage=storage,
            investigation_id=args.investigation,
        )

        print("=" * 80)
        print(f"STARTING ISOLATED TRIAL ON {args.environment} (Baseline: {args.baseline})")
        print("=" * 80)
        print(f"Adapter          : {adapter.get_adapter_type().value}")
        print(f"Selected Changes : {selected_changes if selected_changes else '(None - Baseline check)'}")
        print(f"Health Check     : {args.health_check}")
        print("-" * 80)

        trial_rec = runner.run_isolated_trial(
            selected_changes=selected_changes,
            health_check_id=args.health_check,
            notes=f"CLI trial executed via {args.adapter} adapter",
        )

        print(f"Trial ID         : {trial_rec.trial_id}")
        print(f"Lifecycle State  : {trial_rec.lifecycle_state.value}")
        print(f"Outcome          : {trial_rec.outcome.value}")
        print(f"Restore Status   : {trial_rec.restore_status}")
        print(f"Cleanup Status   : {trial_rec.cleanup_status}")
        print(f"Duration         : {trial_rec.duration_ms:.1f}ms")
        if trial_rec.output_bounded:
            print(f"Evidence Excerpt : {trial_rec.output_bounded[:200]}")
        if trial_rec.error_reason:
            print(f"Error Reason     : {trial_rec.error_reason}")
        print("=" * 80)

        if getattr(args, "format", "text") == "json":
            print(json.dumps(trial_rec.to_dict(), indent=2))

        return 0 if trial_rec.lifecycle_state == TrialLifecycleState.COMPLETED else 1
    except Exception as err:
        print(f"Run Trial Error: {err}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        if storage:
            storage.close()


def cmd_run_minimization(args: argparse.Namespace) -> int:
    """Run Delta Debugging minimization on candidates using an isolated environment adapter."""
    storage = None
    try:
        storage = get_storage(args.db, args.data_dir)
        inv = storage.get_investigation(args.investigation)
        if not inv:
            print(f"Error: Investigation '{args.investigation}' not found.", file=sys.stderr)
            return 1

        candidates = storage.get_candidates(args.investigation, active_only=True)
        if not candidates:
            # Fallback to all candidates if none specifically active
            candidates = storage.get_candidates(args.investigation, active_only=False)

        if not candidates:
            print(f"Error: No candidates found for investigation '{args.investigation}'. Run 'generate-candidates' first.", file=sys.stderr)
            return 1

        print("=" * 80)
        print(f"STATEBISECT ISOLATED DELTA DEBUGGING MINIMIZATION")
        print("=" * 80)
        print(f"Investigation    : {args.investigation}")
        print(f"Adapter          : {args.adapter}")
        print(f"Environment      : {args.environment}")
        print(f"Baseline         : {args.baseline}")
        print(f"Candidates Count : {len(candidates)}")
        print("-" * 80)

        # Convert candidates to Change objects for DeltaDebugger
        changes = [
            Change(
                id=c.candidate_id,
                category=c.category,
                description=c.description,
                payload={"category": c.category, "key": c.object_key, "value": c.after_value},
            )
            for c in candidates
        ]

        registry = AdapterRegistry.get_default()
        adapter = registry.get_adapter(args.adapter)
        if adapter is None:
            print(f"Error: Unknown adapter '{args.adapter}'.", file=sys.stderr)
            return 1

        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id=args.environment,
            baseline_id=args.baseline,
            storage=storage,
            investigation_id=args.investigation,
        )

        debugger = DeltaDebugger(
            runner=runner,
            all_changes=changes,
            health_check=lambda ws: HealthCheckResult(outcome=HealthCheckOutcome.PASS, output=""),
        )
        min_result = debugger.minimize(candidate_ids=[c.id for c in changes])

        print("=" * 80)
        print("MINIMIZATION COMPLETED")
        print("=" * 80)
        print(f"Result Verdict   : {min_result.verdict}")
        print(f"Is 1-Minimal     : {min_result.is_1_minimal}")
        print(f"Trials Executed  : {min_result.total_trials}")
        print(f"Minimal Failure-Inducing Set ({len(min_result.suspected_set)} changes):")
        for cid in min_result.suspected_set:
            print(f"  * {cid}")
        print(f"Rationale        : {min_result.rationale}")
        print("=" * 80)

        # Update investigation status
        storage.update_investigation_status(args.investigation, InvestigationStatus.VERIFIED)
        storage.log_audit(
            investigation_id=args.investigation,
            event_type="ISOLATED_MINIMIZATION_COMPLETED",
            message=f"Minimized {len(candidates)} candidates to {len(min_result.suspected_set)} change(s) in {min_result.total_trials} trials.",
            details={"minimal_set": min_result.suspected_set, "trials": min_result.total_trials, "verdict": min_result.verdict},
        )

        if getattr(args, "format", "text") == "json":
            out_data = {
                "investigation_id": min_result.investigation_id,
                "suspected_set": min_result.suspected_set,
                "is_1_minimal": min_result.is_1_minimal,
                "total_trials": min_result.total_trials,
                "verdict": min_result.verdict,
                "rationale": min_result.rationale,
            }
            print(json.dumps(out_data, indent=2))

        return 0
    except Exception as err:
        print(f"Run Minimization Error: {err}", file=sys.stderr)
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

    # monitor-preview
    p_mprev = subparsers.add_parser("monitor-preview", help="Preview monitoring sources, permissions, and duration limits.")

    # monitor-start
    p_mstart = subparsers.add_parser("monitor-start", help="Start foreground bounded change monitoring session.")
    p_mstart.add_argument("--duration", type=int, default=DEFAULT_DURATION_SEC, help=f"Duration in seconds (range: {MIN_DURATION_SEC}-{MAX_DURATION_SEC}, default: {DEFAULT_DURATION_SEC}).")
    p_mstart.add_argument("--sources", default=None, help="Comma-separated source IDs to monitor (default: all supported).")
    p_mstart.add_argument("--with-snapshots", action="store_true", help="Capture pre-session and post-session snapshots for diff correlation.")
    p_mstart.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_mstart.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # list-monitor-sessions
    p_mlist = subparsers.add_parser("list-monitor-sessions", help="List recorded monitoring sessions.")
    p_mlist.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_mlist.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # inspect-monitor-session
    p_mins = subparsers.add_parser("inspect-monitor-session", help="Inspect a monitoring session and its event timeline.")
    p_mins.add_argument("id", help="Monitoring session ID to inspect.")
    p_mins.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_mins.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_mins.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # Phase 6: create-investigation
    p_cinv = subparsers.add_parser("create-investigation", help="Create a new investigation record.")
    p_cinv.add_argument("--symptom", required=True, help="Description of observed failure or symptom.")
    p_cinv.add_argument("--target", default="", help="Target application, service, or system component.")
    p_cinv.add_argument("--onset", default=None, help="Estimated symptom onset timestamp (ISO format or YYYY-MM-DD HH:MM:SS).")
    p_cinv.add_argument("--timezone", default="UTC", help="Timezone of onset timestamp (default: UTC).")
    p_cinv.add_argument("--notes", default="", help="Optional context or operator notes.")
    p_cinv.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_cinv.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # Phase 6: generate-candidates
    p_gcand = subparsers.add_parser("generate-candidates", help="Correlate snapshot diffs and monitoring events to generate ranked candidates.")
    p_gcand.add_argument("investigation_id", help="Investigation ID to generate candidates for.")
    p_gcand.add_argument("--before-run", default=None, help="Baseline collection run ID.")
    p_gcand.add_argument("--after-run", default=None, help="Target collection run ID.")
    p_gcand.add_argument("--monitor-session", default=None, help="Monitoring session ID to correlate.")
    p_gcand.add_argument("--onset", default=None, help="Override symptom onset timestamp.")
    p_gcand.add_argument("--timezone", default=None, help="Override timezone for onset timestamp.")
    p_gcand.add_argument("--pre-onset-sec", type=int, default=3600, help="Pre-onset correlation window in seconds (default: 3600).")
    p_gcand.add_argument("--post-onset-sec", type=int, default=300, help="Post-onset correlation window in seconds (default: 300).")
    p_gcand.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_gcand.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_gcand.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # Phase 6: list-candidates
    p_lcand = subparsers.add_parser("list-candidates", help="List generated candidate changes for an investigation.")
    p_lcand.add_argument("investigation_id", help="Investigation ID.")
    p_lcand.add_argument("--active-only", action="store_true", help="Filter to active candidates only.")
    p_lcand.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_lcand.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_lcand.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # Phase 6: inspect-candidate
    p_icand = subparsers.add_parser("inspect-candidate", help="Detailed inspection of a candidate change, score breakdown, and review history.")
    p_icand.add_argument("id", help="Candidate ID to inspect.")
    p_icand.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_icand.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_icand.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # Phase 6: review-candidate
    p_rcand = subparsers.add_parser("review-candidate", help="Audit review to include or exclude a candidate change.")
    p_rcand.add_argument("candidate_id", help="Candidate ID to review.")
    rev_group = p_rcand.add_mutually_exclusive_group(required=True)
    rev_group.add_argument("--include", action="store_true", help="Include candidate in downstream experimentation.")
    rev_group.add_argument("--exclude", action="store_true", help="Exclude candidate from downstream experimentation.")
    p_rcand.add_argument("--reason", required=True, help="Required audit justification for inclusion or exclusion.")
    p_rcand.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_rcand.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # ========================================================================
    # Phase 7: Isolated Adapter and Trial Runner Subcommands
    # ========================================================================

    # list-adapters
    p_ladp = subparsers.add_parser("list-adapters", help="List registered environment adapters and availability.")
    p_ladp.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")

    # sandbox-evaluation
    p_wsb = subparsers.add_parser("sandbox-evaluation", help="Display technical evaluation and deferral rationale for Windows Sandbox.")
    p_wsb.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")

    # list-environments
    p_lenv = subparsers.add_parser("list-environments", help="List registered experiment environments.")
    p_lenv.add_argument("--adapter", choices=["simulation", "hyperv", "fake"], default=None, help="Filter by adapter type.")
    p_lenv.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_lenv.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_lenv.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # inspect-environment
    p_ienv = subparsers.add_parser("inspect-environment", help="Inspect an experiment environment and its baselines.")
    p_ienv.add_argument("environment_id", help="Environment ID to inspect.")
    p_ienv.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_ienv.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_ienv.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # register-environment
    p_renv = subparsers.add_parser("register-environment", help="Explicitly register a disposable experiment environment.")
    p_renv.add_argument("--id", required=True, help="Unique environment identifier (e.g. sim-env-1, vm-disposable-1).")
    p_renv.add_argument("--name", required=True, help="Friendly name of the environment.")
    p_renv.add_argument("--adapter", choices=["simulation", "hyperv", "fake"], default="simulation", help="Adapter type.")
    p_renv.add_argument("--description", default="", help="Description of the environment purpose.")
    p_renv.add_argument("--is-disposable", action="store_true", help="Explicit confirmation that this environment is disposable.")
    p_renv.add_argument("--is-host", action="store_true", help="Dangerous: indicates environment is host machine (rejected by safety invariant).")
    p_renv.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_renv.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # register-baseline
    p_rbase = subparsers.add_parser("register-baseline", help="Register a clean baseline checkpoint for an environment.")
    p_rbase.add_argument("--environment", required=True, help="Environment ID to attach baseline to.")
    p_rbase.add_argument("--baseline-id", required=True, help="Unique baseline identifier (e.g. baseline-001).")
    p_rbase.add_argument("--name", required=True, help="Friendly name of the baseline.")
    p_rbase.add_argument("--checkpoint-ref", default="", help="Checkpoint or snapshot reference string.")
    p_rbase.add_argument("--description", default="", help="Description of the baseline state.")
    p_rbase.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_rbase.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # verify-baseline
    p_vbase = subparsers.add_parser("verify-baseline", help="Verify baseline checkpoint restore and health.")
    p_vbase.add_argument("--environment", required=True, help="Environment ID to verify.")
    p_vbase.add_argument("--baseline", required=True, help="Baseline ID to restore and verify.")
    p_vbase.add_argument("--adapter", choices=["simulation", "hyperv", "fake"], default="simulation", help="Adapter type.")
    p_vbase.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_vbase.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # run-trial
    p_rtrial = subparsers.add_parser("run-trial", help="Execute an isolated trial with baseline verification before and after.")
    p_rtrial.add_argument("--environment", default="sim-env-default", help="Environment ID (default: sim-env-default).")
    p_rtrial.add_argument("--baseline", default="baseline-001", help="Baseline ID (default: baseline-001).")
    p_rtrial.add_argument("--adapter", choices=["simulation", "hyperv", "fake"], default="simulation", help="Adapter type.")
    p_rtrial.add_argument("--investigation", default="inv-adhoc", help="Investigation ID.")
    p_rtrial.add_argument("--changes", default="", help="Comma-separated candidate IDs or action keys to apply.")
    p_rtrial.add_argument("--health-check", default="default", help="Health check ID to execute.")
    p_rtrial.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_rtrial.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_rtrial.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

    # run-minimization
    p_rmin = subparsers.add_parser("run-minimization", help="Run Delta Debugging minimization on candidates using an isolated adapter.")
    p_rmin.add_argument("--investigation", required=True, help="Investigation ID containing candidates to minimize.")
    p_rmin.add_argument("--environment", default="sim-env-default", help="Environment ID (default: sim-env-default).")
    p_rmin.add_argument("--baseline", default="baseline-001", help="Baseline ID (default: baseline-001).")
    p_rmin.add_argument("--adapter", choices=["simulation", "hyperv", "fake"], default="simulation", help="Adapter type.")
    p_rmin.add_argument("--health-check", default="default", help="Health check ID to execute.")
    p_rmin.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    p_rmin.add_argument("--db", default=None, help="Path to SQLite database file.")
    p_rmin.add_argument("--data-dir", default=None, help="Path to directory for SQLite database storage.")

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
    elif args.command == "monitor-preview":
        return cmd_monitor_preview(args)
    elif args.command == "monitor-start":
        return cmd_monitor_start(args)
    elif args.command == "list-monitor-sessions":
        return cmd_list_monitor_sessions(args)
    elif args.command == "inspect-monitor-session":
        return cmd_inspect_monitor_session(args)
    elif args.command == "create-investigation":
        return cmd_create_investigation(args)
    elif args.command == "generate-candidates":
        return cmd_generate_candidates(args)
    elif args.command == "list-candidates":
        return cmd_list_candidates(args)
    elif args.command == "inspect-candidate":
        return cmd_inspect_candidate(args)
    elif args.command == "review-candidate":
        return cmd_review_candidate(args)
    elif args.command == "list-adapters":
        return cmd_list_adapters(args)
    elif args.command == "sandbox-evaluation":
        return cmd_sandbox_evaluation(args)
    elif args.command == "list-environments":
        return cmd_list_environments(args)
    elif args.command == "inspect-environment":
        return cmd_inspect_environment(args)
    elif args.command == "register-environment":
        return cmd_register_environment(args)
    elif args.command == "register-baseline":
        return cmd_register_baseline(args)
    elif args.command == "verify-baseline":
        return cmd_verify_baseline(args)
    elif args.command == "run-trial":
        return cmd_run_trial(args)
    elif args.command == "run-minimization":
        return cmd_run_minimization(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
