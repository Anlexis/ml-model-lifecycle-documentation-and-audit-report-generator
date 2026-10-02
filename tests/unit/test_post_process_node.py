# The output boundary: what is released, what is withheld, and what is left in
# state after a refusal.
#
# The assertions here are about PRESENCE and EMPTINESS together, never emptiness
# alone. The graph runtime merges partial deltas, so a key a node leaves out of
# its returned dict keeps whatever it had in state: `assert not result.get(f)` is
# True for a gate that cleared nothing at all, and would pass on the exact defect
# it looks like it is testing.

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import (
    OUTPUT_BEARING_FIELDS,
    VIOLATION_CREDENTIAL,
    VIOLATION_EMPTY_DOCUMENT,
    VIOLATION_PERSONAL_DATA,
    PostProcessNode,
    cleared_output_state,
    security_gate_output,
)

CLEAN_REPORT = (
    "========================================\n"
    "ML MODEL LIFECYCLE DOCUMENTATION & AUDIT REPORT\n"
    "Model ID:   FIN-MDL-20260712-001\n"
    "Model Name: Retail Credit PD Scoring Model\n"
)

# Every pattern the framework's own detector knows. The gate delegates to that
# detector rather than keeping a local list, so this table is the proof the two
# have not drifted: a template list narrower than the framework's is a bypass,
# because the framework then raises inside the node and the wrapper discards the
# node's clearing along with the rest of its result.
CREDENTIAL_SAMPLES = [
    ("stripe_key", "billing key sk_live_" + "abcdefghijklmnop0123 in the dossier"),
    ("openai_key", "token sk-abcdefghij0123456789ABCDEF in the dossier"),
    ("jwt", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghij.klmnopqrst"),
    ("aws_key", "access key AKIAIOSFODNN7EXAMPLE recorded by the owner"),
    ("bearer", "Authorization: Bearer abcdefghijklmnop0123456789"),
    ("conn_string", "feature store at postgresql://feature-store.internal:5432/models_registry"),
]

PERSONAL_DATA_SAMPLES = [
    "Monitoring owner: owner.name@example.co.jp",
    "Escalation line: 03-1234-5678",
]


def _state(report, **extra):
    state = {
        "audit_report": report,
        "report_sections": '{"risk_parameters": "Risk Tier: HIGH"}',
        "compliance_flags": '{"fsa_filing_required": true}',
        "result": report,
        "fsa_filing_required": True,
    }
    state.update(extra)
    return state


@pytest.fixture()
def node():
    return PostProcessNode()


class TestGateDetection:
    @pytest.mark.parametrize("name,sample", CREDENTIAL_SAMPLES, ids=[s[0] for s in CREDENTIAL_SAMPLES])
    def test_every_framework_credential_pattern_is_caught(self, name, sample):
        assert security_gate_output(sample) == VIOLATION_CREDENTIAL

    @pytest.mark.parametrize("sample", PERSONAL_DATA_SAMPLES)
    def test_personal_data_shapes_are_caught(self, sample):
        assert security_gate_output(sample) == VIOLATION_PERSONAL_DATA

    def test_an_ordinary_dossier_passes(self):
        assert security_gate_output(CLEAN_REPORT) is None

    def test_domain_text_that_merely_mentions_a_token_passes(self):
        assert security_gate_output("The model consumes a bearer token from the gateway.") is None


class TestRelease:
    def test_a_clean_dossier_is_released(self, node):
        result = node.execute(_state(CLEAN_REPORT))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == CLEAN_REPORT
        assert result["result"] == CLEAN_REPORT

    def test_trust_level_matches_the_boundary_grant(self, node):
        assert node.required_trust_level == TrustLevel.ANONYMOUS


class TestWithholding:
    @pytest.mark.parametrize("name,sample", CREDENTIAL_SAMPLES, ids=[s[0] for s in CREDENTIAL_SAMPLES])
    def test_a_credential_bearing_dossier_is_withheld(self, node, name, sample):
        result = node.execute(_state(CLEAN_REPORT + sample))
        assert result["status"] == AgentStatus.ERROR.value
        assert sample not in str(result)

    @pytest.mark.parametrize("sample", PERSONAL_DATA_SAMPLES)
    def test_a_personal_data_bearing_dossier_is_withheld(self, node, sample):
        result = node.execute(_state(CLEAN_REPORT + sample))
        assert result["status"] == AgentStatus.ERROR.value
        assert sample not in str(result)

    def test_an_empty_dossier_fails_closed_rather_than_returning_a_placeholder(self, node):
        result = node.execute(_state("   "))
        assert result["status"] == AgentStatus.ERROR.value
        assert VIOLATION_EMPTY_DOCUMENT in result["error_log"][0]

    def test_the_replacement_is_truthy(self, node):
        """A falsy replacement re-opens the envelope's fallback to `result`."""
        result = node.execute(_state(CLEAN_REPORT + CREDENTIAL_SAMPLES[0][1]))
        assert bool(result["formatted_output"]) is True

    def test_every_output_bearing_field_is_present_and_empty(self, node):
        result = node.execute(_state(CLEAN_REPORT + CREDENTIAL_SAMPLES[0][1]))
        for field in OUTPUT_BEARING_FIELDS:
            assert field in result, f"{field} must be PRESENT in the returned delta"
            assert result[field] == "", f"{field} must be cleared, not carried"

    def test_the_notice_carries_no_document_text(self, node):
        report = CLEAN_REPORT + CREDENTIAL_SAMPLES[0][1]
        result = node.execute(_state(report))
        notice = result["formatted_output"]
        assert "FIN-MDL-20260712-001" not in notice
        assert "Retail Credit PD Scoring Model" not in notice
        assert "Risk Tier" not in notice

    def test_the_violation_label_comes_from_the_closed_set(self, node):
        for _name, sample in CREDENTIAL_SAMPLES:
            result = node.execute(_state(CLEAN_REPORT + sample))
            message = result["error_log"][0]
            assert any(
                label in message for label in (VIOLATION_CREDENTIAL, VIOLATION_PERSONAL_DATA, VIOLATION_EMPTY_DOCUMENT)
            )

    def test_no_traceback_or_source_path_reaches_the_error_log(self, node):
        result = node.execute(_state(CLEAN_REPORT + CREDENTIAL_SAMPLES[0][1]))
        message = result["error_log"][0]
        assert "Traceback" not in message
        assert "/src/" not in message
        assert ".py" not in message


class TestClearedSetInventory:
    """The cleared set is pinned against what the workflow actually writes.

    The inventory is DERIVED from the outer graph node's own mapping rather than
    restated, so adding a field to that mapping without adding it to the cleared
    set fails here. Restating the four names in both places would prove nothing:
    the two lists would agree by construction and a fifth field could join the
    envelope unnoticed.
    """

    # Fields the mapping carries that hold no document text: a status string and
    # a boolean determination. Listed explicitly so a new field is a failure
    # until someone classifies it.
    NON_DOCUMENT_FIELDS = {"status", "fsa_filing_required"}

    def test_the_cleared_set_covers_every_document_bearing_field_the_workflow_writes(self):
        from src.graph.graph import ModelAuditGraphNode
        from src.schemas.state import State

        probe = {
            "audit_report": "document",
            "report_sections": "{}",
            "compliance_flags": "{}",
            "fsa_filing_required": True,
            "status": "success",
        }
        written = set(ModelAuditGraphNode().merge_output({}, probe))
        document_bearing = written - self.NON_DOCUMENT_FIELDS
        # `result` is written by this node itself, not by the mapping.
        assert set(OUTPUT_BEARING_FIELDS) == document_bearing | {"result"}
        assert set(OUTPUT_BEARING_FIELDS) <= set(State.__annotations__)

    def test_cleared_output_state_returns_the_whole_set(self):
        cleared = cleared_output_state()
        assert set(cleared) == set(OUTPUT_BEARING_FIELDS)
        assert all(value == "" for value in cleared.values())
