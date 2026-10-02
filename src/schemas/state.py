"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic model. Graph checkpoints are
# serialized with msgpack, and object graphs corrupt silently on the round trip.
# Extend AgentState with agent-specific fields only. Never put credentials or
# secrets here: state is what a checkpoint persists.
#
# FIN-C2-056 — model lifecycle documentation and audit report generator.
# Two-layer graph: outer backbone (AgentBaseGraph) + inner domain workflow
# (BaseGraph). The fields below cover both layers.
#
# Every dict/list-valued field is stored as a JSON-serialized Optional[str] and
# read back through from_json(). Use the two helpers below at every producer and
# every consumer — one contract end to end. A bare dict or list field breaks
# msgpack serialization.

import json
from typing import Any, NotRequired, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a value to a JSON string for state storage."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON string from state storage."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for FIN-C2-056.

    All shared fields (user_input, input_context, status, session_id,
    node_history, error_log, hitl_*, ...) are inherited from AgentState.
    formatted_output is inherited too and is NOT re-declared here.
    """

    # ------------------------------------------------------------------
    # Outer layer — set by PreProcessNode (the request boundary)
    # ------------------------------------------------------------------

    # The request line, after validation. An inert, bounded string; the model
    # record itself never travels in this field, because the platform rewrites
    # personal-data shapes here at every node boundary.
    validated_input: NotRequired[Optional[str]]

    # JSON-serialised validated model record — the whole caller contract, after
    # every field has passed its bounds and inert-shape check.
    # Shape: {model_id, model_name, model_type, business_function,
    #   high_risk_function (bool), development (dict), training_data (dict),
    #   validation (dict), performance_metrics (list of {name, value}),
    #   risk_parameters (dict), risk_tier, materiality,
    #   change_history (list), monitoring (dict), limits (dict)}
    model_contract: NotRequired[Optional[str]]

    # JSON-serialised request metadata. Shape: {"source": str, "model_id": str}.
    enriched_context: NotRequired[Optional[str]]

    # JSON-serialised report bounds, seeded from config/config.yaml `report:`
    # by the graph's initial-state hook so the nodes read the declared values
    # rather than their own defaults.
    report_limits: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # JSON-serialised enriched model record: the validated contract plus the
    # derived risk_tier and reportability fields.
    model_data: NotRequired[Optional[str]]

    # JSON-serialised report section dict. Keys are the seven required
    # sections: model_development_rationale, training_data_description,
    # validation_methodology, performance_metrics, risk_parameters,
    # change_history, monitoring_schedule. Each value is the rendered text.
    report_sections: NotRequired[Optional[str]]

    # JSON-serialised filing-determination result. Shape:
    # {fsa_filing_required: bool, reason: str, risk_tier: str,
    #  materiality: str, business_function: str, regulatory_basis: str}
    compliance_flags: NotRequired[Optional[str]]

    # True when the model requires a filing / inspection dossier under the
    # model risk management framework.
    fsa_filing_required: NotRequired[Optional[bool]]

    # The assembled dossier text, inspection-ready. Built by the inner
    # OutputFormatNode from report_sections + compliance_flags.
    audit_report: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Outer layer — set by PostProcessNode (the output boundary)
    # ------------------------------------------------------------------

    # Primary result surfaced to the caller. Set to the gated dossier text once
    # the output gate passes, and cleared to an empty string when it does not.
    result: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Tracing — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: NotRequired[Optional[str]]
    correlation_id: NotRequired[Optional[str]]
    # node_history is inherited from AgentState
