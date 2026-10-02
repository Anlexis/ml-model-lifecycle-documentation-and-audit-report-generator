"""AgentCore Platform v1.0"""

# InputValidateNode — inner domain node 1.
#
# The record reaching this node has already passed the caller contract at the
# request boundary: every field is bounded, every identifier inert, every number
# finite. This node does the domain step that follows — confirming the record is
# complete enough to build a dossier from, and deriving the fields the rest of
# the pipeline reads.
#
# Inner node: ANONYMOUS trust. The boundary node already vouched for the caller,
# and an inner node that demanded a higher level than the boundary grants would
# deny every real request.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

# Fields without which the dossier would have a title and nothing to document.
_REQUIRED_RECORD_FIELDS = ("model_id", "model_name", "business_function")


class InputValidateNode(FunctionNode):
    """Confirm the seeded record is complete and derive the dossier fields.

    Input state keys:
        model_contract: str  — JSON validated record, seeded by the graph's
                               initial-state hook from the caller bridge

    Output state keys (partial dict):
        model_data: str   — JSON normalised record for the downstream nodes
        status:     str
        error_log:  list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        contract: Dict[str, Any] = from_json(state.get("model_contract"), {}) or {}

        if not contract:
            emit_trace_event(
                "input_validate_failed",
                {"reason": "missing_record"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidateNode: no validated model record reached the workflow"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("InputValidateNode: no validated model record reached the workflow"),
            }

        missing = [name for name in _REQUIRED_RECORD_FIELDS if not contract.get(name)]
        if missing:
            emit_trace_event(
                "input_validate_failed",
                {"reason": "incomplete_record", "missing": sorted(missing)},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"InputValidateNode: model record is missing required fields: {sorted(missing)}"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + (f"InputValidateNode: model record is missing required fields: {sorted(missing)}"),
            }

        model_data: Dict[str, Any] = dict(contract)
        # Drop the bounds from the working record: they are configuration, not
        # part of the documented model, and they must not reach the dossier.
        model_data.pop("limits", None)

        emit_trace_event(
            "input_validate_complete",
            {
                "model_id": model_data["model_id"],
                "model_type": model_data.get("model_type", "Unspecified"),
                "business_function": model_data["business_function"],
                "risk_tier": model_data.get("risk_tier", "unknown"),
            },
            state,
        )

        return {
            "model_data": to_json(model_data),
            "status": AgentStatus.SUCCESS.value,
        }
