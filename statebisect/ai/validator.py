"""Strict output validation, evidence cross-checking, and prompt injection sanitization."""

from __future__ import annotations

import html
import re
from typing import Any

from .models import (
    EvidenceObservation,
    EvidenceReference,
    ExplanationResponse,
    ExplanationStatus,
    Hypothesis,
)


class OutputValidator:
    """Validates structured AI responses against schema, context evidence IDs, and security invariants."""

    SAFE_TEXT_MAX_LEN = 10_000

    def __init__(self) -> None:
        pass

    def validate_and_sanitize(
        self,
        response: ExplanationResponse,
        valid_evidence_ids: set[str],
        verified_verdict_in_context: bool = False,
    ) -> ExplanationResponse:
        """Validate response fields, verify citations, prevent hypothesis escalation, and sanitize strings.

        Args:
            response: Raw ExplanationResponse from provider.
            valid_evidence_ids: Exact set of stable evidence IDs present in supplied context.
            verified_verdict_in_context: Whether a VERIFIED_SET verification run exists in context.

        Returns:
            Sanitized, validated ExplanationResponse with validation_errors populated if any issues found.
        """
        validation_errors: list[str] = list(response.validation_errors)

        # 1. Sanitize text fields against HTML/script injection
        short_answer_sanitized = self._sanitize_text(response.short_answer)

        # 2. Validate Evidence References
        validated_refs: list[EvidenceReference] = []
        for ref in response.evidence_references:
            ev_id = ref.evidence_id.strip()
            summary_clean = self._sanitize_text(ref.summary)
            if ev_id not in valid_evidence_ids:
                validation_errors.append(
                    f"Invalid evidence citation: '{ev_id}' does not exist in supplied context."
                )
                validated_refs.append(
                    EvidenceReference(
                        evidence_id=ev_id,
                        evidence_type=ref.evidence_type,
                        summary=summary_clean,
                        is_valid=False,
                        validation_note="REJECTED: Citation not found in supplied investigation evidence.",
                    )
                )
            else:
                validated_refs.append(
                    EvidenceReference(
                        evidence_id=ev_id,
                        evidence_type=ref.evidence_type,
                        summary=summary_clean,
                        is_valid=True,
                    )
                )

        # 3. Validate Observations
        validated_obs: list[EvidenceObservation] = []
        for obs in response.observations:
            stmt_clean = self._sanitize_text(obs.statement)
            valid_ids_in_obs: list[str] = []
            for ev_id in obs.evidence_ids:
                ev_id_clean = ev_id.strip()
                if ev_id_clean in valid_evidence_ids:
                    valid_ids_in_obs.append(ev_id_clean)
                else:
                    validation_errors.append(
                        f"Observation cites ungrounded evidence ID: '{ev_id_clean}'"
                    )

            validated_obs.append(
                EvidenceObservation(
                    statement=stmt_clean,
                    evidence_ids=valid_ids_in_obs,
                )
            )

        # 4. Validate Hypotheses and Prevent Escalation to Verified Fact
        validated_hyp: list[Hypothesis] = []
        for hyp in response.hypotheses:
            stmt_clean = self._sanitize_text(hyp.statement)
            ev_for_valid = [e.strip() for e in hyp.evidence_for if e.strip() in valid_evidence_ids]
            ev_against_valid = [e.strip() for e in hyp.evidence_against if e.strip() in valid_evidence_ids]

            # Invariant: Never allow an AI hypothesis to claim verified causality unless verified by the engine
            conf_label = hyp.confidence_label
            if not verified_verdict_in_context and "VERIFIED" in conf_label.upper():
                conf_label = "UNVERIFIED_HEURISTIC_HYPOTHESIS"
                validation_errors.append(
                    "Hypothesis confidence coerced: Cannot claim VERIFIED status without an engine VERIFIED_SET verdict."
                )

            validated_hyp.append(
                Hypothesis(
                    statement=stmt_clean,
                    evidence_for=ev_for_valid,
                    evidence_against=ev_against_valid,
                    confidence_label=conf_label,
                )
            )

        # 5. Sanitize unknowns and safe questions
        clean_unknowns = [self._sanitize_text(u) for u in response.unknowns]
        clean_questions = [self._sanitize_text(q) for q in response.suggested_safe_questions]

        final_status = response.status
        if validation_errors and response.status == ExplanationStatus.SUCCESS:
            final_status = ExplanationStatus.VALIDATION_FAILED

        return ExplanationResponse(
            investigation_id=response.investigation_id,
            question=self._sanitize_text(response.question),
            short_answer=short_answer_sanitized,
            observations=validated_obs,
            hypotheses=validated_hyp,
            unknowns=clean_unknowns,
            suggested_safe_questions=clean_questions,
            evidence_references=validated_refs,
            disclaimer=response.disclaimer,
            is_deterministic_fallback=response.is_deterministic_fallback,
            validation_errors=validation_errors,
            provider_id=response.provider_id,
            model_id=response.model_id,
            status=final_status,
        )

    def _sanitize_text(self, text: str) -> str:
        """Strip dangerous HTML/script tags and limit length."""
        if not text:
            return ""
        # Remove script and style tags completely
        cleaned = re.sub(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<\s*style[^>]*>.*?<\s*/\s*style\s*>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        # HTML escape remaining characters to prevent browser DOM execution
        escaped = html.escape(cleaned)
        return escaped[: self.SAFE_TEXT_MAX_LEN]
