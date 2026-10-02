# FIN-C2-056 — MLModelLifecycleDocAuditAgent

> **Category**: Cat 2 (domain-specific documentation pipeline)
> **Industry**: FIN

## Overview

Turns a structured machine-learning model record into a model risk management dossier: a
fixed-format document covering the model's development rationale, training data, validation
methodology, performance metrics, risk parameters, change history and ongoing monitoring
schedule, in the order a supervisory review reads them.

Alongside the document it returns a filing determination — whether the model requires a
model-risk filing or inspection dossier — with the trigger that decided it named explicitly:
the risk tier, the materiality classification, or the business function. The determination is a
prompt for the model-risk function to act on, not a legal conclusion, which is why it always
shows its reasoning rather than only its verdict.

The generation is deterministic. The same record always produces the same document, so two
revisions of a model can be diffed against each other, and nothing in the dossier is invented.

Every caller field is bounds-checked before anything is rendered: numbers must be finite and in
range, identifiers and labels are restricted to inert alphabets, free text is length-capped and
whitespace-collapsed, and personal-data shapes are stripped on the way in and refused on the way
out. A document that fails the output check is withheld whole rather than trimmed.

Typical users are model-risk and validation teams who assemble a dossier per model per review
cycle by hand, and want the format fixed and the missing evidence visible before a review rather
than during one.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >= 3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling it

The model record travels on the structured invocation parameters, under `model_lifecycle`; the
free-text field carries a short request line only:

```json
{
  "input": "Model lifecycle dossier for FIN-MDL-20260712-001",
  "input_context": {
    "model_lifecycle": {
      "model_id": "FIN-MDL-20260712-001",
      "model_name": "Retail Credit PD Scoring Model",
      "model_type": "gbm",
      "business_function": "credit_scoring",
      "risk_parameters": {"risk_tier": "high", "materiality": "material"}
    }
  }
}
```

`deploy/invoke_payload.json` holds a complete example. The record must not be sent inside the
free-text field: the platform rewrites personal-data shapes there at every node boundary, and a
model record is mostly proper nouns — the model name, the developing team, the validator, the
monitoring owner — so a dossier built from that field would name none of them while still
reporting success. The agent refuses that shape rather than accepting it silently.

## Project Structure

```
src/nodes/       the five workflow nodes: validate, classify, render, determine, assemble
src/graph/       the outer graph, the inner workflow, and the record bridge between them
src/services/    the caller-request contract: screens, bounds, and the identifier strip
src/schemas/     the shared state definition
src/api/         the standalone HTTP entry point
tests/           unit tests, boundary tests, and end-to-end tests through the HTTP entry point
config/          the manifest and the runtime parameters
docs/            design and test documentation
```

`docs/02_design.md` describes the architecture and the security boundaries;
`docs/03_test_spec.md` maps every shipped test to what it proves.

## Customising

1. Tune `config/config.yaml`. `report.max_text_chars`, `report.max_change_history_entries` and
   `report.max_performance_metrics` decide what a caller may send — a record beyond any of them
   is refused, naming the field. `report.line_width` fixes the width of the rules that separate
   the document's parts.
2. Adjust the filing criteria in `src/nodes/compliance_check_node.py` for your own framework, and
   the high-risk business functions in `src/services/model_contract.py` alongside them.
3. Change the document's sections and their order in `src/nodes/generate_sections_node.py` and
   `src/nodes/output_format_node.py`.
4. Adjust the personal-data shapes in `src/services/model_contract.py` for your jurisdiction —
   they drive both the inbound strip and the outbound gate.
5. Re-run the test suite. The tests are written against behaviour, not wording, so they should
   keep passing across those changes.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
