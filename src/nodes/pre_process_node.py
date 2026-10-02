"""AgentCore Platform v1.0"""

# PreProcessNode — the request boundary of the model-lifecycle dossier agent,
# and the one place where caller data is validated.
#
# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus.<X>.value strings for status assignments
#  - Read input_context via _without_platform_context(state.get("input_context", {})) — read-only
#  - Never import from other agents
#
# Trust: this node occupies the backbone's pre_process slot and declares
# VERIFIED_EXTERNAL, so an unauthenticated or unverified caller is denied before
# the dossier workflow runs. The framework returns an error status from the
# denied node and every downstream node is then skipped, so a refusal here
# contains the whole request rather than merely annotating it.
#
# Validation: everything a caller can send — the request line and the
# structured model record — is screened and bounds-checked by
# src/services/model_contract.py. The template owns that guarantee itself
# rather than relying on the platform's own input gate, which covers the
# free-text field only and scores only part of the control-token class as
# blocking. A refusal names the field and never repeats the value.
#
# The validated record is handed to the inner workflow through the caller
# bridge (src/graph/context_bridge.py); nothing downstream re-parses raw
# request data.

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.services.model_contract import CallerDataError, build_model_contract


# The Marketplace runner seeds input_context with its own conversation history on every
# invocation (shared/bootstrap/marketplace_app.py); the caller neither sends that key nor can
# suppress it, and the build_input_context hook can only overwrite its value, never remove it.
# It is platform plumbing rather than caller data, so it is dropped here, before the caller
# contract runs: the unknown-field guard below stays strict for everything a caller can
# actually send, and no value screen is ever asked to judge a transcript that contains this
# agent's own earlier answers. The value may also be None, which this tolerates.
_PLATFORM_CONTEXT_KEYS = frozenset({"conversation_history"})


def _without_platform_context(raw: Any) -> Any:
    """The caller-supplied half of input_context, platform-injected keys removed."""
    if not isinstance(raw, dict):
        return raw
    return {k: v for k, v in raw.items() if k not in _PLATFORM_CONTEXT_KEYS}


class PreProcessNode(FunctionNode):
    """Trust gate, disallowed-instruction screen and caller-record validation.

    Input state keys:
        user_input:    str   — the request line
        input_context: dict  — carries `model_lifecycle`, the model record
        report_limits: str   — JSON bounds seeded from config/config.yaml

    Output state keys (partial dict):
        validated_input:  str        — the validated request line
        model_contract:   str        — JSON validated model record
        enriched_context: str        — JSON request metadata
        status:           str
        error_log:        list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        request_line = state.get("user_input", "")
        input_context = _without_platform_context(state.get("input_context", {}))  # read-only
        limits = from_json(state.get("report_limits"), {}) or {}

        try:
            contract = build_model_contract(request_line, input_context, limits)
        except CallerDataError as rejected:
            # The message names the field; the rejected value is never carried
            # into the log, the audit event or the response.
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "caller_contract"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: request rejected — {rejected}"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + (f"PreProcessNode: request rejected — {rejected}"),
            }

        model_id = contract["model_id"]
        emit_trace_event(
            "pre_process_validated",
            {
                "model_id": model_id,
                "business_function": contract["business_function"],
                "risk_tier": contract["risk_tier"],
                "change_history_entries": len(contract["change_history"]),
                "performance_metrics": len(contract["performance_metrics"]),
            },
            state,
        )

        return {
            # An inert, bounded string. The record itself never travels in this
            # field: the platform rewrites personal-data shapes here at every
            # node boundary, and a model record is mostly proper nouns.
            "validated_input": model_id,
            "model_contract": to_json(contract),
            "enriched_context": to_json(
                {
                    "source": "MLModelLifecycleDocAuditAgent",
                    "model_id": model_id,
                }
            ),
            "status": AgentStatus.SUCCESS.value,
        }
