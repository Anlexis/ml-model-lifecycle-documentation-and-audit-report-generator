"""AgentCore Platform v1.0"""

# ComplianceCheckNode — inner domain node 4.
#
# Determines whether the documented model requires a filing / inspection dossier
# under the model risk management framework
# (金融庁 モデル・リスク管理に関する原則, "Principles for Model Risk Management").
#
# Filing criteria (rule-based, all three are independent triggers):
#   - risk_tier in {high, critical}, OR
#   - materiality == "material", OR
#   - the business function is one the framework treats as high-risk
#     (credit_scoring, capital_adequacy, market_risk, aml, fraud_detection,
#      underwriting)
#
# The determination is a prompt for the model-risk function to act on, not a
# legal conclusion — so the result carries which trigger fired, not only the
# verdict.
#
# Inner node: ANONYMOUS trust.
# Returns ONLY the state keys this node writes (partial-dict contract).

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

_HIGH_RISK_TIERS = frozenset({"high", "critical"})
_REGULATORY_BASIS = "FSA AI Model Risk Management Framework / 金融庁 モデル・リスク管理に関する原則"


def _determine_filing(
    risk_tier: str,
    materiality: str,
    high_risk_function: bool,
    business_function: str,
) -> Dict[str, Any]:
    """Evaluate the filing criteria and record which of them fired."""
    tier_trigger = risk_tier in _HIGH_RISK_TIERS
    materiality_trigger = materiality == "material"
    function_trigger = high_risk_function
    fsa_filing_required = tier_trigger or materiality_trigger or function_trigger

    reasons = []
    if tier_trigger:
        reasons.append(f"Risk tier ({risk_tier}) is in {sorted(_HIGH_RISK_TIERS)}")
    if materiality_trigger:
        reasons.append("Model is classified as material")
    if function_trigger:
        reasons.append(f"Business function ({business_function}) is a high-risk function")
    if not reasons:
        reasons.append(
            f"Risk tier ({risk_tier}) is not high or critical, materiality "
            f"({materiality}) is not material, and business function "
            f"({business_function}) is not a high-risk function"
        )

    return {
        "fsa_filing_required": fsa_filing_required,
        "reason": "; ".join(reasons),
        "risk_tier": risk_tier,
        "materiality": materiality,
        "business_function": business_function,
        "tier_trigger": tier_trigger,
        "materiality_trigger": materiality_trigger,
        "function_trigger": function_trigger,
        "regulatory_basis": _REGULATORY_BASIS,
    }


class ComplianceCheckNode(FunctionNode):
    """Filing determination for the documented model.

    Input state keys:
        model_data: str  — JSON enriched record

    Output state keys (partial dict):
        compliance_flags:    str   — JSON determination result
        fsa_filing_required: bool
        status:              str
        error_log:           list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        model_data: Dict[str, Any] = from_json(state.get("model_data"), {}) or {}

        if not model_data:
            emit_trace_event(
                "compliance_check_failed",
                {"reason": "missing_model_data"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ComplianceCheckNode: model_data missing in state"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("ComplianceCheckNode: model_data missing in state"),
            }

        model_id = model_data.get("model_id", "unknown")
        risk_tier = str(model_data.get("risk_tier", "unknown")).lower()
        materiality = str(model_data.get("materiality", "unknown")).lower()
        high_risk_function = bool(model_data.get("high_risk_function", False))
        business_function = str(model_data.get("business_function", "unknown"))

        result = _determine_filing(risk_tier, materiality, high_risk_function, business_function)
        fsa_filing_required = bool(result["fsa_filing_required"])

        emit_trace_event(
            "compliance_check_complete",
            {
                "model_id": model_id,
                "fsa_filing_required": fsa_filing_required,
                "tier_trigger": result["tier_trigger"],
                "materiality_trigger": result["materiality_trigger"],
                "function_trigger": result["function_trigger"],
            },
            state,
        )

        return {
            "compliance_flags": to_json(result),
            "fsa_filing_required": fsa_filing_required,
            "status": AgentStatus.SUCCESS.value,
        }
