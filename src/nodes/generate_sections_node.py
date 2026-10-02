"""AgentCore Platform v1.0"""

# GenerateSectionsNode — inner domain node 3.
#
# Renders the seven documentation sections a model risk management submission
# has to carry:
#
#   1. model_development_rationale
#   2. training_data_description
#   3. validation_methodology
#   4. performance_metrics
#   5. risk_parameters
#   6. change_history
#   7. monitoring_schedule
#
# The rendering is deterministic: each section is composed from the validated
# record by fixed templates, so the same record always produces the same
# document and the dossier can be diffed between revisions. The agent declares
# `generation_mode: deterministic` in its manifest for that reason; it makes no
# model call and reads no model settings.
#
# Inner node: ANONYMOUS trust.
# Returns ONLY the state keys this node writes (partial-dict contract).

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

_UNSTATED = "Not stated"


def _text(value: Any, fallback: str = _UNSTATED) -> str:
    """Render an optional record value, or a fixed placeholder when absent.

    The placeholder is deliberately uniform: a submission reader has to be able
    to tell "the model owner did not supply this" from "the value is empty", and
    a blank line reads as neither.
    """
    if value is None or value == "":
        return fallback
    return str(value)


def _format_metric(value: float) -> str:
    """Render a metric value.

    Every value reaching here has already been parsed as finite and bounded at
    the request boundary, so there is no non-finite case to render. Integers are
    printed without a trailing ".0" so counts read as counts.
    """
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _section_development_rationale(d: Dict[str, Any]) -> str:
    dev: Dict[str, Any] = d.get("development") or {}
    rationale = _text(
        dev.get("rationale"),
        "Development rationale not supplied by the model owner.",
    )
    return "\n".join(
        [
            f"Model ID:          {_text(d.get('model_id'))}",
            f"Model Name:        {_text(d.get('model_name'))}",
            f"Model Type:        {_text(d.get('model_type'))}",
            f"Business Function: {_text(d.get('business_function'))}",
            f"Developed By:      {_text(dev.get('developed_by'))}",
            f"Development Date:  {_text(dev.get('development_date'))}",
            "",
            "Rationale:",
            f"  {rationale}",
        ]
    )


def _section_training_data(d: Dict[str, Any]) -> str:
    td: Dict[str, Any] = d.get("training_data") or {}
    return "\n".join(
        [
            f"  Description:   {_text(td.get('description'), 'Training data description not supplied.')}",
            f"  Data Period:   {_text(td.get('period'))}",
            f"  Record Count:  {_text(td.get('record_count'))}",
            f"  Feature Count: {_text(td.get('features'))}",
            f"  Data Source:   {_text(td.get('source'))}",
        ]
    )


def _section_validation_methodology(d: Dict[str, Any]) -> str:
    val: Dict[str, Any] = d.get("validation") or {}
    return "\n".join(
        [
            f"  Methodology:     {_text(val.get('methodology'), 'Validation methodology not supplied.')}",
            f"  Validated By:    {_text(val.get('validated_by'))}",
            f"  Validation Date: {_text(val.get('validation_date'))}",
            f"  Independent:     {'YES' if val.get('independent') else 'NOT STATED'}",
        ]
    )


def _section_performance_metrics(d: Dict[str, Any]) -> str:
    metrics: List[Dict[str, Any]] = d.get("performance_metrics") or []
    if not metrics:
        return "  No performance metrics recorded."
    return "\n".join(
        f"  {str(metric.get('name', '')).upper()}: {_format_metric(metric.get('value', 0.0))}" for metric in metrics
    )


def _section_risk_parameters(d: Dict[str, Any]) -> str:
    rp: Dict[str, Any] = d.get("risk_parameters") or {}
    return "\n".join(
        [
            f"  Risk Tier:          {str(d.get('risk_tier', 'unknown')).upper()}",
            f"  Materiality:        {str(d.get('materiality', 'unknown')).upper()}",
            f"  PD Range:           {_text(rp.get('pd_range'))}",
            f"  Risk Appetite:      {_text(rp.get('risk_appetite'))}",
            f"  High-Risk Function: {'YES' if d.get('high_risk_function') else 'NO'}",
        ]
    )


def _section_change_history(d: Dict[str, Any]) -> str:
    history: List[Dict[str, Any]] = d.get("change_history") or []
    if not history:
        return "  No change history recorded (initial model version)."
    return "\n".join(
        f"  {_text(entry.get('version'))} ({_text(entry.get('date'))}): {_text(entry.get('change'))}"
        for entry in history
    )


def _section_monitoring_schedule(d: Dict[str, Any]) -> str:
    mon: Dict[str, Any] = d.get("monitoring") or {}
    return "\n".join(
        [
            f"  Monitoring Frequency:  {_text(mon.get('schedule'), 'Not scheduled')}",
            f"  Next Review Date:      {_text(mon.get('next_review'))}",
            f"  Monitoring Owner:      {_text(mon.get('owner'))}",
            f"  Performance Threshold: {_text(mon.get('threshold'))}",
        ]
    )


class GenerateSectionsNode(FunctionNode):
    """Render the seven documentation sections from the validated record.

    Input state keys:
        model_data: str  — JSON enriched record

    Output state keys (partial dict):
        report_sections: str  — JSON section dict
        status:          str
        error_log:       list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        model_data: Dict[str, Any] = from_json(state.get("model_data"), {}) or {}

        if not model_data:
            emit_trace_event(
                "generate_sections_failed",
                {"reason": "missing_model_data"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GenerateSectionsNode: model_data missing in state"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("GenerateSectionsNode: model_data missing in state"),
            }

        model_id = model_data.get("model_id", "unknown")

        sections: Dict[str, str] = {
            "model_development_rationale": _section_development_rationale(model_data),
            "training_data_description": _section_training_data(model_data),
            "validation_methodology": _section_validation_methodology(model_data),
            "performance_metrics": _section_performance_metrics(model_data),
            "risk_parameters": _section_risk_parameters(model_data),
            "change_history": _section_change_history(model_data),
            "monitoring_schedule": _section_monitoring_schedule(model_data),
        }

        emit_trace_event(
            "generate_sections_complete",
            {
                "model_id": model_id,
                "section_count": len(sections),
                "section_keys": sorted(sections.keys()),
            },
            state,
        )

        return {
            "report_sections": to_json(sections),
            "status": AgentStatus.SUCCESS.value,
        }
