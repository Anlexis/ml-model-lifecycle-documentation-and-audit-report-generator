"""AgentCore Platform v1.0"""

# PostProcessNode — the output boundary. The single point at which the
# assembled dossier is released to the caller, and the single point at which it
# is refused.
#
# What the gate scans for, and why it is composed the way it is:
#
#   * credentials — using the framework's own detector rather than a local
#     pattern list. A local list narrower than the framework's is a bypass, not
#     a simplification: the value passes this gate, the framework's mandatory
#     output scan then raises inside this node, and the wrapper replaces the
#     whole returned delta with a bare error — discarding the clearing below
#     along with everything else. Calling the same function keeps the two block
#     sets identical by construction.
#   * personal-data shapes — using the same definition the request boundary
#     strips with. One list, both directions: a second list would drift, and the
#     drift would always favour the leak.
#
# What a violation does, and why clearing is not optional:
#
#   The framework's output envelope resolves the caller-visible value as
#   `formatted_output or result`. There is no status check in that resolution,
#   so an error status alone withholds nothing: an output gate that raises, or
#   that returns an error without overwriting the fields, ships the un-gated
#   document inside the error envelope. A falsy replacement ("" or None) has the
#   same effect, because it re-opens the fallback to `result`.
#
#   So on violation this node returns an error status, writes a TRUTHY notice to
#   formatted_output, and overwrites every state field that carries document
#   text. The cleared set is declared once, below, and pinned by a test, so a
#   field added later cannot quietly stay in the envelope.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

from src.services.model_contract import find_personal_data
from src.services.llm_factory import resolve_llm
from src.services.llm_review import render_review, review_result
import json

logger = logging.getLogger(__name__)

# Every state field that can carry document text. A violation overwrites all of
# them — not only the one the caller reads — so a checkpoint or a later reader
# cannot pick the document back up. tests/unit/test_post_process_node.py pins
# this set against the state schema.
OUTPUT_BEARING_FIELDS: Tuple[str, ...] = (
    "result",
    "audit_report",
    "report_sections",
    "compliance_flags",
)

# Closed set of violation labels. A label says which class of pattern fired and
# nothing else: never the matched text, never its offset, never the field's
# contents. The label is what reaches the caller, the log and the audit record.
VIOLATION_CREDENTIAL = "credential_pattern"
VIOLATION_PERSONAL_DATA = "personal_data_pattern"
VIOLATION_EMPTY_DOCUMENT = "empty_document"

_WITHHELD_NOTICE = (
    "[Model lifecycle dossier withheld: the assembled document did not pass the "
    "output check. Nothing has been released. Contact the model-risk governance "
    "team, quoting the correlation id of this request, for the original document.]"
)


def security_gate_output(content: str) -> Optional[str]:
    """Name the first disallowed class present in the document, or None.

    A module-level function rather than a node method on purpose: the node's own
    gate methods are final on the framework base class, and a subclass that
    tried to override one would fail at class-definition time.
    """
    if detect_credentials(content):
        return VIOLATION_CREDENTIAL
    if find_personal_data(content):
        return VIOLATION_PERSONAL_DATA
    return None


def cleared_output_state() -> Dict[str, Any]:
    """Return every output-bearing field, present and empty.

    Present matters as much as empty. The graph runtime merges partial deltas,
    so a key left out of the returned dict keeps its previous value in state —
    a gate that "cleared" a field by omitting it would clear nothing at all.
    """
    return {field: "" for field in OUTPUT_BEARING_FIELDS}


class PostProcessNode(FunctionNode):
    """Apply the output gate and release or withhold the dossier.

    Declared ANONYMOUS: the caller was already vouched for at the request
    boundary, and requiring more here than the boundary grants would deny every
    real request at the last step.

    Input state keys:
        audit_report:        str   — the assembled dossier
        fsa_filing_required: bool  — the filing determination

    Output state keys (partial dict):
        formatted_output: str
        result:           str
        status:           str
        error_log:        list[str]  — set only on ERROR
        plus every field in OUTPUT_BEARING_FIELDS, cleared, on a violation
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        audit_report: str = state.get("audit_report") or ""
        fsa_filing_required: bool = bool(state.get("fsa_filing_required"))

        # An empty document at this point means the pipeline produced nothing.
        # Releasing a placeholder with a success status would tell the caller a
        # dossier exists when none does, so this fails closed like any other
        # violation.
        _llm, _ = resolve_llm(None, state)
        # The advisory must judge the answer against the SAME request the answer was built
        # from. `user_input` has been PII-masked by the S-2 input gate before this node runs,
        # so on the Marketplace path it reads `[MASKED]` where `input_context.model_lifecycle` holds a
        # real name -- and the advisory then reports the report's own correct values as "not
        # supported by the input". The record is not masked, so it is the honest basis.
        _basis_record = (state.get("input_context") or {}).get("model_lifecycle")
        _basis = json.dumps(_basis_record, ensure_ascii=False) if _basis_record else str(state.get("user_input") or "")
        _remarks = review_result(
            _llm,
            user_input=_basis,
            result=audit_report,
            domain="FIN ML Model Lifecycle Documentation & Audit Report Generator",
        )
        _review = render_review(_remarks)
        # Remarks are LLM text derived from the caller's raw words, so they pass through the
        # same gate the answer does -- appending after the gate would put unscanned text past
        # it. A tripped review is dropped on its own: withholding a correct answer because an
        # advisory remark quoted an identifier would let the review change the outcome, and
        # the whole design rests on it being unable to.
        if _review and isinstance(audit_report, str) and not security_gate_output(audit_report + _review):
            audit_report = audit_report + _review

        violation = VIOLATION_EMPTY_DOCUMENT if not audit_report.strip() else security_gate_output(audit_report)

        if violation:
            logger.error("PostProcessNode: output withheld — %s", violation)
            emit_trace_event(
                "post_process_output_withheld",
                {"violation": violation},
                state,
            )
            return {
                **cleared_output_state(),
                # Truthy on purpose: an empty or absent value here re-opens the
                # envelope's fallback to `result` and releases the document the
                # gate has just refused.
                "formatted_output": _WITHHELD_NOTICE,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: output withheld — {violation}"],
            }

        emit_trace_event(
            "post_process_complete",
            {
                "output_length": len(audit_report),
                "fsa_filing_required": fsa_filing_required,
            },
            state,
        )

        return {
            "formatted_output": audit_report,
            "result": audit_report,
            "status": AgentStatus.SUCCESS.value,
        }
