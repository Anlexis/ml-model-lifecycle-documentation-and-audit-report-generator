"""What the Marketplace adapter hook accepts from a chat message.

Chat sends one string. This hook is the only thing that can move a record out of that string
and into `input_context`, which the S-2 input gate does not mask -- so the shapes it recognises
are the entire usable surface of this agent from chat, and the shapes it refuses are the ones a
caller will see a refusal for.
"""

import json
from pathlib import Path

import pytest

from src.services.chat_context import RECORD_KEY, build_input_context

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_FIELD = "model_id"


@pytest.fixture(scope="module")
def record():
    """The published record itself, so this test and deploy/invoke_payload.json cannot drift."""
    payload = json.loads((REPO_ROOT / "deploy" / "invoke_payload.json").read_text(encoding="utf-8"))
    rec = (payload.get("input_context") or {}).get(RECORD_KEY)
    assert isinstance(rec, dict) and PROOF_FIELD in rec
    return rec


def _turn(text):
    return [{"role": "user", "content": text if isinstance(text, str) else json.dumps(text)}]


class TestTheShapesACallerMightPaste:
    def test_the_record_alone(self, record):
        assert build_input_context(history=_turn(record)) == {RECORD_KEY: record}

    def test_the_input_context_alone(self, record):
        assert build_input_context(history=_turn({RECORD_KEY: record})) == {RECORD_KEY: record}

    def test_the_whole_published_payload_file(self, record):
        """A reviewer pasted this. It is what the repository publishes, so it must work."""
        envelope = {"input": "", "session_id": "x", "input_context": {RECORD_KEY: record}}
        assert build_input_context(history=_turn(envelope)) == {RECORD_KEY: record}

    def test_the_newest_user_turn_wins_over_earlier_ones(self, record):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": json.dumps(record)},
        ]
        assert build_input_context(history=history) == {RECORD_KEY: record}


class TestNothingIsGuessed:
    @pytest.mark.parametrize(
        "text",
        [
            "please review the attached document",
            "{not json at all",
            "[1, 2, 3]",
            '{"unrelated": "object"}',
        ],
    )
    def test_a_message_without_a_record_yields_nothing(self, text):
        """The caller contract then refuses with a reason -- that refusal is the right answer."""
        assert build_input_context(history=_turn(text)) == {}

    @pytest.mark.parametrize("history", [None, [], [{"role": "assistant", "content": "hi"}]])
    def test_no_user_turn_yields_nothing(self, history):
        assert build_input_context(history=history) == {}

    def test_a_record_missing_its_identifying_field_is_not_accepted(self, record):
        without = {k: v for k, v in record.items() if k != PROOF_FIELD}
        assert build_input_context(history=_turn(without)) == {}
