"""Allowlisted registry change observation source."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from statebisect.health_checks.base import bound_evidence, redact_sensitive_text

from ..base import BaseMonitorSource
from ..models import (
    EventConfidence,
    MonitoringCoverageGap,
    MonitoringEvent,
    ProcessProvenance,
)

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore

# Strict allowlist of safe registry paths that may be observed
ALLOWLISTED_REGISTRY_PREFIXES = (
    r"HKCU\Software\StateBisect",
    r"HKCU\Software\StateBisectTest",
    r"HKCU\Environment",
    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
)

FORBIDDEN_REGISTRY_PATTERNS = (
    r"SAM",
    r"SECURITY",
    r"LSA",
    r"PASSWORDS",
    r"CREDENTIALS",
    r"SECRETS",
)


class RegistryMonitorSource(BaseMonitorSource):
    """Monitors explicitly allowlisted registry paths for configuration changes.

    Safety Guarantees:
        1. Strictly restricted to allowlisted, safe user configuration keys.
        2. Strictly read-only (`winreg.KEY_READ`). Zero write, create, or delete operations.
        3. Never watches the entire hive.
        4. Does not guess or infer modifying process IDs from timing (marks as UNAVAILABLE).
    """

    def __init__(self, watch_paths: list[str] | None = None) -> None:
        super().__init__(
            source_id="registry_watcher",
            category="registry",
            description="Observes explicit allowlisted registry paths for value modifications",
            version="1.0.0",
            permission_requirement="Standard User (Read-Only)",
        )
        self.watch_paths = watch_paths if watch_paths is not None else [
            r"HKCU\Software\StateBisect",
            r"HKCU\Environment",
        ]
        self.validated_paths: list[tuple[Any, str, str]] = []
        self.previous_states: dict[str, dict[str, Any]] = {}
        self.coverage_gaps: list[MonitoringCoverageGap] = []

    def is_supported(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Requires Microsoft Windows host environment"
        return True, "Available via standard user winreg read access"

    def _validate_path(self, path_str: str) -> tuple[bool, str]:
        upper = path_str.upper()
        for forbidden in FORBIDDEN_REGISTRY_PATTERNS:
            if forbidden in upper:
                return False, f"Path '{path_str}' contains forbidden security token '{forbidden}'."

        allowed = any(path_str.startswith(prefix) for prefix in ALLOWLISTED_REGISTRY_PREFIXES)
        if not allowed:
            return False, f"Path '{path_str}' is not in the strict StateBisect registry allowlist."
        return True, "Path allowed"

    def start(self, session_id: str, start_time_utc: str) -> list[MonitoringCoverageGap]:
        self.session_id = session_id
        self.start_time_utc = start_time_utc
        self.validated_paths.clear()
        self.previous_states.clear()
        self.coverage_gaps.clear()

        if sys.platform != "win32":
            return [
                MonitoringCoverageGap(
                    gap_id=f"gap-{self.source_id}-nonwin",
                    session_id=session_id,
                    source_id=self.source_id,
                    category=self.category,
                    reason="Non-Windows platform; registry observation is unavailable.",
                    impact="Registry changes cannot be observed.",
                )
            ]

        for p in self.watch_paths:
            ok, reason = self._validate_path(p)
            if not ok:
                self.coverage_gaps.append(
                    MonitoringCoverageGap(
                        gap_id=f"gap-{self.source_id}-{uuid.uuid4().hex[:6]}",
                        session_id=session_id,
                        source_id=self.source_id,
                        category=self.category,
                        reason=reason,
                        impact=f"Path '{p}' will not be monitored.",
                    )
                )
                continue

            # Parse hive and subkey
            if p.startswith(r"HKCU\\") or p.startswith("HKCU\\"):
                hive = winreg.HKEY_CURRENT_USER
                subkey = p.split("\\", 1)[1]
            elif p.startswith(r"HKLM\\") or p.startswith("HKLM\\"):
                hive = winreg.HKEY_LOCAL_MACHINE
                subkey = p.split("\\", 1)[1]
            else:
                continue

            self.validated_paths.append((hive, subkey, p))
            # Record initial baseline state
            current_vals = self._read_key_values(hive, subkey, p)
            self.previous_states[p] = current_vals

        return list(self.coverage_gaps)

    def _read_key_values(self, hive: Any, subkey: str, path_str: str) -> dict[str, Any]:
        """Safely read values in an allowlisted key with KEY_READ."""
        values: dict[str, Any] = {}
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                num_subkeys, num_values, _ = winreg.QueryInfoKey(key)
                for i in range(num_values):
                    val_name, val_data, val_type = winreg.EnumValue(key, i)
                    # Convert bytes to hex if necessary
                    if isinstance(val_data, bytes):
                        val_repr = val_data.hex()
                    else:
                        val_repr = str(val_data)
                    clean_val, _ = redact_sensitive_text(val_repr)
                    bounded_val, _ = bound_evidence(clean_val, max_length=256)
                    values[val_name] = {
                        "value": bounded_val,
                        "type": val_type,
                    }
        except FileNotFoundError:
            # Key does not currently exist; tracked as empty
            values["<status>"] = {"value": "KEY_NOT_FOUND", "type": 0}
        except PermissionError as err:
            self.coverage_gaps.append(
                MonitoringCoverageGap(
                    gap_id=f"gap-{self.source_id}-perm-{uuid.uuid4().hex[:6]}",
                    session_id=self.session_id,
                    source_id=self.source_id,
                    category=self.category,
                    reason=f"Access denied reading allowlisted path '{path_str}': {err}",
                    impact=f"Cannot observe changes to '{path_str}'.",
                )
            )
        except Exception:
            pass

        return values

    def poll(self) -> list[MonitoringEvent]:
        if sys.platform != "win32":
            return []

        events: list[MonitoringEvent] = []
        now_utc = datetime.now(timezone.utc).isoformat()

        for hive, subkey, path_str in self.validated_paths:
            current_vals = self._read_key_values(hive, subkey, path_str)
            prev_vals = self.previous_states.get(path_str, {})

            all_val_names = sorted(set(current_vals.keys()) | set(prev_vals.keys()))
            for val_name in all_val_names:
                prev_item = prev_vals.get(val_name)
                curr_item = current_vals.get(val_name)

                if prev_item is None and curr_item is not None:
                    # Value added
                    events.append(
                        self._create_event(
                            path_str=path_str,
                            event_code="VALUE_ADDED",
                            val_name=val_name,
                            old_val=None,
                            new_val=curr_item["value"],
                            timestamp=now_utc,
                        )
                    )
                elif prev_item is not None and curr_item is None:
                    # Value deleted
                    events.append(
                        self._create_event(
                            path_str=path_str,
                            event_code="VALUE_DELETED",
                            val_name=val_name,
                            old_val=prev_item["value"],
                            new_val=None,
                            timestamp=now_utc,
                        )
                    )
                elif prev_item is not None and curr_item is not None:
                    if prev_item["value"] != curr_item["value"] or prev_item["type"] != curr_item["type"]:
                        # Value modified
                        events.append(
                            self._create_event(
                                path_str=path_str,
                                event_code="VALUE_MODIFIED",
                                val_name=val_name,
                                old_val=prev_item["value"],
                                new_val=curr_item["value"],
                                timestamp=now_utc,
                            )
                        )

            # Update state for next poll
            self.previous_states[path_str] = current_vals

        return events

    def _create_event(
        self,
        path_str: str,
        event_code: str,
        val_name: str,
        old_val: str | None,
        new_val: str | None,
        timestamp: str,
    ) -> MonitoringEvent:
        payload = {
            "path": path_str,
            "value_name": val_name,
            "old_value": old_val,
            "new_value": new_val,
        }
        raw_ref = f"reg-{hashlib.sha256(f'{path_str}:{val_name}:{event_code}:{timestamp}'.encode()).hexdigest()[:12]}"
        return MonitoringEvent(
            event_id=f"evt-{uuid.uuid4().hex[:10]}",
            session_id=self.session_id,
            source_id=self.source_id,
            source_version=self.version,
            category=self.category,
            timestamp_utc=timestamp,
            source_timestamp=timestamp,
            channel_or_path=path_str,
            event_code=event_code,
            process_id=None,
            process_name=None,
            process_provenance=ProcessProvenance.UNAVAILABLE,
            payload=payload,
            is_redacted=False,
            is_truncated=False,
            confidence=EventConfidence.CONFIRMED,
            raw_ref=raw_ref,
        )

    def stop(self) -> list[MonitoringCoverageGap]:
        return list(self.coverage_gaps)

    def get_loss_indicators(self) -> dict[str, Any]:
        return {
            "events_dropped": 0,
            "buffer_overflows": 0,
            "truncations": 0,
        }
