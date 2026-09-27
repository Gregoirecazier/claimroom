"""One Gemini path for explicit media analysis and newly received photos/videos."""
from __future__ import annotations

from uuid import UUID

from claim_api.analysis import AnalysisService, AnalysisInProgressError, UrgentHandoffError
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.gemini_analysis import GeminiClaimsAnalyzer
from claim_api.models import AnalysisRunRequest, AnalysisRunResponse, CaseView


class MediaAnalysisService:
    def __init__(self, repository, *, storage=None, analyzer=None, on_ready=None):
        self.repository = repository
        self.analyzer = analyzer or GeminiClaimsAnalyzer(storage=storage, media_only=True)
        self.on_ready = on_ready

    def run(self, actor_id: str, case_id: UUID, request: AnalysisRunRequest, *, automatic=False):
        case = self.repository.get_case(UUID(actor_id), case_id)
        if case is None:
            raise CaseNotFoundError
        if case.state_version != request.expected_state_version:
            raise StaleCaseError(case.state_version)
        previous = case.latest_analysis
        if (previous and previous.input_content_revision == case.content_revision
                and previous.method_version == self.analyzer.method_version
                and ((previous.status == 'ready' and previous.output and previous.output.media_analysis)
                     or (automatic and previous.status in {'running', 'failed'}))):
            result = AnalysisRunResponse(case=case, analysis_run=previous)
        else:
            result = AnalysisService(self.repository, self.analyzer).run(actor_id, case_id, request)
        if result.analysis_run.status == 'ready' and self.on_ready:
            self.on_ready(actor_id,result.case)
        return result

    def received(self, actor_id: str, case: CaseView) -> CaseView:
        """The evidence is committed first. Provider failures are persisted by AnalysisService."""
        try:
            return self.run(actor_id, case.id,
                AnalysisRunRequest(expected_state_version=case.state_version), automatic=True).case
        except (StaleCaseError, AnalysisInProgressError, UrgentHandoffError):
            # A concurrent edit/run or human triage must never undo a successful receipt.
            return self.repository.get_case(UUID(actor_id), case.id) or case
