"""Command-line interface (CLI) for StateBisect."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import traceback
from pathlib import Path
from typing import Any

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
            print(f"  {t.trial_id:<14} {t.outcome.value:<12} {t.duration_ms:>6.2f}ms  [{c_str}]")
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
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
