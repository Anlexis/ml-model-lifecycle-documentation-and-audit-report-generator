"""The advisory review must judge the answer against the unmasked record.

The framework's S-2 input gate masks PII shapes in `user_input` before any node runs. The
report is built from `input_context`, which is not masked. Handing the advisory `user_input`
made it compare a correct report against a redacted request and call the report's own values
unsupported -- which is what the platform run showed.
"""

import json
from pathlib import Path

import pytest

from src.nodes.post_process_node import PostProcessNode
import src.nodes.post_process_node as ppn

RECORD_KEY = "model_lifecycle"
MUST_SURVIVE = "Retail Credit PD Scoring Model"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _record():
    payload = json.loads((REPO_ROOT / "deploy" / "invoke_payload.json").read_text(encoding="utf-8"))
    return (payload.get("input_context") or {}).get(RECORD_KEY)


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def _fake(llm, user_input, result, domain="x"):
        seen["basis"] = user_input
        return None  # advisory stays silent; only the basis is under test

    monkeypatch.setattr(ppn, "review_result", _fake)
    return seen


class TestAdvisoryReadsTheSameRequestAsTheReport:
    def test_the_record_is_the_basis_not_the_masked_text(self, captured):
        """`user_input` arrives redacted on the Marketplace path; the record does not."""
        state = {
            "user_input": "Contract Type: [MASKED] party [MASKED]",
            "input_context": {RECORD_KEY: _record()},
            "node_history": [],
        }
        PostProcessNode().execute(state)
        basis = captured.get("basis")
        assert basis is not None, "the advisory was never invoked"
        assert MUST_SURVIVE in basis, (
            "the advisory is judging the report against the masked request -- it will report "
            "the report's own correct values as unsupported"
        )
        assert "[MASKED]" not in basis

    def test_without_a_record_it_falls_back_to_the_request_line(self, captured):
        """No record on the structured channel: there is nothing better to judge against."""
        state = {"user_input": "review this please", "input_context": {}, "node_history": []}
        PostProcessNode().execute(state)
        assert captured.get("basis") == "review this please"
