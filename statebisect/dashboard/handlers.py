"""REST API handlers and router for StateBisect Local Dashboard."""

from __future__ import annotations

import json
import re
import threading
import uuid
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from ..adapters.models import AdapterType
from ..ai.mcp_tools import ReadOnlyMcpServer
from ..ai.service import ExplanationService
from ..correlation.engine import CorrelationEngine
from ..correlation.models import CandidateReviewRecord, CandidateReviewStatus
from ..models import ENGINE_VERSION, Investigation, InvestigationStatus, MinimizationResult, utc_now_iso
from ..reporting import generate_json_report, generate_text_report
from ..storage import Storage
from ..verification.reporting import format_verification_json, format_verification_text
from .serializers import (
    sanitize_value,
    serialize_candidate,
    serialize_investigation,
    serialize_isolated_trial,
    serialize_verification_run,
)
from .timeline import TimelineBuilder

SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


def validate_id(val: str, field_name: str = "ID") -> str:
    """Validate that an identifier contains only safe alphanumeric/hyphen/underscore chars."""
    if not val or not SAFE_ID_PATTERN.match(val):
        raise ValueError(f"Invalid {field_name}: '{val}'. Must match ^[a-zA-Z0-9_\\-\\.]{{1,128}}$")
    return val


class ApiError(Exception):
    """Structured API error with HTTP status code."""

    def __init__(self, message: str, status_code: int = 400, code: str = "BAD_REQUEST") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.message, "code": self.code, "status": self.status_code}


