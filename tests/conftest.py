# Shared fixtures for the FIN-C2-056 suite.
#
# CANONICAL_RECORD is the one model record the whole suite works from, and it is
# the same record deploy/invoke_payload.json carries — the boundary test asserts
# that equality, so the deployment payload and the tests cannot drift apart.
#
# The audit sink is patched at the node module level rather than through a
# sys.modules stub: stubbing the module would break the real shared package the
# framework imports at load time.

import copy
from typing import Any, Dict

import pytest

CANONICAL_REQUEST_LINE = "Model lifecycle dossier for FIN-MDL-20260712-001"
CANONICAL_SESSION_ID = "signoff-fin-c2-056-001"

CANONICAL_RECORD: Dict[str, Any] = {
    "model_id": "FIN-MDL-20260712-001",
    "model_name": "Retail Credit PD Scoring Model",
    "model_type": "gbm",
    "business_function": "credit_scoring",
    "development": {
        "rationale": (
            "Replace the legacy application scorecard with a gradient-boosting "
            "probability-of-default model to improve rank-ordering of retail "
            "obligors and align with the internal ratings-based approach."
        ),
        "developed_by": "Model Development Team, Retail Credit Risk",
        "development_date": "2026-03-15",
    },
    "training_data": {
        "description": "Five years of retail loan performance with 24-month outcome windows.",
        "period": "2021-01 to 2025-12",
        "record_count": 1840000,
        "features": 42,
        "source": "Retail loan data warehouse",
    },
    "validation": {
        "methodology": (
            "Out-of-time and out-of-sample discrimination and calibration " "testing with independent replication."
        ),
        "validated_by": "Independent Model Validation Unit",
        "validation_date": "2026-04-20",
        "independent": True,
    },
    "performance_metrics": {"auc": 0.87, "ks": 0.52, "gini": 0.74},
    "risk_parameters": {
        "risk_tier": "high",
        "materiality": "material",
        "pd_range": "0.1 percent to 25 percent",
        "risk_appetite": "Within the board-approved retail credit risk appetite.",
    },
    "change_history": [
        {"version": "v1.0", "date": "2026-03-15", "change": "Initial production release."},
        {
            "version": "v1.1",
            "date": "2026-05-02",
            "change": "Recalibrated PD to the latest central tendency.",
        },
    ],
    "monitoring": {
        "schedule": "Quarterly",
        "next_review": "2026-10-01",
        "owner": "Retail Credit Risk Monitoring",
        "threshold": "A quarterly AUC decline greater than 0.03 triggers escalation.",
    },
}

# Every node module that emits domain audit events.
NODE_MODULES = (
    "pre_process_node",
    "input_validate_node",
    "parse_model_data_node",
    "generate_sections_node",
    "compliance_check_node",
    "output_format_node",
    "post_process_node",
)


def record(**overrides: Any) -> Dict[str, Any]:
    """A deep copy of the canonical record, with top-level overrides applied."""
    data = copy.deepcopy(CANONICAL_RECORD)
    data.update(copy.deepcopy(overrides))
    return data


def context(**overrides: Any) -> Dict[str, Any]:
    """The structured invocation parameters carrying the canonical record."""
    return {"model_lifecycle": record(**overrides)}


@pytest.fixture(autouse=True)
def quiet_audit_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every node's audit emitter at a no-op for the duration of a test."""
    for module in NODE_MODULES:
        monkeypatch.setattr(
            f"src.nodes.{module}.emit_trace_event",
            lambda *args, **kwargs: None,
            raising=False,
        )
