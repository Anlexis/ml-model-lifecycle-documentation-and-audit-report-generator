"""AgentCore Platform v1.0"""

# OutputFormatNode — inner domain node 5, last in the workflow.
#
# Assembles the dossier from the rendered sections and the filing
# determination. This node produces the document; nothing releases it to the
# caller until the output gate in the post_process slot has scanned it.
#
# Inner node: ANONYMOUS trust.
# Returns ONLY the state keys this node writes (partial-dict contract).

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json
from src.services.model_contract import resolve_limits

# Section order and headings for the assembled document.
_SECTION_ORDER: List[str] = [
    "model_development_rationale",
    "training_data_description",
    "validation_methodology",
    "performance_metrics",
    "risk_parameters",
    "change_history",
    "monitoring_schedule",
]

_SECTION_HEADERS: Dict[str, str] = {
    "model_development_rationale": "1. Model Development Rationale",
    "training_data_description": "2. Training Data Description",
    "validation_methodology": "3. Validation Methodology",
    "performance_metrics": "4. Performance Metrics",
    "risk_parameters": "5. Risk Parameters",
    "change_history": "6. Change History",
    "monitoring_schedule": "7. Ongoing Monitoring Schedule",
}

_DEFAULT_BASIS = "FSA AI Model Risk Management Framework / 金融庁 モデル・リスク管理に関する原則"


def assemble_report(
    model_id: str,
    model_name: str,
    sections: Dict[str, str],
    compliance: Dict[str, Any],
    line_width: int,
) -> str:
    """Assemble the dossier from the rendered sections and the determination.

    `line_width` is the declared document width from config/config.yaml: it
    fixes the length of the rules that separate the document's parts. A model
    risk submission is read as fixed-width plain text and some review workflows
    pin that width, so it is a deployment setting rather than a constant.
    """
    separator = "=" * line_width
    subsep = "-" * line_width

    lines = [
        separator,
        "ML MODEL LIFECYCLE DOCUMENTATION & AUDIT REPORT",
        f"Model ID:   {model_id}",
        f"Model Name: {model_name}",
        separator,
        "",
    ]

    for key in _SECTION_ORDER:
        lines.append(_SECTION_HEADERS[key])
        lines.append(subsep)
        lines.append(sections.get(key, "(Section not generated)"))
        lines.append("")

    lines += [
        separator,
        "MODEL RISK MANAGEMENT COMPLIANCE NOTE",
        subsep,
        f"  Regulatory Basis:      {compliance.get('regulatory_basis', _DEFAULT_BASIS)}",
        "  Filing Required:       "
        + ("YES — prepare the model-risk inspection dossier" if compliance.get("fsa_filing_required", False) else "NO"),
        f"  Determination Basis:   {compliance.get('reason', 'Not determined')}",
        separator,
    ]

    return "\n".join(lines)


class OutputFormatNode(FunctionNode):
    """Assemble the dossier document.

    Input state keys:
        report_sections:  str  — JSON section dict
        compliance_flags: str  — JSON determination result
        model_data:       str  — JSON enriched record
        report_limits:    str  — JSON bounds; line_width is read here

    Output state keys (partial dict):
        audit_report: str
        status:       str
        error_log:    list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        sections: Dict[str, str] = from_json(state.get("report_sections"), {}) or {}
        compliance: Dict[str, Any] = from_json(state.get("compliance_flags"), {}) or {}
        model_data: Dict[str, Any] = from_json(state.get("model_data"), {}) or {}
        limits = resolve_limits(from_json(state.get("report_limits"), {}))

        model_id = str(model_data.get("model_id", "unknown"))
        model_name = str(model_data.get("model_name", "Not stated"))

        if not sections:
            emit_trace_event(
                "output_format_failed",
                {"reason": "missing_report_sections", "model_id": model_id},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"OutputFormatNode: report_sections missing for model_id={model_id}"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + (f"OutputFormatNode: report_sections missing for model_id={model_id}"),
            }

        report = assemble_report(model_id, model_name, sections, compliance, limits["line_width"])

        emit_trace_event(
            "output_format_complete",
            {
                "model_id": model_id,
                "report_length": len(report),
                "section_count": len(sections),
                "line_width": limits["line_width"],
                "fsa_filing_required": compliance.get("fsa_filing_required", False),
            },
            state,
        )

        # `result` is deliberately NOT written here. The dossier reaches the
        # caller only through the output gate in the post_process slot, so there
        # is exactly one release point rather than two.
        return {
            "audit_report": report,
            "status": AgentStatus.SUCCESS.value,
        }
