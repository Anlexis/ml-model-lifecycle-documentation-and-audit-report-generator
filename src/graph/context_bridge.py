"""AgentCore Platform v1.0"""

# Validated-record bridge across the outer/inner graph boundary.
#
# Why it exists: the framework invokes a nested graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. Only that one string
# crosses — neither the outer state nor the caller's structured invocation
# parameters are forwarded. So the validated model record the request boundary
# produces would never reach the dossier pipeline on its own.
#
# The two sanctioned subclass hooks bridge it:
#
#   ModelAuditGraphNode.extract_input(state)     [runs BEFORE subgraph.invoke]
#       -> set_model_contract(<validated record>)
#   DomainWorkflowGraph._extra_initial_state()   [runs INSIDE subgraph.invoke]
#       -> seeds the record into the inner state
#
# What crosses is the VALIDATED record only — every value has already passed
# its bounded, inert shape check. The raw request body never travels.
#
# Smuggling the record inside the request string instead is not viable: the
# platform rewrites personal-data shapes in that field at every node boundary,
# and a model record is mostly proper nouns, so the dossier would be assembled
# from corrupted text. This channel is not rewritten.
#
# A ContextVar keeps the hand-off correct per thread and per task, so
# concurrent invocations inside one process cannot see each other's record.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_MODEL_CONTRACT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("fin_c2_056_model_contract", default=None)


def set_model_contract(contract: Optional[Dict[str, Any]]) -> None:
    """Stash the validated model record for the imminent inner-graph invoke."""
    _MODEL_CONTRACT.set(dict(contract) if contract else {})


def get_model_contract() -> Dict[str, Any]:
    """Read (without consuming) the stashed record; {} when none was set."""
    return _MODEL_CONTRACT.get() or {}
