"""AgentCore Platform v1.0"""

# FIN-C2-056 — DomainWorkflowGraph (inner graph)
#
# The inner half of the two-layer Cat 2 architecture. It encapsulates the
# model-lifecycle documentation and audit pipeline:
#
#   START
#     -> input_validate      (InputValidateNode)
#     -> parse_model_data    (ParseModelDataNode)
#     -> generate_sections   (GenerateSectionsNode)
#     -> compliance_check    (ComplianceCheckNode)
#     -> output_format       (OutputFormatNode)
#     -> END
#
# Called by ModelAuditGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   - Inherits BaseGraph (fully custom topology — no forced backbone)
#   - Implements all 7 BaseGraph abstract methods
#   - register_nodes() does NOT call super() (abstract in BaseGraph)
#   - Does NOT register initialize / finalize (outer backbone concerns)
#   - All inner nodes declare required_trust_level = TrustLevel.ANONYMOUS, so a
#     caller vouched for at the boundary passes through without a second gate
#   - get_output() designed together with ModelAuditGraphNode.merge_output()
#   - All inner node constructors are empty-parens (no constructor arguments)
#   - No platform SDK imports

from typing import Any, Dict

from langgraph.graph import END, START

from framework.errors import ConfigError
from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_model_contract
from src.nodes.compliance_check_node import ComplianceCheckNode
from src.nodes.generate_sections_node import GenerateSectionsNode
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.parse_model_data_node import ParseModelDataNode
from src.schemas.state import State, to_json
from src.services.model_contract import resolve_limits


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for FIN-C2-056.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by ModelAuditGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          -> input_validate     (InputValidateNode)
          -> parse_model_data   (ParseModelDataNode)
          -> generate_sections  (GenerateSectionsNode)
          -> compliance_check   (ComplianceCheckNode)
          -> output_format      (OutputFormatNode)
          -> END

    All nodes are FunctionNode subclasses with ANONYMOUS trust.
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "fin_c2_056_ml_model_lifecycle_doc_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Reject a malformed forwarded config before the graph is built.

        The outer graph forwards the report bounds under `configurable`. A
        non-mapping there means the forwarding contract has drifted, and the
        pipeline would silently fall back to defaults — so it fails here
        instead, at compile time, where the cause is still visible.
        """
        configurable = self.config.get("configurable", {})
        if not isinstance(configurable, dict):
            raise ConfigError(
                f"[{self.__class__.__name__}] 'configurable' must be a mapping, got: {type(configurable).__name__}"
            )
        report = configurable.get("report")
        if report is not None and not isinstance(report, dict):
            raise ConfigError(
                f"[{self.__class__.__name__}] 'configurable.report' must be a mapping, " f"got: {type(report).__name__}"
            )

    # ── Initial state ─────────────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the validated record and the declared bounds into inner state.

        Two things have to cross into this graph and neither travels on its own:

          * the validated model record, because the framework hands a nested
            graph one string and nothing else. It arrives on the caller bridge,
            set by the outer graph node one step before this invoke;
          * the report bounds declared in config/config.yaml, because node
            execute() methods take no config parameter. They arrive through the
            constructor config the outer graph forwards.
        """
        configurable = self.config.get("configurable", {})
        report = configurable.get("report") if isinstance(configurable, dict) else None
        return {
            "model_contract": to_json(get_model_contract()),
            "report_limits": to_json(resolve_limits(report)),
        }

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().
        """
        self._nodes["input_validate"] = InputValidateNode()
        self._nodes["parse_model_data"] = ParseModelDataNode()
        self._nodes["generate_sections"] = GenerateSectionsNode()
        self._nodes["compliance_check"] = ComplianceCheckNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear dossier topology.

        input_validate -> parse_model_data -> generate_sections
        -> compliance_check -> output_format -> END.

        No conditional branching: every dossier goes through the same five
        steps. route() satisfies the abstract contract but is not wired.
        """
        self._sg.add_edge(START, "input_validate")
        self._sg.add_edge("input_validate", "parse_model_data")
        self._sg.add_edge("parse_model_data", "generate_sections")
        self._sg.add_edge("generate_sections", "compliance_check")
        self._sg.add_edge("compliance_check", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Conditional routing — required by the BaseGraph abstract contract.

        The topology is linear and add_conditional_edges() is not used, so this
        method is not called at runtime. It is annotated with this graph's own
        State: the graph runtime reads a path callable's annotation as its input
        schema and projects away every field the annotation does not declare, so
        an AgentState annotation here would hide the domain fields from any
        future branch added on top of it.

        Returns END on error so an unexpected invocation cannot re-enter a
        processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_format"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: State) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        Received by ModelAuditGraphNode.merge_output() in graph.py. Both methods
        are designed together so the field names cannot drift apart:

            Inner get_output() emits:   "audit_report", "report_sections",
                                        "compliance_flags",
                                        "fsa_filing_required", "status"
            Outer merge_output() reads: the same five keys
        """
        return {
            "audit_report": state.get("audit_report"),
            "report_sections": state.get("report_sections"),
            "compliance_flags": state.get("compliance_flags"),
            "fsa_filing_required": state.get("fsa_filing_required", False),
            "status": state.get("status"),
            "node_history": state.get("node_history", []),
            "correlation_id": state.get("correlation_id"),
        }
