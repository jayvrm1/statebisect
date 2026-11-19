"""Deterministic mock explanation provider for tests, demos, and air-gapped environments."""

from __future__ import annotations

import time
from typing import Any, Optional

from ..models import (
    DataClassification,
    EvidenceObservation,
    EvidenceReference,
    ExplanationResponse,
    ExplanationStatus,
    Hypothesis,
    ProviderStatus,
)
from .base import ExplanationProvider


class MockExplanationProvider(ExplanationProvider):
    """Deterministic mock provider delivering grounded explanations from supplied evidence."""

    def __init__(
        self,
        provider_id: str = "mock",
        model_id: str = "mock-grounded-v1",
        version: str = "1.0.0",
        force_status: Optional[ProviderStatus] = None,
        status_message: str = "Mock provider ready (local deterministic engine)",
        # Adversarial / Test configuration flags:
        force_invalid_citations: bool = False,
        force_malformed_json: bool = False,
        force_timeout: bool = False,
        force_refusal: bool = False,
        force_error: Optional[str] = None,
    ) -> None:
        self._provider_id = provider_id
        self._model_id = model_id
        self._version = version
        self._force_status = force_status or ProviderStatus.READY
        self._status_message = status_message

        # Test flags
        self.force_invalid_citations = force_invalid_citations
        self.force_malformed_json = force_malformed_json
        self.force_timeout = force_timeout
        self.force_refusal = force_refusal
        self.force_error = force_error
        self.call_count = 0
        self.last_question = ""
        self.last_evidence_context: Optional[dict[str, Any]] = None

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def version(self) -> str:
        return self._version

    @property
    def model_identifier(self) -> str:
        return self._model_id

    @property
    def api_version(self) -> Optional[str]:
        return "2026-mock"

    @property
    def data_classification(self) -> DataClassification:
        return DataClassification.LOCAL

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "structured_outputs": True,
            "streaming": False,
            "local_execution": True,
            "offline_capable": True,
        }

    def check_health(self) -> tuple[ProviderStatus, str]:
        return self._force_status, self._status_message

    def generate_explanation(
        self,
        question: str,
        investigation_id: str,
        evidence_context: dict[str, Any],
        timeout_sec: float = 30.0,
    ) -> ExplanationResponse:
        self.call_count += 1
        self.last_question = question
        self.last_evidence_context = evidence_context

        if self.force_timeout:
            raise TimeoutError(f"Mock provider timed out after {timeout_sec}s")

        if self.force_error:
            raise RuntimeError(f"Mock provider error: {self.force_error}")

        if self.force_refusal:
            return ExplanationResponse(
                investigation_id=investigation_id,
                question=question,
                short_answer="I cannot fulfill this request because it asks for unsupported actions outside investigation evidence.",
                status=ExplanationStatus.REFUSED,
                provider_id=self.provider_id,
                model_id=self.model_identifier,
            )

        # Extract available valid evidence items from context
        evidence_items = evidence_context.get("evidence_items", {})
        valid_ids = list(evidence_items.keys())

        # Synthesize deterministic grounded observations
        observations: list[EvidenceObservation] = []
        references: list[EvidenceReference] = []

        # Target system and symptom observation
        inv_data = evidence_context.get("investigation", {})
        inv_ev_id = f"ev-inv-{investigation_id}"
        if inv_ev_id in valid_ids or not valid_ids:
            observations.append(
                EvidenceObservation(
                    statement=f"Investigation {investigation_id} targets '{inv_data.get('target_system', 'Unknown System')}' "
                    f"with reported symptom: '{inv_data.get('symptom', 'No symptom recorded')}'.",
                    evidence_ids=[inv_ev_id] if inv_ev_id in valid_ids else [],
                )
            )
            if inv_ev_id in valid_ids:
                references.append(
                    EvidenceReference(
                        evidence_id=inv_ev_id,
                        evidence_type="investigation",
                        summary=f"Investigation definition for {investigation_id}",
                    )
                )

        # Candidate observations
        top_candidates = evidence_context.get("top_candidates", [])
        for cand in top_candidates[:3]:
            cand_id = cand.get("candidate_id", "")
            ev_id = f"ev-cand-{cand_id}"
            if ev_id in valid_ids:
                disp_name = cand.get("display_name", cand_id)
                rank = cand.get("rank", 0)
                score = cand.get("score", 0.0)
                observations.append(
                    EvidenceObservation(
                        statement=f"Candidate change '{disp_name}' (ID: {cand_id}) is ranked #{rank} with heuristic score {score:.1f}.",
                        evidence_ids=[ev_id],
                    )
                )
                references.append(
                    EvidenceReference(
                        evidence_id=ev_id,
                        evidence_type="candidate",
                        summary=f"Candidate change: {disp_name}",
                    )
                )

        # Verification run observations
        verif_runs = evidence_context.get("verification_runs", [])
        has_verified_set = False
        for vr in verif_runs[:2]:
            verif_id = vr.get("verification_id", "")
            ev_id = f"ev-verif-{verif_id}"
            if ev_id in valid_ids:
                verdict = vr.get("verdict", "")
                is_1min = vr.get("is_1_minimal", False)
                cands_tested = vr.get("candidate_ids", [])
                if verdict in ("VERIFIED_SET", "VERIFIED_INTERACTION"):
                    has_verified_set = True
                observations.append(
                    EvidenceObservation(
                        statement=f"Verification run {verif_id} reached verdict {verdict} over candidate set {cands_tested} (1-Minimal: {is_1min}).",
                        evidence_ids=[ev_id],
                    )
                )
                references.append(
                    EvidenceReference(
                        evidence_id=ev_id,
                        evidence_type="verification",
                        summary=f"Verification run {verif_id} (Verdict: {verdict})",
                    )
                )

        # Coverage gaps observations
        gaps = evidence_context.get("coverage_gaps", [])
        unknowns: list[str] = []
        for gap in gaps[:3]:
            comp = gap.get("component_or_channel", "Unknown source")
            reason = gap.get("reason", "Telemtry gap detected")
            unknowns.append(f"Coverage gap in {comp}: {reason}")

        # Formulate grounded hypotheses
        hypotheses: list[Hypothesis] = []
        if has_verified_set:
            hypotheses.append(
                Hypothesis(
                    statement="The verified candidate change set reproduced the failure under baseline controls.",
                    evidence_for=[r.evidence_id for r in references if r.evidence_type == "verification"],
                    evidence_against=[],
                    confidence_label="EMPIRICALLY_VERIFIED_IN_ENVIRONMENT",
                )
            )
        elif top_candidates:
            cand0 = top_candidates[0]
            c0_id = cand0.get("candidate_id", "")
            ev_c0 = f"ev-cand-{c0_id}"
            hypotheses.append(
                Hypothesis(
                    statement=f"Change '{cand0.get('display_name', c0_id)}' has the highest temporal proximity to symptom onset, but requires verification.",
                    evidence_for=[ev_c0] if ev_c0 in valid_ids else [],
                    evidence_against=[],
                    confidence_label="UNVERIFIED_HEURISTIC_HYPOTHESIS",
                )
            )
        else:
            hypotheses.append(
                Hypothesis(
                    statement="Insufficient telemetry or candidate changes available to formulate a specific hypothesis.",
                    evidence_for=[],
                    evidence_against=[],
                    confidence_label="DATA_DEFICIENT",
                )
            )

        # If test flag is set to inject invalid citations
        if self.force_invalid_citations:
            references.append(
                EvidenceReference(
                    evidence_id="ev-fabricated-nonexistent-999",
                    evidence_type="fabricated",
                    summary="This evidence ID does not exist in context",
                    is_valid=False,
                )
            )
            observations.append(
                EvidenceObservation(
                    statement="This observation cites a fabricated non-existent evidence ID.",
                    evidence_ids=["ev-fabricated-nonexistent-999"],
                )
            )

        # Build short answer
        if has_verified_set:
            short_answer = (
                f"Based on empirical verification run(s), StateBisect isolated and reproduced the symptom "
                f"using the verified candidate set. See observations and cited evidence below."
            )
        elif top_candidates:
            short_answer = (
                f"Based on observed configuration telemetry, candidate changes have been ranked by temporal proximity. "
                f"No verification run has yet confirmed root cause in an isolated environment."
            )
        else:
            short_answer = (
                f"Investigation {investigation_id} does not yet have enough configuration telemetry or verification "
                f"trials recorded to identify candidate causes."
            )

        safe_questions = [
            "Which candidate changes had the closest timestamp to the reported onset?",
            "Did the baseline control trial pass before testing candidate changes?",
            "What coverage gaps or telemetry limitations were recorded during data collection?",
            "Was the failure reproduced as a single change or a multi-change interaction?",
        ]

        return ExplanationResponse(
            investigation_id=investigation_id,
            question=question,
            short_answer=short_answer,
            observations=observations,
            hypotheses=hypotheses,
            unknowns=unknowns if unknowns else ["No critical telemetry gaps recorded."],
            suggested_safe_questions=safe_questions,
            evidence_references=references,
            provider_id=self.provider_id,
            model_id=self.model_identifier,
            status=ExplanationStatus.SUCCESS,
        )
