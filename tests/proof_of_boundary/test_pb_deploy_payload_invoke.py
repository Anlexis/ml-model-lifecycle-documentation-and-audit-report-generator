"""deploy/invoke_payload.json must work when POSTed, not merely match the suite.

`deploy-stg` posts this file to src/api/server.py, which forwards `input` as the request line
and `input_context` as the structured channel in the same call. Both are populated now -- the
request line carries the record as JSON so that a chat user, who has no structured channel,
reaches the same review through the adapter hook. Nothing exercised that combination, and
deploy-stg would have been the first place it ran.

Success alone is not the bar: the answer must carry the record's own facts and must not carry
the redaction sentinel. An answer that succeeds while printing "[MASKED]" as recorded fact is
the defect, not the fix.
"""

import json
from pathlib import Path

import pytest

from framework.schemas.trust_level import TrustLevel
from src.graph.graph import MLModelLifecycleDocAuditAgent

REPO_ROOT = Path(__file__).resolve().parents[2]
MUST_APPEAR = ["Retail Credit PD Scoring Model"]


@pytest.fixture(scope="module")
def payload():
    return json.loads((REPO_ROOT / "deploy" / "invoke_payload.json").read_text(encoding="utf-8"))


def _answer(result) -> str:
    """Whatever the envelope calls its text, flattened once so assertions read plainly."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("output", "formatted_output", "result", "response"):
            got = result.get(key)
            if isinstance(got, str) and got:
                return got
        return json.dumps(result, ensure_ascii=False)
    return str(result)


class TestThePublishedPayloadRunsOverHttp:
    def test_both_channels_together_yield_the_record_unmasked(self, payload):
        from framework.schemas.invocation_context import InvocationContext

        agent = MLModelLifecycleDocAuditAgent()
        agent.compile()
        ctx = InvocationContext(
            session_id="pb-deploy-payload",
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        )
        result = agent.invoke(
            payload["input"],
            ctx=ctx,
            input_context=dict(payload.get("input_context") or {}),
        )
        text = _answer(result)
        assert "[MASKED]" not in text, (
            "the answer carries the redaction sentinel -- the record was read from the "
            "request line, which the S-2 gate masks, instead of from input_context"
        )
        for fact in MUST_APPEAR:
            assert fact in text, f"{fact!r} is in the record but not in the answer"

    def test_the_request_line_is_the_record_so_a_chat_user_can_send_it(self, payload):
        """Chat has one string. If `input` is not the record, chat cannot drive this agent."""
        parsed = json.loads(payload["input"])
        assert isinstance(parsed, dict) and parsed, "`input` must parse to the record object"
