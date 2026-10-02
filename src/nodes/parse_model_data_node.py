"""AgentCore Platform v1.0"""

# ParseModelDataNode — inner domain node 2.
#
# Classifies the model's risk tier and derives the reportability indicator that
# the compliance step and the dossier both read.
#
# Inner node: ANONYMOUS trust (see InputValidateNode for why).
# Returns ONLY the state keys this node writes (partial-dict contract).

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

# Tiers that make a model reportable on their own.
_HIGH_RISK_TIERS = frozenset({"high", "critical"})
_KNOWN_TIERS = ("critical", "high", "medium", "low")


def _classify_risk_tier(
    declared_tier: str,
    high_risk_function: bool,
    materiality: str,
) -> str:
    """Classify the model risk tier, escalating on materiality and function.

    Returns "critical", "high", "medium" or "low". The declared tier is a floor:
    a material model used in a high-risk business function is escalated to at
    least "high", and a high-risk function alone lifts "low" to "medium". An
    unrecognised or absent declaration is treated as "medium" rather than as the
    lowest tier, so an incomplete record cannot classify itself down.
    """
    tier = declared_tier if declared_tier in _KNOWN_TIERS else "medium"
    if materiality == "material" and high_risk_function and tier in ("low", "medium"):
        return "high"
    if high_risk_function and tier == "low":
        return "medium"
    return tier


class ParseModelDataNode(FunctionNode):
    """Enrich the record with the risk-tier classification and derived fields.

    Input state keys:
        model_data: str  — JSON normalised record

    Output state keys (partial dict):
        model_data: str  — JSON enriched record, same key updated
        status:     str
        error_log:  list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        model_data: Dict[str, Any] = from_json(state.get("model_data"), {}) or {}

        if not model_data:
            emit_trace_event(
                "parse_model_data_failed",
                {"reason": "missing_model_data"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ParseModelDataNode: model_data missing in state"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("ParseModelDataNode: model_data missing in state"),
            }

        model_id = model_data.get("model_id", "unknown")
        declared_tier = str(model_data.get("risk_tier", "unknown")).lower()
        materiality = str(model_data.get("materiality", "unknown")).lower()
        high_risk_function = bool(model_data.get("high_risk_function", False))

        risk_tier = _classify_risk_tier(declared_tier, high_risk_function, materiality)
        fsa_reportable = risk_tier in _HIGH_RISK_TIERS or materiality == "material" or high_risk_function

        enriched: Dict[str, Any] = dict(model_data)
        enriched["risk_tier"] = risk_tier
        enriched["fsa_reportable"] = fsa_reportable
        enriched["materiality"] = materiality

        emit_trace_event(
            "parse_model_data_complete",
            {
                "model_id": model_id,
                "risk_tier": risk_tier,
                "fsa_reportable": fsa_reportable,
                "materiality": materiality,
            },
            state,
        )

        return {
            "model_data": to_json(enriched),
            "status": AgentStatus.SUCCESS.value,
        }
