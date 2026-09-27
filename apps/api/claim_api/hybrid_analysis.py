"""Choose text or media analysis from the case snapshot before reserving a run."""

from __future__ import annotations

from claim_api.analysis import ClaimsAnalyzer, PipelexClaimsAnalyzer
from claim_api.gemini_analysis import GeminiClaimsAnalyzer, SUPPORTED_TYPES
from claim_api.models import AnalysisInputV1


class HybridClaimsAnalyzer:
    def __init__(self, *, text_analyzer: ClaimsAnalyzer | None = None,
                 media_analyzer: ClaimsAnalyzer | None = None) -> None:
        self.text_analyzer = text_analyzer or PipelexClaimsAnalyzer()
        self.media_analyzer = media_analyzer or GeminiClaimsAnalyzer()

    def for_input(self, analysis_input: AnalysisInputV1) -> ClaimsAnalyzer:
        if any(item.mime_type in SUPPORTED_TYPES for item in analysis_input.evidence):
            return self.media_analyzer
        return self.text_analyzer
