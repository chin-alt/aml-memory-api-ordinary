"""候选结构修复的确定性回归；不调用模型服务、数据库或 QA。"""

import json

from jiuwen_memory.common.type_def import MemoryUnit, Segment
from jiuwen_memory.construction.extractor_impl.llm_extractor import (
    ExtractorImpl,
    InvalidExtractionCandidateError,
    InvalidExtractionJSONError,
)


class SequenceLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, messages, **options):
        self.calls.append((messages, options))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response)


def source():
    return MemoryUnit(id="u1", segments=[Segment(content="Alice owns three bicycles.")])


def valid_item():
    return {"source_id": "u1", "content": "Alice owns three bicycles.", "confidence": 1.0}


def test_repairs_once_using_same_sources_and_same_generation_options():
    wrong = {**valid_item(), "source_id": "private-invalid-model-value"}
    llm = SequenceLLM([[wrong], [valid_item()]])
    result = ExtractorImpl(llm, retry_max_retries=1).extract([source()])
    assert len(result) == 1
    assert result[0].provenance == ["u1"]
    assert result[0].content == "Alice owns three bicycles."
    assert len(llm.calls) == 2
    initial, repaired = llm.calls
    assert initial[0][1].content == repaired[0][1].content
    assert initial[1] == repaired[1] == {"temperature": 0, "max_tokens": 8192}
    assert "source_id_not_in_batch" in repaired[0][0].content
    assert "private-invalid-model-value" not in repaired[0][0].content


def test_repeated_invalid_candidates_fail_after_one_repair():
    llm = SequenceLLM([[{}], [{}]])
    try:
        ExtractorImpl(llm, retry_max_retries=1).extract([source()])
    except InvalidExtractionCandidateError as exc:
        assert exc.reason_counts == {"missing_confidence": 1}
    else:
        raise AssertionError("invalid output must not become a successful empty extraction")
    assert len(llm.calls) == 2


def test_partial_success_and_explicit_empty_do_not_retry():
    for response, count in (([valid_item(), {}], 1), ([], 0)):
        llm = SequenceLLM([response])
        assert len(ExtractorImpl(llm).extract([source()])) == count
        assert len(llm.calls) == 1


def test_transport_failure_does_not_trigger_candidate_repair():
    failure = RuntimeError("transport failure")
    llm = SequenceLLM([failure])
    try:
        ExtractorImpl(llm, retry_max_retries=1).extract([source()])
    except RuntimeError as exc:
        assert exc is failure
    else:
        raise AssertionError("transport failure must propagate")
    assert len(llm.calls) == 1


def test_reason_codes_cover_field_validation_without_model_values():
    bad = "private-invalid-model-value"
    items = [None, {}, {"confidence": bad}, {"confidence": "nan"},
             {**valid_item(), "source_id": bad}, {**valid_item(), "content": ""},
             {**valid_item(), "evidence": [bad]}]
    try:
        ExtractorImpl(SequenceLLM([])).build_candidates(items, [source()])
    except InvalidExtractionCandidateError as exc:
        assert exc.reason_counts == {
            "not_object": 1, "missing_confidence": 1, "invalid_confidence": 1,
            "confidence_out_of_range": 1, "source_id_not_in_batch": 1,
            "empty_content": 1, "non_string_evidence": 1,
        }
        assert bad not in str(exc)
    else:
        raise AssertionError("all candidates are invalid")


def test_invalid_json_repairs_once_with_original_source_and_fixed_feedback():
    llm = SequenceLLM(["private malformed model output", [valid_item()]])
    result = ExtractorImpl(llm, retry_max_retries=1).extract([source()])
    assert len(result) == 1
    assert result[0].provenance == ["u1"]
    assert len(llm.calls) == 2
    first, repair = llm.calls
    assert first[0][1].content == repair[0][1].content
    assert first[1] == repair[1]
    assert "invalid_json" in repair[0][0].content
    assert "private malformed model output" not in repair[0][0].content


def test_invalid_json_twice_raises_without_third_call():
    llm = SequenceLLM(["not json", "still not json", [valid_item()]])
    try:
        ExtractorImpl(llm, retry_max_retries=1).extract([source()])
    except InvalidExtractionJSONError:
        pass
    else:
        raise AssertionError("malformed JSON must remain a failure after repair")
    assert len(llm.calls) == 2


def test_json_and_candidate_errors_share_one_repair_budget():
    for responses, expected in ((["not json", [{}]], InvalidExtractionCandidateError),
                                ([[{}], "not json"], InvalidExtractionJSONError)):
        llm = SequenceLLM([*responses, [valid_item()]])
        try:
            ExtractorImpl(llm, retry_max_retries=1).extract([source()])
        except expected:
            pass
        else:
            raise AssertionError("switching validation error types must not allow another repair")
        assert len(llm.calls) == 2


def test_transport_error_during_json_repair_propagates():
    failure = RuntimeError("transport failure during repair")
    llm = SequenceLLM(["not json", failure])
    try:
        ExtractorImpl(llm, retry_max_retries=1).extract([source()])
    except RuntimeError as exc:
        assert exc is failure
    else:
        raise AssertionError("repair transport failure must propagate")
    assert len(llm.calls) == 2
