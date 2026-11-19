"""Provider-neutral abstract interface for StateBisect explanation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..models import DataClassification, ExplanationResponse, ProviderStatus


class ExplanationProvider(ABC):
    """Abstract interface for evidence explanation providers (local or remote)."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider (e.g. 'mock', 'openai')."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Version of this provider implementation."""
        ...

    @property
    @abstractmethod
    def model_identifier(self) -> str:
        """Identifier of the underlying model (e.g. 'gpt-4o', 'gpt-4o-mini', 'mock-grounded-v1')."""
        ...

    @property
    @abstractmethod
    def api_version(self) -> Optional[str]:
        """API version string when applicable."""
        ...

    @property
    @abstractmethod
    def data_classification(self) -> DataClassification:
        """Classification indicating if data is processed locally or transmitted remotely."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> dict[str, bool]:
        """Provider capabilities (e.g. structured_outputs, streaming, local_execution)."""
        ...

    @abstractmethod
    def check_health(self) -> tuple[ProviderStatus, str]:
        """Check provider operational status without transmitting investigation data.

        Returns:
            (status, message) where status is ProviderStatus.READY, UNAVAILABLE, DISABLED, or ERROR.
        """
        ...

    @abstractmethod
    def generate_explanation(
        self,
        question: str,
        investigation_id: str,
        evidence_context: dict[str, Any],
        timeout_sec: float = 30.0,
    ) -> ExplanationResponse:
        """Generate an evidence-grounded explanation from the supplied context.

        Args:
            question: The user's natural language question.
            investigation_id: Investigation identifier.
            evidence_context: Curated, redacted evidence dictionary with stable evidence IDs.
            timeout_sec: Bounded execution timeout.

        Returns:
            Structured ExplanationResponse containing short answer, observations, hypotheses,
            unknowns, safe questions, and cited evidence references.
        """
        ...
