# Unit tests for the workflow nodes and the graph wiring.
#
# These import the real modules and assert real behaviour: the rendered document
# content, the filing thresholds, the trust levels each node declares, and the
# two-layer graph composition.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.schemas.state import from_json, to_json
from src.services.model_contract import build_model_contract
from tests.conftest import CANONICAL_REQUEST_LINE, context, record

LINE = CANONICAL_REQUEST_LINE


def _contract(**overrides):
    return build_model_contract(LINE, context(**overrides))


def _model_data(**overrides):
    """The record shape the inner nodes consume, as InputValidateNode leaves it."""
    data = _contract(**overrides)
    data.pop("limits", None)
    return data


# ── PreProcessNode: the request boundary ─────────────────────────────────────


class TestPreProcessNode:
    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def test_a_valid_request_produces_the_validated_record(self):
        result = self.node.execute({"user_input": LINE, "input_context": context()})
        assert result["status"] == AgentStatus.SUCCESS.value
        contract = from_json(result["model_contract"])
        assert contract["model_id"] == "FIN-MDL-20260712-001"
        assert contract["model_name"] == "Retail Credit PD Scoring Model"

    def test_the_field_the_platform_rewrites_carries_only_an_inert_identifier(self):
        """The record must not travel through a field the platform masks."""
        result = self.node.execute({"user_input": LINE, "input_context": context()})
        assert result["validated_input"] == "FIN-MDL-20260712-001"
        assert "Retail Credit PD Scoring Model" not in result["validated_input"]

    def test_request_metadata_carries_the_model_identifier(self):
        result = self.node.execute({"user_input": LINE, "input_context": context()})
        meta = from_json(result["enriched_context"])
        assert meta["model_id"] == "FIN-MDL-20260712-001"
        assert meta["source"] == "MLModelLifecycleDocAuditAgent"

    def test_a_missing_record_is_refused(self):
        result = self.node.execute({"user_input": LINE, "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert "model_lifecycle" in result["error_log"][0]

    def test_an_empty_request_line_is_refused(self):
        result = self.node.execute({"user_input": "", "input_context": context()})
        assert result["status"] == AgentStatus.ERROR.value

    def test_a_refusal_names_the_field_and_not_the_value(self):
        result = self.node.execute({"user_input": LINE, "input_context": context(model_id="MDL 001 with spaces")})
        assert result["status"] == AgentStatus.ERROR.value
        assert "model_id" in result["error_log"][0]
        assert "MDL 001 with spaces" not in result["error_log"][0]

    def test_the_declared_bounds_reach_the_boundary_through_state(self):
        entries = [{"version": f"v{i}", "date": "2026-01-01", "change": "c"} for i in range(30)]
        seeded = {
            "user_input": LINE,
            "input_context": context(change_history=entries),
            "report_limits": to_json({"max_change_history_entries": 25}),
        }
        refused = self.node.execute(seeded)
        assert refused["status"] == AgentStatus.ERROR.value

        seeded["report_limits"] = to_json({"max_change_history_entries": 40})
        accepted = self.node.execute(seeded)
        assert accepted["status"] == AgentStatus.SUCCESS.value

    def test_trust_level_is_the_boundary_level(self):
        assert self.node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_execute_takes_state_and_nothing_else(self):
        import inspect

        from src.nodes.pre_process_node import PreProcessNode

        params = list(inspect.signature(PreProcessNode.execute).parameters)
        assert params == ["self", "state"]


# ── InputValidateNode ────────────────────────────────────────────────────────


class TestInputValidateNode:
    def setup_method(self):
        from src.nodes.input_validate_node import InputValidateNode

        self.node = InputValidateNode()

    def test_the_seeded_record_becomes_the_working_record(self):
        result = self.node.execute({"model_contract": to_json(_contract())})
        assert result["status"] == AgentStatus.SUCCESS.value
        data = from_json(result["model_data"])
        assert data["model_id"] == "FIN-MDL-20260712-001"
        assert data["high_risk_function"] is True

    def test_the_configuration_bounds_are_not_carried_into_the_document_record(self):
        result = self.node.execute({"model_contract": to_json(_contract())})
        assert "limits" not in from_json(result["model_data"])

    def test_no_record_reaching_the_workflow_is_an_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    @pytest.mark.parametrize("field", ["model_id", "model_name", "business_function"])
    def test_an_incomplete_record_is_an_error(self, field):
        broken = _contract()
        broken[field] = ""
        result = self.node.execute({"model_contract": to_json(broken)})
        assert result["status"] == AgentStatus.ERROR.value
        assert field in result["error_log"][0]

    def test_trust_level_matches_the_boundary_grant(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ParseModelDataNode ───────────────────────────────────────────────────────


class TestParseModelDataNode:
    def setup_method(self):
        from src.nodes.parse_model_data_node import ParseModelDataNode

        self.node = ParseModelDataNode()

    def _run(self, **risk):
        data = _model_data()
        data["risk_tier"] = risk.get("risk_tier", "high")
        data["materiality"] = risk.get("materiality", "material")
        data["high_risk_function"] = risk.get("high_risk_function", True)
        return from_json(self.node.execute({"model_data": to_json(data)})["model_data"])

    def test_a_material_high_risk_low_tier_model_is_escalated(self):
        data = self._run(risk_tier="low", materiality="material", high_risk_function=True)
        assert data["risk_tier"] == "high"
        assert data["fsa_reportable"] is True

    def test_a_high_risk_function_lifts_a_low_tier_to_medium(self):
        data = self._run(risk_tier="low", materiality="immaterial", high_risk_function=True)
        assert data["risk_tier"] == "medium"

    def test_a_declared_tier_is_respected_when_nothing_escalates_it(self):
        data = self._run(risk_tier="medium", materiality="immaterial", high_risk_function=False)
        assert data["risk_tier"] == "medium"
        assert data["fsa_reportable"] is False

    def test_a_critical_tier_is_preserved(self):
        data = self._run(risk_tier="critical", materiality="immaterial", high_risk_function=False)
        assert data["risk_tier"] == "critical"
        assert data["fsa_reportable"] is True

    def test_an_undeclared_tier_is_treated_as_medium_not_as_the_lowest(self):
        data = self._run(risk_tier="unknown", materiality="immaterial", high_risk_function=False)
        assert data["risk_tier"] == "medium"

    def test_a_missing_record_is_an_error(self):
        assert self.node.execute({})["status"] == AgentStatus.ERROR.value

    def test_trust_level_matches_the_boundary_grant(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── GenerateSectionsNode ─────────────────────────────────────────────────────

SECTION_KEYS = {
    "model_development_rationale",
    "training_data_description",
    "validation_methodology",
    "performance_metrics",
    "risk_parameters",
    "change_history",
    "monitoring_schedule",
}


class TestGenerateSectionsNode:
    def setup_method(self):
        from src.nodes.generate_sections_node import GenerateSectionsNode

        self.node = GenerateSectionsNode()

    def _sections(self, **overrides):
        result = self.node.execute({"model_data": to_json(_model_data(**overrides))})
        assert result["status"] == AgentStatus.SUCCESS.value
        return from_json(result["report_sections"])

    def test_all_seven_sections_are_rendered(self):
        assert set(self._sections()) == SECTION_KEYS

    def test_the_sections_carry_the_caller_record_not_a_placeholder(self):
        sections = self._sections()
        assert "FIN-MDL-20260712-001" in sections["model_development_rationale"]
        assert "Retail Credit PD Scoring Model" in sections["model_development_rationale"]
        assert "Independent Model Validation Unit" in sections["validation_methodology"]
        assert "Retail Credit Risk Monitoring" in sections["monitoring_schedule"]

    def test_a_different_record_produces_a_different_document(self):
        first = self._sections()
        second = self._sections(model_name="Wholesale LGD Model", model_id="FIN-MDL-999")
        assert first["model_development_rationale"] != second["model_development_rationale"]
        assert "Wholesale LGD Model" in second["model_development_rationale"]

    def test_metrics_are_rendered_by_name_and_value(self):
        sections = self._sections()
        assert "AUC: 0.87" in sections["performance_metrics"]
        assert "GINI: 0.74" in sections["performance_metrics"]

    def test_an_empty_metric_table_says_so(self):
        sections = self._sections(performance_metrics={})
        assert "No performance metrics recorded" in sections["performance_metrics"]

    def test_an_empty_change_history_says_so(self):
        sections = self._sections(change_history=[])
        assert "No change history recorded" in sections["change_history"]

    def test_an_absent_optional_field_renders_a_uniform_placeholder(self):
        data = _model_data()
        data["development"] = dict(data["development"], developed_by="")
        sections = from_json(self.node.execute({"model_data": to_json(data)})["report_sections"])
        assert "Developed By:      Not stated" in sections["model_development_rationale"]

    def test_a_missing_record_is_an_error(self):
        assert self.node.execute({})["status"] == AgentStatus.ERROR.value

    def test_trust_level_matches_the_boundary_grant(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ComplianceCheckNode ──────────────────────────────────────────────────────


class TestComplianceCheckNode:
    def setup_method(self):
        from src.nodes.compliance_check_node import ComplianceCheckNode

        self.node = ComplianceCheckNode()

    def _run(self, risk_tier, materiality, high_risk_function):
        data = _model_data()
        data["risk_tier"] = risk_tier
        data["materiality"] = materiality
        data["high_risk_function"] = high_risk_function
        return self.node.execute({"model_data": to_json(data)})

    def test_a_high_tier_alone_requires_a_filing(self):
        result = self._run("high", "immaterial", False)
        assert result["fsa_filing_required"] is True
        flags = from_json(result["compliance_flags"])
        assert flags["tier_trigger"] is True
        assert flags["materiality_trigger"] is False
        assert flags["function_trigger"] is False

    def test_materiality_alone_requires_a_filing(self):
        flags = from_json(self._run("medium", "material", False)["compliance_flags"])
        assert flags["materiality_trigger"] is True
        assert flags["fsa_filing_required"] is True

    def test_a_high_risk_function_alone_requires_a_filing(self):
        flags = from_json(self._run("medium", "immaterial", True)["compliance_flags"])
        assert flags["function_trigger"] is True
        assert flags["fsa_filing_required"] is True

    def test_none_of_the_triggers_means_no_filing(self):
        result = self._run("low", "immaterial", False)
        assert result["fsa_filing_required"] is False
        flags = from_json(result["compliance_flags"])
        assert "not a high-risk function" in flags["reason"]

    def test_the_determination_cites_its_regulatory_basis(self):
        flags = from_json(self._run("high", "material", True)["compliance_flags"])
        assert "Model Risk Management" in flags["regulatory_basis"]

    def test_a_missing_record_is_an_error(self):
        assert self.node.execute({})["status"] == AgentStatus.ERROR.value

    def test_trust_level_matches_the_boundary_grant(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── OutputFormatNode ─────────────────────────────────────────────────────────


class TestOutputFormatNode:
    def setup_method(self):
        from src.nodes.output_format_node import OutputFormatNode

        self.node = OutputFormatNode()

    def _state(self, fsa_filing_required=True, line_width=None):
        sections = {
            "model_development_rationale": "Model ID: FIN-MDL-20260712-001",
            "training_data_description": "  Description: Five years of performance.",
            "validation_methodology": "  Methodology: Out-of-time testing.",
            "performance_metrics": "  AUC: 0.87",
            "risk_parameters": "  Risk Tier: HIGH",
            "change_history": "  v1.0 (2026-03-15): Initial release.",
            "monitoring_schedule": "  Monitoring Frequency: Quarterly",
        }
        compliance = {
            "fsa_filing_required": fsa_filing_required,
            "reason": "Risk tier (high) is in ['critical', 'high']",
            "regulatory_basis": "Model Risk Management Framework",
        }
        state = {
            "report_sections": to_json(sections),
            "compliance_flags": to_json(compliance),
            "model_data": to_json(_model_data()),
        }
        if line_width is not None:
            state["report_limits"] = to_json({"line_width": line_width})
        return state

    def test_the_document_carries_every_section_heading(self):
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS.value
        report = result["audit_report"]
        for header in (
            "1. Model Development Rationale",
            "2. Training Data Description",
            "3. Validation Methodology",
            "4. Performance Metrics",
            "5. Risk Parameters",
            "6. Change History",
            "7. Ongoing Monitoring Schedule",
            "MODEL RISK MANAGEMENT COMPLIANCE NOTE",
        ):
            assert header in report, f"missing section heading: {header}"

    def test_the_document_is_not_released_here(self):
        """Only the output gate releases; this node writes the document alone."""
        result = self.node.execute(self._state())
        assert "result" not in result
        assert "formatted_output" not in result

    def test_a_required_filing_is_stated_plainly(self):
        report = self.node.execute(self._state(fsa_filing_required=True))["audit_report"]
        assert "Filing Required:       YES" in report

    def test_no_required_filing_is_stated_plainly(self):
        report = self.node.execute(self._state(fsa_filing_required=False))["audit_report"]
        assert "Filing Required:       NO" in report

    def test_the_declared_line_width_sets_the_document_rules(self):
        narrow = self.node.execute(self._state(line_width=48))["audit_report"]
        wide = self.node.execute(self._state(line_width=100))["audit_report"]
        assert "=" * 48 in narrow and "=" * 100 not in narrow
        assert "=" * 100 in wide

    def test_an_out_of_range_line_width_falls_back_to_the_default(self):
        report = self.node.execute(self._state(line_width=5))["audit_report"]
        assert "=" * 72 in report

    def test_missing_sections_are_an_error(self):
        result = self.node.execute({"model_data": to_json(_model_data())})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_matches_the_boundary_grant(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── Graph wiring ─────────────────────────────────────────────────────────────


class TestOuterGraphComposition:
    def test_all_five_backbone_slots_are_registered(self):
        from src.graph.graph import MLModelLifecycleDocAuditAgent, ModelAuditGraphNode
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode

        agent = MLModelLifecycleDocAuditAgent()
        agent.compile()
        assert set(agent._nodes) == {
            "initialize",
            "pre_process",
            "main",
            "post_process",
            "finalize",
        }
        assert isinstance(agent._nodes["pre_process"], PreProcessNode)
        assert isinstance(agent._nodes["main"], ModelAuditGraphNode)
        assert isinstance(agent._nodes["post_process"], PostProcessNode)

    def test_identity_and_state_schema(self):
        from src.graph.graph import MLModelLifecycleDocAuditAgent
        from src.schemas.state import State

        agent = MLModelLifecycleDocAuditAgent()
        assert agent.name == "MLModelLifecycleDocAuditAgent"
        assert agent.state_schema is State

    def test_the_alias_points_at_the_agent(self):
        from src.graph.graph import Graph, MLModelLifecycleDocAuditAgent

        assert Graph is MLModelLifecycleDocAuditAgent

    def test_the_declared_bounds_are_seeded_into_the_outer_initial_state(self):
        from src.graph.graph import MLModelLifecycleDocAuditAgent

        agent = MLModelLifecycleDocAuditAgent(config={"report": {"line_width": 90}})
        seeded = from_json(agent._extra_initial_state()["report_limits"])
        assert seeded["line_width"] == 90

    def test_the_runtime_config_file_is_the_source_of_the_bounds(self):
        from src.graph.graph import runtime_config

        loaded = runtime_config()
        assert loaded.get("max_retry") == 3
        assert isinstance(loaded.get("report"), dict)
        assert loaded["report"]["line_width"] == 72

    def test_the_forwarded_config_is_never_empty(self):
        from src.graph.graph import ModelAuditGraphNode

        forwarded = ModelAuditGraphNode()._parent_config()
        assert forwarded["configurable"]["report"]["line_width"] == 72

    def test_the_boundary_hands_the_inner_graph_an_inert_string(self):
        from src.graph.graph import ModelAuditGraphNode

        node = ModelAuditGraphNode()
        handed = node.extract_input({"model_contract": to_json(_contract())})
        assert handed == "FIN-MDL-20260712-001"

    def test_the_validated_record_crosses_on_the_bridge(self):
        from src.graph.context_bridge import get_model_contract
        from src.graph.graph import ModelAuditGraphNode

        node = ModelAuditGraphNode()
        node.extract_input({"model_contract": to_json(_contract())})
        assert get_model_contract()["model_name"] == "Retail Credit PD Scoring Model"

    def test_the_mapping_carries_only_the_coupled_keys(self):
        from src.graph.graph import ModelAuditGraphNode

        sub_result = {
            "audit_report": "DOCUMENT",
            "report_sections": "{}",
            "compliance_flags": "{}",
            "fsa_filing_required": True,
            "status": AgentStatus.SUCCESS.value,
            "node_history": ["ignored"],
        }
        delta = ModelAuditGraphNode().merge_output({}, sub_result)
        assert set(delta) == {
            "audit_report",
            "report_sections",
            "compliance_flags",
            "fsa_filing_required",
            "status",
        }
        assert delta["audit_report"] == "DOCUMENT"

    def test_the_mapping_does_not_release_the_document(self):
        """`result` is written by the output gate alone — one release point."""
        from src.graph.graph import ModelAuditGraphNode

        delta = ModelAuditGraphNode().merge_output({}, {"audit_report": "DOCUMENT"})
        assert "result" not in delta


class TestInnerDomainGraph:
    def test_all_five_domain_nodes_are_registered(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        graph = DomainWorkflowGraph()
        graph.register_nodes()
        assert set(graph._nodes) == {
            "input_validate",
            "parse_model_data",
            "generate_sections",
            "compliance_check",
            "output_format",
        }

    def test_identity_and_state_schema(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph
        from src.schemas.state import State

        graph = DomainWorkflowGraph()
        assert graph.name == "fin_c2_056_ml_model_lifecycle_doc_workflow"
        assert graph.state_schema is State

    def test_the_path_callable_is_annotated_with_this_graphs_own_state(self):
        """The runtime reads that annotation as the callable's input schema and
        projects away every field it does not declare."""
        import typing

        from src.graph.domain_workflow_graph import DomainWorkflowGraph
        from src.schemas.state import State

        hints = typing.get_type_hints(DomainWorkflowGraph.route)
        assert hints["state"] is State

    def test_a_malformed_forwarded_config_is_refused_at_compile_time(self):
        from framework.errors import ConfigError
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        with pytest.raises(ConfigError):
            DomainWorkflowGraph(config={"configurable": {"report": "wide"}}).compile()

    def test_the_workflow_produces_a_document_from_the_bridged_record(self):
        from framework.schemas.invocation_context import InvocationContext, TrustLevel as CtxTrust
        from src.graph.context_bridge import set_model_contract
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        set_model_contract(_contract())
        graph = DomainWorkflowGraph(config={"configurable": {"report": {"line_width": 72}}})
        graph.compile()
        result = graph.invoke(
            "FIN-MDL-20260712-001",
            ctx=InvocationContext(caller_trust_level=CtxTrust.ANONYMOUS),
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["fsa_filing_required"] is True
        assert "ML MODEL LIFECYCLE DOCUMENTATION & AUDIT REPORT" in result["audit_report"]
        assert "Retail Credit PD Scoring Model" in result["audit_report"]

    def test_the_output_shape_matches_what_the_outer_mapping_reads(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        emitted = DomainWorkflowGraph().get_output({})
        assert {
            "audit_report",
            "report_sections",
            "compliance_flags",
            "fsa_filing_required",
            "status",
        } <= set(emitted)


# ── The deployment payload and the suite agree ───────────────────────────────


def test_the_deployment_payload_is_the_canonical_record():
    """The payload the deployment check posts is the record these tests use.

    Held here rather than in the deployment script so the two cannot drift: a
    change to one fails on the other.
    """
    from pathlib import Path

    payload_file = Path(__file__).resolve().parents[2] / "deploy" / "invoke_payload.json"
    assert payload_file.exists()
    body = json.loads(payload_file.read_text(encoding="utf-8"))
    # See the note in tests/proof_of_boundary/test_pb_invoke_order.py: `input` carries the
    # record as JSON so a chat user can actually drive this agent.
    assert json.loads(body["input"]) == {"model_lifecycle": record()}
    assert body["input_context"] == {"model_lifecycle": record()}
