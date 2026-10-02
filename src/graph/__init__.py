"""AgentCore Platform v1.0"""

# AgentRegistry discoverability — config/agent.yaml declares
#   module: "src.graph"
#   class:  "MLModelLifecycleDocAuditAgent"
# so the registry resolves the agent via
#   getattr(import_module("src.graph"), "MLModelLifecycleDocAuditAgent").
# Re-export the agent class (and its `Graph` alias) at the package level so that
# lazy auto-discovery resolves it without importing the graph submodule directly.

from .graph import Graph, MLModelLifecycleDocAuditAgent

__all__ = ["MLModelLifecycleDocAuditAgent", "Graph"]
