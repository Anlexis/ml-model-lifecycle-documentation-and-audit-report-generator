"""cli.py must hand the adapter hook to run_agent_marketplace.

The Pod entry point is `cli.py`; nothing else runs it, and `run_agent_marketplace` consults a
hook only when one is passed in. src/services/chat_context.py existing is therefore not the
same fact as the hook being wired -- that exact gap shipped once: the module was on develop,
the call was not, and the built image disagreed with the repository.

Parsed rather than grepped, so a mention in a docstring or a commented-out call cannot pass.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_MODULE = "src.services.chat_context"
HOOK_NAME = "build_input_context"


def _tree() -> ast.Module:
    return ast.parse((REPO_ROOT / "cli.py").read_text(encoding="utf-8"))


def _marketplace_call(tree: ast.Module) -> ast.Call:
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "run_agent_marketplace"
    ]
    assert len(calls) == 1, f"expected exactly one run_agent_marketplace call, found {len(calls)}"
    return calls[0]


class TestTheAdapterHookIsWired:
    def test_cli_imports_the_hook_from_the_service_module(self):
        names = {
            alias.asname or alias.name
            for node in ast.walk(_tree())
            if isinstance(node, ast.ImportFrom) and node.module == HOOK_MODULE
            for alias in node.names
        }
        assert HOOK_NAME in names, (
            f"cli.py does not import {HOOK_NAME} from {HOOK_MODULE}; the chat record would "
            f"arrive through user_input, where the S-2 gate masks it"
        )

    def test_the_hook_is_passed_to_run_agent_marketplace(self):
        call = _marketplace_call(_tree())
        bound = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        assert HOOK_NAME in bound, (
            f"run_agent_marketplace() is called without {HOOK_NAME}=; the module can sit in "
            f"src/services/ and never run, which is exactly the defect this pins"
        )
        value = bound[HOOK_NAME]
        assert isinstance(value, ast.Name) and value.id == HOOK_NAME, (
            f"{HOOK_NAME}= must be bound to the imported hook, not to " f"{ast.dump(value)[:60]}"
        )

    def test_the_agent_class_and_identity_are_still_bound(self):
        """A rewrite that adds the hook must not drop what was already there."""
        call = _marketplace_call(_tree())
        bound = {kw.arg for kw in call.keywords if kw.arg}
        assert {"agent_name", "namespace"} <= bound
        assert call.args, "the agent class is passed positionally and must stay"