class DashboardApiRouter:
    """Dispatches HTTP requests to typed handler methods backed by StateBisect storage."""

    def __init__(self, storage: Storage, ai_service: Optional[ExplanationService] = None) -> None:
        self.storage = storage
        self.timeline_builder = TimelineBuilder(storage)
        self.ai_service = ai_service or ExplanationService(storage)
        self.mcp_server = ReadOnlyMcpServer(storage)
        self._lock = threading.Lock()

    def dispatch(
        self,
        method: str,
        path: str,
        query_params: dict[str, list[str]],
        body: Optional[dict[str, Any]] = None,
    ) -> tuple[int, dict[str, Any] | str, dict[str, str]]:
        """Dispatch HTTP request to appropriate handler. Returns (status_code, response_data, headers)."""
        with self._lock:
            return self._dispatch_locked(method, path, query_params, body)

    def _dispatch_locked(
        self,
        method: str,
        path: str,
        query_params: dict[str, list[str]],
        body: Optional[dict[str, Any]] = None,
    ) -> tuple[int, dict[str, Any] | str, dict[str, str]]:
        method = method.upper()
        headers = {"Content-Type": "application/json; charset=utf-8"}

        try:
            # 1. Overview
            if method == "GET" and path == "/api/overview":
                data = self.get_overview()
                return 200, data, headers

            # 2. Environments & Adapters
            if method == "GET" and path == "/api/environments":
                data = self.get_environments()
                return 200, data, headers

            # 3. Investigations
            if method == "GET" and path == "/api/investigations":
                data = self.list_investigations(query_params)
                return 200, data, headers

            if method == "POST" and path == "/api/investigations":
                data = self.create_investigation(body or {})
                return 201, data, headers

            inv_match = re.match(r"^/api/investigations/([^/]+)$", path)
            if inv_match:
                inv_id = validate_id(inv_match.group(1), "investigation_id")
                if method == "GET":
                    data = self.get_investigation(inv_id)
                    return 200, data, headers

            # 4. Investigation Timeline
            timeline_match = re.match(r"^/api/investigations/([^/]+)/timeline$", path)
            if timeline_match:
                inv_id = validate_id(timeline_match.group(1), "investigation_id")
                if method == "GET":
                    data = self.get_timeline(inv_id, query_params)
                    return 200, data, headers

            # 5. Candidates & Ranking
            candidates_match = re.match(r"^/api/investigations/([^/]+)/candidates$", path)
            if candidates_match:
                inv_id = validate_id(candidates_match.group(1), "investigation_id")
                if method == "GET":
                    data = self.get_candidates(inv_id, query_params)
                    return 200, data, headers

            cand_detail_match = re.match(r"^/api/candidates/([^/]+)$", path)
            if cand_detail_match:
                cand_id = validate_id(cand_detail_match.group(1), "candidate_id")
                if method == "GET":
                    data = self.get_candidate_detail(cand_id)
                    return 200, data, headers

            cand_review_match = re.match(r"^/api/candidates/([^/]+)/review$", path)
            if cand_review_match:
                cand_id = validate_id(cand_review_match.group(1), "candidate_id")
                if method == "POST":
                    data = self.review_candidate(cand_id, body or {})
                    return 200, data, headers

            cand_gen_match = re.match(r"^/api/investigations/([^/]+)/generate-candidates$", path)
            if cand_gen_match:
                inv_id = validate_id(cand_gen_match.group(1), "investigation_id")
                if method == "POST":
                    data = self.generate_candidates(inv_id, body or {})
                    return 200, data, headers

            # 6. Isolated Trials
            trials_match = re.match(r"^/api/investigations/([^/]+)/trials$", path)
            if trials_match:
                inv_id = validate_id(trials_match.group(1), "investigation_id")
                if method == "GET":
                    data = self.get_isolated_trials(inv_id, query_params)
                    return 200, data, headers

            # 7. Verification Runs
            verifications_match = re.match(r"^/api/investigations/([^/]+)/verifications$", path)
            if verifications_match:
                inv_id = validate_id(verifications_match.group(1), "investigation_id")
                if method == "GET":
                    data = self.get_verifications(inv_id, query_params)
                    return 200, data, headers

            verif_detail_match = re.match(r"^/api/verification/([^/]+)$", path)
            if verif_detail_match:
                run_id = validate_id(verif_detail_match.group(1), "run_id")
                if method == "GET":
                    data = self.get_verification_detail(run_id)
                    return 200, data, headers

            # 8. Reports & Download
            report_match = re.match(r"^/api/investigations/([^/]+)/report$", path)
            if report_match:
                inv_id = validate_id(report_match.group(1), "investigation_id")
                if method == "GET":
                    data = self.get_report(inv_id, query_params)
                    return 200, data, headers

            download_match = re.match(r"^/api/investigations/([^/]+)/report/download$", path)
            if download_match:
                inv_id = validate_id(download_match.group(1), "investigation_id")
                if method == "GET":
                    content, filename, mime = self.download_report(inv_id, query_params)
                    headers["Content-Type"] = mime
                    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                    return 200, content, headers

            # 9. Collections & Monitoring Sessions
            if method == "GET" and path == "/api/collections":
                data = self.list_collections(query_params)
                return 200, data, headers

            if method == "GET" and path == "/api/monitoring-sessions":
                data = self.list_monitoring_sessions(query_params)
                return 200, data, headers

            # 10. Phase 10 AI Explanation Layer Endpoints
            if method == "GET" and path == "/api/ai/status":
                p_id = query_params.get("provider", [None])[0]
                data = self.ai_service.get_status(p_id)
                return 200, data, headers

            ai_preview_match = re.match(r"^/api/investigations/([^/]+)/ai/context-preview$", path)
            if ai_preview_match:
                inv_id = validate_id(ai_preview_match.group(1), "investigation_id")
                if method == "GET":
                    p_id = query_params.get("provider", [None])[0]
                    preview = self.ai_service.get_context_preview(inv_id, p_id)
                    return 200, preview.to_dict(), headers

            ai_explain_match = re.match(r"^/api/investigations/([^/]+)/ai/explain$", path)
            if ai_explain_match:
                inv_id = validate_id(ai_explain_match.group(1), "investigation_id")
                if method == "POST":
                    body_map = body or {}
                    question = (body_map.get("question") or "").strip()
                    if not question:
                        raise ApiError("Field 'question' is required.", status_code=400)
                    p_id = body_map.get("provider")
                    confirm_remote = bool(body_map.get("confirm_remote", False))
                    resp = self.ai_service.explain(
                        investigation_id=inv_id,
                        question=question,
                        provider_id=p_id,
                        confirm_remote=confirm_remote,
                    )
                    return 200, resp.to_dict(), headers

            ai_hist_match = re.match(r"^/api/investigations/([^/]+)/ai/history$", path)
            if ai_hist_match:
                inv_id = validate_id(ai_hist_match.group(1), "investigation_id")
                if method == "GET":
                    hist = self.ai_service.list_history(inv_id)
                    return 200, {"investigation_id": inv_id, "total": len(hist), "history": hist}, headers
                if method == "DELETE":
                    del_count = self.ai_service.clear_history(inv_id)
                    return 200, {"investigation_id": inv_id, "deleted_count": del_count}, headers

            ai_fb_match = re.match(r"^/api/investigations/([^/]+)/ai/fallback-summary$", path)
            if ai_fb_match:
                inv_id = validate_id(ai_fb_match.group(1), "investigation_id")
                if method == "POST":
                    body_map = body or {}
                    question = (body_map.get("question") or "Summary of investigation evidence").strip()
                    resp = self.ai_service.generate_deterministic_fallback(
                        inv_id, question, reason="User requested deterministic evidence summary."
                    )
                    return 200, resp.to_dict(), headers

            # 11. Read-Only MCP Tool Endpoint
            if method == "POST" and path == "/api/mcp":
                mcp_resp = self.mcp_server.handle_json_rpc(body or {})
                return 200, mcp_resp, headers

            raise ApiError(f"Route not found: {method} {path}", status_code=404, code="NOT_FOUND")

        except ApiError as e:
            return e.status_code, e.to_dict(), headers
        except ValueError as e:
            return 400, {"error": str(e), "code": "VALIDATION_ERROR", "status": 400}, headers
        except Exception as e:
            return 500, {"error": f"Internal server error: {str(e)}", "code": "INTERNAL_ERROR", "status": 500}, headers

    # --- Endpoint Implementations ---

    def get_overview(self) -> dict[str, Any]:
        """Return system overview, status, recent runs, and transparency badges."""
        investigations = self.storage.list_investigations()
        collection_runs = self.storage.list_collection_runs()[:10]
        monitoring_sessions = self.storage.list_monitoring_sessions()[:10]
        environments = self.storage.list_environments()
        verification_runs = self.storage.list_verification_runs()[:10]

        # Count active coverage gaps across recent runs
        total_gaps = 0
        recent_gaps: list[dict[str, Any]] = []
        for cr in collection_runs:
            for g in getattr(cr, "coverage_gaps", []):
                total_gaps += 1
                if len(recent_gaps) < 10:
                    recent_gaps.append({
                        "source": "collector",
                        "category": g.category,
                        "reason": g.reason,
                        "details": g.details,
                    })
        for ms in monitoring_sessions:
            for g in getattr(ms, "coverage_gaps", []):
                total_gaps += 1
                if len(recent_gaps) < 10:
                    recent_gaps.append({
                        "source": "monitor",
                        "category": g.source_name,
                        "reason": g.reason,
                        "details": g.details,
                    })

        return {
            "version": ENGINE_VERSION,
            "status": "ONLINE",
            "health_notice": "Note: Absence of recent failure observations does NOT imply system health.",
            "counts": {
                "investigations": len(investigations),
                "collection_runs": len(collection_runs),
                "monitoring_sessions": len(monitoring_sessions),
                "environments": len(environments),
                "verification_runs": len(verification_runs),
                "active_coverage_gaps": total_gaps,
            },
            "environment_transparency": {
                "has_simulation": any(e.adapter_type == AdapterType.SIMULATION or getattr(e, "is_simulation", False) for e in environments),
                "has_disposable_vm": any(getattr(e, "is_disposable", False) and e.adapter_type != AdapterType.SIMULATION for e in environments),
                "notice": "Simulations evaluate in-memory state models. Real VM experiments execute exclusively on disposable non-host VMs.",
            },
            "recent_investigations": [serialize_investigation(inv) for inv in investigations[:5]],
            "recent_verifications": [serialize_verification_run(v) for v in verification_runs[:5]],
            "active_coverage_gaps": recent_gaps,
        }

    def get_environments(self) -> dict[str, Any]:
        """List registered environments and baselines with safety labels."""
        envs = self.storage.list_environments()
        results = []
        for e in envs:
            baselines = self.storage.list_baselines(e.environment_id)
            is_sim = e.adapter_type == AdapterType.SIMULATION or getattr(e, "is_simulation", False)
            results.append({
                "environment_id": e.environment_id,
                "name": getattr(e, "display_name", getattr(e, "name", e.environment_id)),
                "adapter_type": e.adapter_type.value if hasattr(e.adapter_type, "value") else str(e.adapter_type),
                "is_disposable": e.is_disposable,
                "is_simulation": is_sim,
                "is_host": e.is_host,
                "status": e.status.value if hasattr(e.status, "value") else str(e.status),
                "created_at": e.created_at,
                "baselines": [
                    {
                        "baseline_id": b.baseline_id,
                        "name": getattr(b, "display_name", getattr(b, "name", b.baseline_id)),
                        "snapshot_reference": getattr(b, "checksum_or_state", getattr(b, "snapshot_reference", "")),
                        "is_verified": b.is_verified,
                        "verified_at": getattr(b, "created_at", ""),
                    }
                    for b in baselines
                ],
            })
        return {"environments": results}

    def list_investigations(self, params: dict[str, list[str]]) -> dict[str, Any]:
        """List and filter investigations."""
        search = (params.get("search", [""])[0] or "").lower()
        status_filter = (params.get("status", [""])[0] or "").upper()
        target_filter = (params.get("target", [""])[0] or "").lower()
        limit = min(int(params.get("limit", [50])[0]), 200)
        offset = max(int(params.get("offset", [0])[0]), 0)

        invs = self.storage.list_investigations()
        filtered = []
        for inv in invs:
            if search and search not in inv.symptom.lower() and search not in inv.target_system.lower() and search not in inv.id.lower():
                continue
            if status_filter and status_filter != "ALL":
                inv_status = inv.status.value if hasattr(inv.status, "value") else str(inv.status)
                if inv_status.upper() != status_filter:
                    continue
            if target_filter and target_filter not in inv.target_system.lower():
                continue
            filtered.append(inv)

        paginated = filtered[offset : offset + limit]
        return {
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
            "investigations": [serialize_investigation(i) for i in paginated],
        }

    def create_investigation(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a new investigation."""
        symptom = (body.get("symptom") or "").strip()
        target_system = (body.get("target_system") or "").strip()
        baseline_id = (body.get("baseline_id") or "baseline-001").strip()
        onset_time = (body.get("onset_time") or "").strip() or None
        health_check_def = body.get("health_check_def") or {}

        if not symptom:
            raise ApiError("Field 'symptom' is required and cannot be empty.", status_code=400)
        if not target_system:
            raise ApiError("Field 'target_system' is required and cannot be empty.", status_code=400)

        import uuid
        inv_id = f"inv-{uuid.uuid4().hex[:12]}"
        inv = Investigation(
            id=inv_id,
            symptom=symptom,
            target_system=target_system,
            baseline_id=baseline_id,
            onset_time_utc=onset_time,
            status=InvestigationStatus.NEW,
        )
        self.storage.save_investigation(inv)
        return serialize_investigation(inv)

    def get_investigation(self, inv_id: str) -> dict[str, Any]:
        """Get investigation details."""
        inv = self.storage.get_investigation(inv_id)
        if not inv:
            raise ApiError(f"Investigation '{inv_id}' not found.", status_code=404, code="NOT_FOUND")
        return serialize_investigation(inv)

    def get_timeline(self, inv_id: str, params: dict[str, list[str]]) -> dict[str, Any]:
        """Get timeline events for an investigation."""
        source_filter = params.get("source", [None])[0]
        category_filter = params.get("category", [None])[0]
        outcome_filter = params.get("outcome", [None])[0]
        time_start = params.get("time_start", [None])[0]
        time_end = params.get("time_end", [None])[0]
        limit = min(int(params.get("limit", [50])[0]), 200)
        offset = max(int(params.get("offset", [0])[0]), 0)

        return self.timeline_builder.build_timeline(
            investigation_id=inv_id,
            source_filter=source_filter,
            category_filter=category_filter,
            outcome_filter=outcome_filter,
            time_start=time_start,
            time_end=time_end,
            limit=limit,
            offset=offset,
        )

    def get_candidates(self, inv_id: str, params: dict[str, list[str]]) -> dict[str, Any]:
        """List ranked candidates for an investigation."""
        active_only = params.get("active_only", ["false"])[0].lower() in ("true", "1", "yes")
        candidates = self.storage.get_candidates(inv_id, active_only=active_only)
        return {
            "investigation_id": inv_id,
            "total": len(candidates),
            "disclaimer": "Ranking reflects experimental priority for Delta Debugging, NOT proof or probability of fault.",
            "candidates": [serialize_candidate(c) for c in candidates],
        }

    def get_candidate_detail(self, cand_id: str) -> dict[str, Any]:
        """Get candidate details and review history."""
        cand = self.storage.get_candidate(cand_id)
        if not cand:
            raise ApiError(f"Candidate '{cand_id}' not found.", status_code=404, code="NOT_FOUND")
        reviews = self.storage.get_candidate_reviews(cand_id)
        data = serialize_candidate(cand)
        data["reviews"] = [
            {
                "review_id": r.review_id,
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                "reason": r.reason,
                "reviewed_by": r.reviewed_by,
                "reviewed_at": r.reviewed_at,
            }
            for r in reviews
        ]
        return data

    def review_candidate(self, cand_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Record an audited review action (include/exclude) on a candidate."""
        cand = self.storage.get_candidate(cand_id)
        if not cand:
            raise ApiError(f"Candidate '{cand_id}' not found.", status_code=404, code="NOT_FOUND")

        raw_action = (body.get("action") or body.get("status") or "").lower()
        if raw_action in ("exclude", "excluded"):
            action = "exclude"
        elif raw_action in ("include", "included"):
            action = "include"
        else:
            action = raw_action

        reason = (body.get("reason") or "").strip()
        reviewer = (body.get("reviewer") or body.get("reviewed_by") or "dashboard_user").strip()

        if action not in ("include", "exclude"):
            raise ApiError("Action must be either 'include' or 'exclude'.", status_code=400)
        if action == "exclude" and (not reason or len(reason) < 3):
            raise ApiError("Excluding a candidate requires an explicit audit reason of at least 3 characters.", status_code=400)

        new_status = CandidateReviewStatus.EXCLUDED if action == "exclude" else CandidateReviewStatus.INCLUDED
        is_active = (new_status == CandidateReviewStatus.INCLUDED)
        review_record = CandidateReviewRecord(
            review_id=f"rev-{uuid.uuid4().hex[:12]}",
            candidate_id=cand_id,
            investigation_id=cand.investigation_id if hasattr(cand, "investigation_id") else "",
            decision=action,
            previous_status=cand.review_status if hasattr(cand, "review_status") else CandidateReviewStatus.INCLUDED,
            new_status=new_status,
            reason=reason or f"Manually {action}d via dashboard",
            reviewed_at=utc_now_iso(),
        )
        self.storage.record_candidate_review(review_record)
        self.storage.update_candidate_review_status(cand_id, new_status, is_active)
        updated_cand = self.storage.get_candidate(cand_id)
        return serialize_candidate(updated_cand)

    def generate_candidates(self, inv_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Safely trigger candidate change generation for an investigation."""
        inv = self.storage.get_investigation(inv_id)
        if not inv:
            raise ApiError(f"Investigation '{inv_id}' not found.", status_code=404, code="NOT_FOUND")

        before_run_id = body.get("before_run_id")
        after_run_id = body.get("after_run_id")
        monitor_session_id = body.get("monitor_session_id")

        if before_run_id:
            validate_id(before_run_id, "before_run_id")
        if after_run_id:
            validate_id(after_run_id, "after_run_id")
        if monitor_session_id:
            validate_id(monitor_session_id, "monitor_session_id")

        engine = CorrelationEngine()
        candidates = engine.generate_and_store_candidates(
            storage=self.storage,
            investigation=inv,
            before_run_id=before_run_id,
            after_run_id=after_run_id,
            monitoring_session_id=monitor_session_id,
        )
        return {
            "investigation_id": inv_id,
            "count": len(candidates),
            "candidates": [serialize_candidate(c) for c in candidates],
        }

    def get_isolated_trials(self, inv_id: str, params: dict[str, list[str]]) -> dict[str, Any]:
        """List isolated trials for an investigation."""
        limit = min(int(params.get("limit", [100])[0]), 200)
        trials = self.storage.get_isolated_trials(investigation_id=inv_id, limit=limit)
        return {
            "investigation_id": inv_id,
            "total": len(trials),
            "trials": [serialize_isolated_trial(t) for t in trials],
        }

    def get_verifications(self, inv_id: str, params: dict[str, list[str]]) -> dict[str, Any]:
        """List verification runs for an investigation."""
        limit = min(int(params.get("limit", [20])[0]), 50)
        runs = self.storage.list_verification_runs(investigation_id=inv_id)[:limit]
        return {
            "investigation_id": inv_id,
            "total": len(runs),
            "verification_runs": [serialize_verification_run(v) for v in runs],
        }

    def get_verification_detail(self, run_id: str) -> dict[str, Any]:
        """Get detail of a specific verification run."""
        v = self.storage.get_verification_run(run_id)
        if not v:
            raise ApiError(f"Verification run '{run_id}' not found.", status_code=404, code="NOT_FOUND")
        return serialize_verification_run(v)

    def get_report(self, inv_id: str, params: dict[str, list[str]]) -> dict[str, Any]:
        """Generate and format diagnostic report."""
        inv = self.storage.get_investigation(inv_id)
        if not inv:
            raise ApiError(f"Investigation '{inv_id}' not found.", status_code=404, code="NOT_FOUND")

        fmt = (params.get("format", ["text"])[0] or "text").lower()
        verif_runs = self.storage.list_verification_runs(investigation_id=inv_id)[:1]

        if verif_runs:
            latest_v = verif_runs[0]
            if fmt == "json":
                content = format_verification_json(latest_v)
            else:
                content = format_verification_text(latest_v)
        else:
            # Fallback to Phase 1/2 diagnostic report
            trials = self.storage.get_trials(inv_id)
            res = MinimizationResult(
                investigation_id=inv_id,
                suspected_set=[],
                is_1_minimal=False,
                total_trials=len(trials),
                verdict=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
                rationale="Report generated from recorded trial audit trail.",
                trials=trials,
            )
            if fmt == "json":
                content = json.dumps(generate_json_report(inv, res), indent=2)
            else:
                content = generate_text_report(inv, res)

        return {
            "investigation_id": inv_id,
            "format": fmt,
            "content": content,
        }

    def download_report(self, inv_id: str, params: dict[str, list[str]]) -> tuple[str, str, str]:
        """Return raw content, filename, and mime type for report download."""
        rep_data = self.get_report(inv_id, params)
        fmt = rep_data["format"]
        content = rep_data["content"]
        if fmt == "json":
            return content, f"statebisect_report_{inv_id}.json", "application/json; charset=utf-8"
        return content, f"statebisect_report_{inv_id}.txt", "text/plain; charset=utf-8"

    def list_collections(self, params: dict[str, list[str]]) -> dict[str, Any]:
        """List recent collection runs."""
        limit = min(int(params.get("limit", [20])[0]), 50)
        runs = self.storage.list_collection_runs()[:limit]
        return {
            "total": len(runs),
            "collection_runs": [
                {
                    "run_id": r.run_id,
                    "machine_id": r.machine_id,
                    "start_time": r.start_time,
                    "finish_time": r.finish_time,
                    "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                    "coverage_gaps_count": len(getattr(r, "coverage_gaps", [])),
                }
                for r in runs
            ],
        }

    def list_monitoring_sessions(self, params: dict[str, list[str]]) -> dict[str, Any]:
        """List recent monitoring sessions."""
        limit = min(int(params.get("limit", [20])[0]), 50)
        sessions = self.storage.list_monitoring_sessions()[:limit]
        return {
            "total": len(sessions),
            "monitoring_sessions": [
                {
                    "session_id": s.session_id,
                    "start_time": s.start_time,
                    "finish_time": s.finish_time,
                    "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                    "requested_duration_sec": s.requested_duration_sec,
                    "total_events_captured": s.total_events_captured,
                    "total_events_dropped": s.total_events_dropped,
                }
                for s in sessions
            ],
        }
