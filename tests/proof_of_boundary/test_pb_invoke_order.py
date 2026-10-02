# PB-6: invoke execution order.
#
# Verifies that the framework's node wrapper enforces, for every node under
# src/nodes/:
#     trust gate -> node_start -> input gate -> execute() -> output gate
#     -> node_complete
# and that a full agent invoke runs the backbone in order:
#     InitializeNode -> PreProcessNode (pre_process) -> ModelAuditGraphNode
#     (main) -> PostProcessNode (post_process) -> FinalizeNode
#
# The backbone invoke runs at VERIFIED_EXTERNAL — the trust level a real
# external caller holds and the level the manifest declares. for_internal() is
# deliberately not used: it represents no caller a deployment can produce, and a
# suite that used it would be green on an agent that refuses every real request.

import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest

from tests.conftest import CANONICAL_REQUEST_LINE, context, record

# Class name of the node in the `main` backbone slot.
_MAIN_SLOT_NODE = "ModelAuditGraphNode"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _discover_node_classes() -> list[type]:
    """Import every module under src/nodes/ and collect concrete node classes."""
    from framework.nodes.base_node import BaseNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


class TestInvokeOrder:
    """The wrapper's order is fixed and cannot be bypassed by a node."""

    def test_call_order_for_every_node(self, monkeypatch):
        node_classes = _discover_node_classes()
        assert node_classes, "no concrete node classes found under src/nodes/"

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in node_classes:
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "input_gate"),
                ("execute", "execute"),
                ("_security_gate_output", "output_gate"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            # Caller trust equals the node's required level so the trust gate
            # always passes here; the denial branch is asserted separately below.
            state = {
                "caller_trust_level": node_cls.required_trust_level.value,
                "correlation_id": "invoke-order-probe",
            }
            instance(state)

            expected = [
                "event:node_start",
                "input_gate",
                "execute",
                "output_gate",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)


class TestTrustGate:
    """The trust gate runs before execute() and denies an under-trusted caller."""

    def test_the_boundary_node_denies_an_anonymous_caller(self):
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.trust_level import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        result = PreProcessNode()(
            {
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
                "user_input": CANONICAL_REQUEST_LINE,
                "input_context": context(),
                "correlation_id": "trust-gate-denial",
            }
        )
        assert result["status"] == AgentStatus.ERROR.value
        assert any("trust gate" in entry.lower() for entry in result.get("error_log", []))

    def test_the_boundary_node_admits_a_verified_caller(self):
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.trust_level import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        result = PreProcessNode()(
            {
                "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
                "user_input": CANONICAL_REQUEST_LINE,
                "input_context": context(),
                "correlation_id": "trust-gate-admit",
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["model_contract"] is not None


class TestBackboneInvokeOrder:
    """A full agent invoke runs the five backbone slots in order."""

    def _invoke(self):
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.graph import Graph, runtime_config

        agent = Graph(config=runtime_config())
        agent.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        return agent.invoke(
            CANONICAL_REQUEST_LINE,
            ctx=ctx,
            input_context=context(),
        )

    def test_the_invoke_succeeds_and_returns_the_document(self):
        from framework.schemas.agent_status import AgentStatus

        result = self._invoke()
        assert result.get("status") == AgentStatus.SUCCESS.value, result.get("error_log")
        assert result.get("output")
        assert "ML MODEL LIFECYCLE DOCUMENTATION & AUDIT REPORT" in result["output"]
        assert "FIN-MDL-20260712-001" in result["output"]

    def test_node_history_matches_the_expected_order(self):
        assert self._invoke().get("node_history", []) == [
            "InitializeNode",
            "PreProcessNode",
            "ModelAuditGraphNode",
            "PostProcessNode",
            "FinalizeNode",
        ]

    def test_the_main_slot_holds_the_nested_workflow_node(self):
        from framework.nodes.graph_node import GraphNode
        from src.graph.graph import MLModelLifecycleDocAuditAgent, ModelAuditGraphNode

        agent = MLModelLifecycleDocAuditAgent()
        agent.compile()
        main_node = agent._nodes.get("main")
        assert isinstance(main_node, ModelAuditGraphNode)
        assert isinstance(main_node, GraphNode)
        assert main_node.__class__.__name__ == _MAIN_SLOT_NODE

    def test_the_deployment_payload_is_the_payload_this_test_proves(self):
        """The deployment check posts deploy/invoke_payload.json; this asserts it
        is the same request the invoke above proves succeeds."""
        payload_file = _REPO_ROOT / "deploy" / "invoke_payload.json"
        assert payload_file.exists(), "deploy/invoke_payload.json is required"
        body = json.loads(payload_file.read_text(encoding="utf-8"))
        # `input` now carries the record as JSON, because that is the only channel
        # Marketplace chat has: the adapter hook lifts it into input_context, which the
        # S-2 gate does not mask. A published payload whose `input` is a bare sentence
        # gives a chat user `agent failed` every time. `input_context` stays for the HTTP
        # path, and the anti-drift guarantee is kept by comparing the parsed record.
        assert json.loads(body["input"]) == {"model_lifecycle": record()}
        assert body["input_context"] == {"model_lifecycle": record()}

    @pytest.mark.parametrize("field", ["model_id", "model_name", "business_function"])
    def test_the_deployment_payload_carries_every_required_field(self, field):
        body = json.loads((_REPO_ROOT / "deploy" / "invoke_payload.json").read_text(encoding="utf-8"))
        assert field in body["input_context"]["model_lifecycle"]
