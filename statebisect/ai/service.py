"""Explanation service coordinating providers, context building, validation, consent, and audit persistence."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from statebisect.models import utc_now_iso
from statebisect.storage import Storage

from .context import EvidenceContextBuilder
from .models import (
    AiExplanationRecord,
    ContextPreview,
    DataClassification,
    EvidenceObservation,
    EvidenceReference,
    ExplanationResponse,
    ExplanationStatus,
    Hypothesis,
    ProviderStatus,
)
from .providers.base import ExplanationProvider
from .providers.mock_provider import MockExplanationProvider
from .providers.openai_provider import OpenAIExplanationProvider
from .validator import OutputValidator

logger = logging.getLogger("statebisect.ai.service")


class ExplanationService:
    """Core service orchestrating evidence-grounded AI explanations with strict privacy and consent controls."""

    def __init__(
        self,
        storage: Storage,
        default_provider_id: str = "mock",
        enabled: bool = True,
        persistence_enabled: bool = True,
    ) -> None:
        self.storage = storage
        self.context_builder = EvidenceContextBuilder(storage)
        self.validator = OutputValidator()
        self._enabled = enabled
        self._persistence_enabled = persistence_enabled

        # Provider registry
        self._providers: dict[str, ExplanationProvider] = {
            "mock": MockExplanationProvider(),
            "openai": OpenAIExplanationProvider(),
        }
        self._default_provider_id = default_provider_id if default_provider_id in self._providers else "mock"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def persistence_enabled(self) -> bool:
        return self._persistence_enabled

    def set_persistence_enabled(self, enabled: bool) -> None:
        self._persistence_enabled = enabled

    def register_provider(self, provider: ExplanationProvider) -> None:
        """Register or override an explanation provider."""
        self._providers[provider.provider_id] = provider

    def get_provider(self, provider_id: Optional[str] = None) -> ExplanationProvider:
        """Get provider by ID or default."""
        pid = provider_id or self._default_provider_id
        if pid not in self._providers:
            raise ValueError(f"Unknown explanation provider '{pid}'. Available: {list(self._providers.keys())}")
        return self._providers[pid]

    def get_status(self, provider_id: Optional[str] = None) -> dict[str, Any]:
        """Get operational status and capabilities for a provider."""
        if not self._enabled:
            return {
                "service_enabled": False,
                "status": ProviderStatus.DISABLED.value,
                "provider_id": provider_id or self._default_provider_id,
                "message": "AI explanation service is currently disabled by configuration.",
                "data_classification": DataClassification.LOCAL.value,
                "available_providers": list(self._providers.keys()),
                "persistence_enabled": self._persistence_enabled,
            }

        provider = self.get_provider(provider_id)
        status, msg = provider.check_health()
        return {
            "service_enabled": True,
            "status": status.value,
            "provider_id": provider.provider_id,
            "model_id": provider.model_identifier,
            "api_version": provider.api_version,
            "data_classification": provider.data_classification.value,
            "capabilities": provider.capabilities,
            "message": msg,
            "available_providers": list(self._providers.keys()),
            "persistence_enabled": self._persistence_enabled,
        }

    def get_context_preview(
        self,
        investigation_id: str,
        provider_id: Optional[str] = None,
    ) -> ContextPreview:
        """Generate exact preview of the evidence context that would be sent to a provider."""
        provider = self.get_provider(provider_id)
        _, preview = self.context_builder.build_context(
            investigation_id=investigation_id,
            provider_id=provider.provider_id,
            data_classification=provider.data_classification,
        )
        return preview

    def explain(
        self,
        investigation_id: str,
        question: str,
        provider_id: Optional[str] = None,
        confirm_remote: bool = False,
        timeout_sec: float = 30.0,
    ) -> ExplanationResponse:
        """Answer a user question grounded in investigation evidence.

        Args:
            investigation_id: Target investigation ID.
            question: Natural language question.
            provider_id: Optional provider identifier ('mock', 'openai').
            confirm_remote: Explicit user consent for remote transmission if using a remote provider.
            timeout_sec: Maximum execution time.

        Returns:
            Validated, sanitized ExplanationResponse.
        """
        start_time = time.monotonic()

        # If AI is disabled, immediately fall back to deterministic summary
        if not self._enabled:
            return self.generate_deterministic_fallback(
                investigation_id,
                question,
                reason="AI explanation service is disabled. Showing deterministic evidence summary.",
            )

        provider = self.get_provider(provider_id)

        # Build curated evidence context
        evidence_context, preview = self.context_builder.build_context(
            investigation_id=investigation_id,
            provider_id=provider.provider_id,
            data_classification=provider.data_classification,
        )

        valid_evidence_ids = set(preview.evidence_ids)

        # Check whether any verified verdict exists in context
        has_verified_set = any(
            vr.get("verdict") in ("VERIFIED_SET", "VERIFIED_INTERACTION")
            for vr in evidence_context.get("verification_runs", [])
        )

        # Enforce remote privacy consent check
        if provider.data_classification == DataClassification.REMOTE and not confirm_remote:
            resp = ExplanationResponse(
                investigation_id=investigation_id,
                question=question,
                short_answer=(
                    f"Remote data transmission unconfirmed. Provider '{provider.provider_id}' "
                    f"({provider.model_identifier}) processes data on a remote server. "
                    f"Review the evidence preview and check 'Confirm Remote Transmission' to proceed."
                ),
                status=ExplanationStatus.REMOTE_UNCONFIRMED,
                provider_id=provider.provider_id,
                model_id=provider.model_identifier,
                is_deterministic_fallback=False,
            )
            return resp

        # Check provider health
        p_status, p_msg = provider.check_health()
        if p_status != ProviderStatus.READY:
            return self.generate_deterministic_fallback(
                investigation_id,
                question,
                reason=f"Provider '{provider.provider_id}' is unavailable: {p_msg}. Showing deterministic summary.",
            )

        # Execute provider call with timing and error isolation
        try:
            raw_response = provider.generate_explanation(
                question=question,
                investigation_id=investigation_id,
                evidence_context=evidence_context,
                timeout_sec=timeout_sec,
            )
        except TimeoutError as e:
            logger.warning("Provider %s timed out: %s", provider.provider_id, e)
            return self.generate_deterministic_fallback(
                investigation_id,
                question,
                reason=f"Provider timed out after {timeout_sec}s. Showing deterministic summary.",
            )
        except Exception as e:
            logger.error("Provider %s failed: %s", provider.provider_id, e, exc_info=True)
            return self.generate_deterministic_fallback(
                investigation_id,
                question,
                reason=f"Provider failed with error ({type(e).__name__}). Showing deterministic summary.",
            )

        duration_ms = (time.monotonic() - start_time) * 1000.0

        # Validate and sanitize provider output
        validated_response = self.validator.validate_and_sanitize(
            response=raw_response,
            valid_evidence_ids=valid_evidence_ids,
            verified_verdict_in_context=has_verified_set,
        )

        # Audit logging / persistence
        if self._persistence_enabled:
            record = AiExplanationRecord(
                explanation_id=f"exp-{uuid.uuid4().hex[:12]}",
                investigation_id=investigation_id,
                provider_id=provider.provider_id,
                model_id=provider.model_identifier,
                api_version=provider.api_version or "",
                prompt_template_version="1.0.0",
                question=question,
                context_hash=preview.context_hash,
                evidence_ids=preview.evidence_ids,
                is_remote=(provider.data_classification == DataClassification.REMOTE),
                user_confirmed_remote=confirm_remote,
                status=validated_response.status,
                short_answer=validated_response.short_answer,
                structured_response=validated_response.to_dict(),
                duration_ms=duration_ms,
                created_at=utc_now_iso(),
            )
            try:
                self.storage.save_ai_explanation(record)
            except Exception as e:
                logger.warning("Failed to persist AI explanation record: %s", e)

        return validated_response

    def generate_deterministic_fallback(
        self,
        investigation_id: str,
        question: str,
        reason: str = "Deterministic fallback requested.",
    ) -> ExplanationResponse:
        """Generate a 100% deterministic, evidence-backed summary with zero external AI calls."""
        evidence_context, preview = self.context_builder.build_context(
            investigation_id=investigation_id,
            provider_id="deterministic-fallback",
            data_classification=DataClassification.LOCAL,
        )

        inv = evidence_context.get("investigation", {})
        top_candidates = evidence_context.get("top_candidates", [])
        verif_runs = evidence_context.get("verification_runs", [])
        gaps = evidence_context.get("coverage_gaps", [])

        observations: list[EvidenceObservation] = []
        references: list[EvidenceReference] = []

        inv_id = inv.get("id", investigation_id)
        inv_ev_id = f"ev-inv-{inv_id}"
        observations.append(
            EvidenceObservation(
                statement=f"Target: '{inv.get('target_system', 'Unknown')}', Symptom: '{inv.get('symptom', 'None')}'. Baseline: '{inv.get('baseline_id', 'None')}'.",
                evidence_ids=[inv_ev_id],
            )
        )
        references.append(
            EvidenceReference(
                evidence_id=inv_ev_id,
                evidence_type="investigation",
                summary=f"Investigation record: {inv_id}",
            )
        )

        has_verified = False
        for vr in verif_runs:
            v_id = vr.get("verification_id", "")
            ev_id = f"ev-verif-{v_id}"
            verdict = vr.get("verdict", "")
            if verdict in ("VERIFIED_SET", "VERIFIED_INTERACTION"):
                has_verified = True
            observations.append(
                EvidenceObservation(
                    statement=f"Verification run {v_id} concluded with verdict {verdict}. Tested candidate IDs: {vr.get('candidate_ids', [])}.",
                    evidence_ids=[ev_id],
                )
            )
            references.append(
                EvidenceReference(
                    evidence_id=ev_id,
                    evidence_type="verification",
                    summary=f"Verification audit: {v_id} ({verdict})",
                )
            )

        for c in top_candidates[:3]:
            c_id = c.get("candidate_id", "")
            ev_id = f"ev-cand-{c_id}"
            observations.append(
                EvidenceObservation(
                    statement=f"Candidate change '{c.get('display_name', c_id)}' ranked #{c.get('rank', 0)} (score: {c.get('score', 0.0)}).",
                    evidence_ids=[ev_id],
                )
            )
            references.append(
                EvidenceReference(
                    evidence_id=ev_id,
                    evidence_type="candidate",
                    summary=f"Candidate change: {c.get('display_name', c_id)}",
                )
            )

        hypotheses: list[Hypothesis] = []
        if has_verified:
            hypotheses.append(
                Hypothesis(
                    statement="Deterministic verification confirmed that the isolated candidate set reproduces the failure.",
                    evidence_for=[r.evidence_id for r in references if r.evidence_type == "verification"],
                    evidence_against=[],
                    confidence_label="EMPIRICALLY_VERIFIED_BY_ENGINE",
                )
            )
        elif top_candidates:
            c0 = top_candidates[0]
            hypotheses.append(
                Hypothesis(
                    statement=f"Candidate '{c0.get('display_name', '')}' is the closest chronological configuration change, but has not yet been verified.",
                    evidence_for=[f"ev-cand-{c0.get('candidate_id', '')}"],
                    evidence_against=[],
                    confidence_label="UNVERIFIED_HEURISTIC_HYPOTHESIS",
                )
            )

        unknowns = [
            f"Coverage gap: {g.get('component_or_channel')} - {g.get('reason')}"
            for g in gaps[:5]
        ] or ["No critical coverage gaps recorded."]

        safe_questions = [
            "What was the baseline verification outcome before candidate application?",
            "Were all elements of the candidate set proven strictly necessary?",
            "Did candidate reversal restore the application to a healthy state?",
        ]

        short_answer = (
            f"[Deterministic Summary - {reason}] StateBisect recorded {len(top_candidates)} candidate changes "
            f"and {len(verif_runs)} verification run(s) for investigation '{investigation_id}'."
        )

        return ExplanationResponse(
            investigation_id=investigation_id,
            question=question,
            short_answer=short_answer,
            observations=observations,
            hypotheses=hypotheses,
            unknowns=unknowns,
            suggested_safe_questions=safe_questions,
            evidence_references=references,
            is_deterministic_fallback=True,
            provider_id="deterministic-fallback",
            model_id="engine-summary-v1",
            status=ExplanationStatus.DETERMINISTIC_FALLBACK,
        )

    def list_history(self, investigation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """List saved explanation records for an investigation."""
        records = self.storage.list_ai_explanations(investigation_id, limit=limit)
        return [r.to_dict() for r in records]

    def clear_history(self, investigation_id: str) -> int:
        """Delete saved explanations for an investigation."""
        return self.storage.delete_ai_explanations(investigation_id)
