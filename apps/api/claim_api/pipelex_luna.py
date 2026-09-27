"""Keep Luna synthesis non-reasoning, matching the previous latency/output budget.

Pipelex 0.65's structured Responses worker does not forward reasoning_effort.
Its supported extras-factory hook sets the API parameter explicitly, so the
provider default cannot silently enable reasoning while temperature is sent.
"""

from pipelex.cogt.inference.inference_manager import InferenceManager
from pipelex.providers.openai.openai_responses_factory import OpenAIResponsesFactory
from pipelex.providers.openai.openai_responses_llm_worker import OpenAIResponsesLLMWorker


class LunaResponsesFactory(OpenAIResponsesFactory):
    def make_extras(self, inference_model, *, inference_job, output_desc):
        headers, body = super().make_extras(
            inference_model, inference_job=inference_job, output_desc=output_desc)
        if inference_model.model_id == "gpt-6-luna":
            body["reasoning"] = {"effort": "none"}
        return headers, body


class ClaimsInferenceManager(InferenceManager):
    def get_llm_worker(self, llm_handle):
        worker = super().get_llm_worker(llm_handle)
        if (worker.inference_model.model_id == "gpt-6-luna"
                and isinstance(worker, OpenAIResponsesLLMWorker)):
            worker.openai_responses_factory = LunaResponsesFactory(is_http_url_enabled=True)
        return worker
