"""AgentCore Platform v1.0"""

# FIN-C2-056 — outer graph (two-layer nested Cat 2 architecture)
#
# MLModelLifecycleDocAuditAgent — model lifecycle documentation and audit
# report generator.
#
# Architecture:
#
#   Outer backbone (fixed — do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
#                                             |  (RETRY, bounded by max_retry)
#                                             -> pre_process
#
#   The `main` slot is a GraphNode subclass (ModelAuditGraphNode) that delegates
#   the whole domain workflow to DomainWorkflowGraph (inner graph:
#   input_validate -> parse_model_data -> generate_sections -> compliance_check
#   -> output_format).
#
#   Domain complexity is fully encapsulated inside the inner graph; the outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step topology)
#   src/graph/context_bridge.py        <- validated record across the boundary
#
# Class-name contract:
#   graph.py class:           MLModelLifecycleDocAuditAgent (this file)
#   config/agent.yaml class:  "src.graph.graph.MLModelLifecycleDocAuditAgent"
#   src/api/server.py import: from src.graph.graph import MLModelLifecycleDocAuditAgent
#
# Rules enforced:
#   - MLModelLifecycleDocAuditAgent inherits AgentBaseGraph (direct framework inheritance)
#   - super().register_nodes() called first (fills initialize + finalize)
#   - ModelAuditGraphNode assigned to self._nodes["main"]
#   - _parent_config() forwards the LIVE runtime config (never {})
#   - merge_output() returns only changed keys
#   - add_edges() NOT overridden on the outer graph
#   - No platform SDK imports

from pathlib import Path
from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_state import AgentState
from framework.utils.config_loader import load_agent_config
from src.graph.context_bridge import set_model_contract
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json, to_json
from src.services.model_contract import resolve_limits

# Repo root: src/graph/graph.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]


def runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml — the live runtime parameters.

    The registry loads this file and passes it to the graph constructor; the
    standalone HTTP entry point does the same, so max_retry and the report
    bounds are live in both deployments rather than declared and ignored.

    Reading the static manifest (config/agent.yaml) here instead would return
    nothing: the manifest carries identity and compile-time requirements only,
    and a reader pointed at it degrades silently to defaults.
    """
    loaded = load_agent_config(_REPO_ROOT)
    return dict(loaded) if isinstance(loaded, dict) else {}


class ModelAuditGraphNode(GraphNode):
    """The `main` slot: wraps the inner model-lifecycle dossier workflow.

    Contracts:
      get_subgraph()    - instantiate DomainWorkflowGraph with the forwarded
                          runtime config (_parent_config())
      extract_input()   - hand the request line to the inner graph and stash the
                          validated model record on the bridge
      merge_output()    - map sub_result fields into the outer state delta
      error_strategy    - "propagate": re-raise inner errors (fail fast)
    """

    # S-1 declared on the wrapper too: the CI gate only AST-scans FunctionNode
    # subclasses, so a GraphNode main slot passes the pipeline without one and is
    # flagged at review. Same level the nodes in this repo already declare.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # "propagate": re-raise inner graph exceptions as SubgraphError (fail fast).
    # "handle": call on_subgraph_error() instead — for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: this workflow has no cross-boundary human-review interrupt.
    propagate_hitl: ClassVar[bool] = False

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the live report bounds to the inner graph.

        Returns the bounds under config["configurable"] — never an empty dict.
        The inner graph republishes them into inner state
        (DomainWorkflowGraph._extra_initial_state()) so the section and format
        nodes read the declared values: node execute() methods take no config
        parameter, so state seeding is the only route config can travel.
        """
        return {"configurable": {"report": resolve_limits(runtime_config().get("report"))}}

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner domain workflow graph.

        Imported inside the method to avoid circular-import risk at module load
        time. The inner graph receives the runtime-derived config through its
        constructor; its domain nodes still take no constructor arguments and
        read the bounds per call from seeded state.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the inner graph's input string, and bridge the validated record.

        The framework hands only a string to the inner graph, so the structured
        part of the request travels on the bridge instead — set here, one step
        before the inner invoke, and read by the inner graph's initial-state
        hook. Only the record the request boundary already validated crosses.

        The string itself is the validated model identifier: inert, bounded, and
        safe to carry through a field the platform rewrites.
        """
        contract = from_json(state.get("model_contract"), {}) or {}
        set_model_contract(contract)
        return str(contract.get("model_id") or state.get("validated_input") or "")

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner result back into the outer state delta (changed keys only).

        Key coupling, designed together with DomainWorkflowGraph.get_output():

          Inner get_output() emits  -> "audit_report", "report_sections",
                                       "compliance_flags", "fsa_filing_required",
                                       "status"
          This merge_output() reads -> the same five keys

        `result` is deliberately NOT set here. The dossier reaches the caller
        only through the post_process slot, after the output gate has run, so
        there is exactly one release point rather than two.
        """
        return {
            "audit_report": sub_result.get("audit_report"),
            "report_sections": sub_result.get("report_sections"),
            "compliance_flags": sub_result.get("compliance_flags"),
            "fsa_filing_required": sub_result.get("fsa_filing_required", False),
            "status": sub_result.get("status"),
        }


class MLModelLifecycleDocAuditAgent(AgentBaseGraph):
    """Outer graph for FIN-C2-056.

    Inherits AgentBaseGraph directly. Domain logic is fully encapsulated in
    ModelAuditGraphNode (main slot), which delegates to DomainWorkflowGraph.

    Backbone (fixed):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the ONLY topology override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process:  PreProcessNode (trust gate + caller-record validation)
      - main:         ModelAuditGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode (output gate)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the agent registry."""
        return "MLModelLifecycleDocAuditAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default initialize node (schema version, session id, trust
        level) and finalize node (response metadata, total elapsed time).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = ModelAuditGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the declared report bounds into the outer initial state.

        Node execute() methods receive state and nothing else, so this hook is
        how a value declared in config/config.yaml reaches the request boundary.
        Without it the bounds in the config file would be documentation: the
        node would fall back to its own defaults and the declared numbers would
        never change anything.
        """
        return {"report_limits": to_json(resolve_limits(self.config.get("report")))}


# Back-compat alias — config/agent.yaml names the class by dotted path and
# src/api/server.py imports it directly. Keep both names pointing at the agent.
Graph = MLModelLifecycleDocAuditAgent
