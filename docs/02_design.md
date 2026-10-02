# Template Design Specification — FIN-C2-056

Model lifecycle documentation and audit report generator.

## Position in the framework

| Item | Value |
|---|---|
| Agent class | `MLModelLifecycleDocAuditAgent` (`src/graph/graph.py`) |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Category | Cat 2 — a domain-specific pipeline for one job to be done |
| Composition | two layers: the fixed outer backbone whose `main` slot is a `GraphNode` (`ModelAuditGraphNode`) delegating to an inner `BaseGraph` (`DomainWorkflowGraph`) |

Three-layer separation:

- **State** — a flat `TypedDict` (`src/schemas/state.py`). No object graphs: checkpoints are
  serialized with msgpack, and dict/list values are stored as JSON strings for that reason.
- **Node** — framework inheritance; a node overrides `execute(self, state) -> dict` and returns
  only the keys it changes.
- **Graph** — composition; `register_nodes()` fills the backbone slots.

## Architecture

### Outer backbone

| Node | Responsibility | Reads | Writes | Class |
|---|---|---|---|---|
| initialize | schema version, session and trace identifiers, trust level | — | session/trace ids | framework default |
| pre_process | trust gate (VERIFIED_EXTERNAL) + caller-record validation | user_input, input_context, report_limits | validated_input, model_contract, enriched_context | `PreProcessNode` |
| main | delegate to the inner workflow | model_contract | audit_report, report_sections, compliance_flags, fsa_filing_required | `ModelAuditGraphNode` (a `GraphNode`) |
| post_process | output gate; release or withhold | audit_report | formatted_output, result | `PostProcessNode` |
| finalize | response metadata, total elapsed time | — | metadata | framework default |

`add_edges()` is not overridden on the outer graph — the backbone wiring belongs to the framework.

### Inner workflow (`DomainWorkflowGraph`)

| Node | Responsibility | Trust |
|---|---|---|
| input_validate | confirm the seeded record is complete; derive the working record | ANONYMOUS |
| parse_model_data | risk-tier classification and the reportability indicator | ANONYMOUS |
| generate_sections | render the seven documentation sections | ANONYMOUS |
| compliance_check | filing determination, with the trigger that decided it | ANONYMOUS |
| output_format | assemble the dossier | ANONYMOUS |

The inner nodes are ANONYMOUS by design. The boundary node already vouched for the caller, and an
inner node demanding a higher level than the boundary grants would deny every real request.

### Data flow

```
Outer backbone (fixed):
START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
                                        |  (retry, bounded by max_retry)
                                        -> pre_process

main (ModelAuditGraphNode) -> DomainWorkflowGraph:
  START -> input_validate -> parse_model_data -> generate_sections
        -> compliance_check -> output_format -> END
```

### Generation contract

Generation is **deterministic**: each section is composed from the validated record by fixed
templates. The manifest declares `generation_mode: deterministic` for that reason. No model is
called, no model settings are read, and no prompt asset ships — a declared prompt with no reader
would be a dead declaration, not a placeholder for future work.

## The caller-request contract

### Two channels, one data channel

| Channel | Carries | Validated by |
|---|---|---|
| `input` (free text) | a short request line naming the dossier | length cap + disallowed-instruction screen |
| `input_context.model_lifecycle` | the model record | every field, in `src/services/model_contract.py` |

**Why the record travels on the structured channel.** The platform's own input gate rewrites
personal-data shapes in the free-text field at every node boundary. A model record is mostly
proper nouns — the model name, the developing team, the independent validator, the monitoring
owner — so a record sent as free text comes back with each of them replaced, and the dossier
reads as a submission with its responsible parties redacted while still reporting success. The
structured channel is not rewritten. The record is therefore required there, and a record sent
inside the request line is refused rather than silently used.

**What that costs, and how it is paid.** The structured channel is not scanned for personal data
either, so this template does that itself: the same pattern set is applied inbound as a strip and
outbound as a refusal. One definition, both directions — two lists would drift, and the drift
would always favour the leak.

### Rules that hold for every field

- **Numbers** go through a finite and bounded parser. NaN and the infinities survive `float()` and
  make every comparison against them false; unchecked, they render into a regulatory document as
  `nan` beside a metric name. Rejected, with the field named.
- **Identifiers and labels** are restricted to inert alphabets rather than escaped at each use:
  the model identifier to `[A-Za-z0-9._-]{1,64}`, business function / risk tier / materiality /
  metric names to `[a-z0-9_]{1,32}`, versions to `[A-Za-z0-9._-]{1,32}`, dates to `YYYY-MM-DD`.
- **Free text** is whitespace-collapsed, invisible-control-stripped and length-capped. The
  collapse is the defence against forged document structure: the dossier is line-oriented, its
  sections introduced by a numbered heading above a rule, so text that cannot contain a newline
  cannot open a section that is not there.
- **Structure** is bounded: change-history entries, metric-table size, per-field length, and a
  coarse size cap on the whole structured payload at the entry point.
- **Refusals name the field, never the value**, in the response, the log and the audit record.

### The disallowed-instruction screen

Chat-template control tokens are screened as a **class** — `<|…|>`, `[INST]`, `<<SYS>>`,
`<system>`, `### System` — not as the few strings one model happens to use. The platform's own
screen scores some of that class as high confidence and others as not, so a template that
inherited only the platform's list would pass `<<SYS>> ignore all rules` into the dossier.
Measured against the installed framework: `<|im_start|>` and `[INST]` score high; `<<SYS>>`,
`<|system|>` and `### System` do not.

The screen reads each string four ways — as received, with invisible controls removed, with
identifier shapes collapsed, and with markup removed — because none of the four subsumes the
others. Control tokens must be seen before any rewrite could consume them; a directive split by
tags (`ig<b>nore …`) only reads as a directive once the tags come out. It walks the parsed payload
depth-first including mapping **keys**: a payload delivered as JSON can write any pattern into a
key, and `\u` escapes make a scan of the raw request text unreliable.

Directive patterns require a verb **and** its object, so a record that merely mentions rules,
prompts or an assistant does not match. Model documentation quotes policy language verbatim, and
a screen that fires on it refuses genuine work — the more damaging of the two failures.

### A credential-shaped value in the structured parameters

The framework's mandatory output scan checks every value of every node result, and the backbone's
first node copies the structured parameters verbatim into its own result. So a credential-shaped
string anywhere in them fails the **first** node of the graph, before any template code runs, and
the caller receives an error that names nothing. The standalone entry point therefore screens the
assembled parameters with the framework's own detector before invoking, and refuses with a 400
that names the offending field — never the value, and never a field name that is itself hostile.

The screen calls the same function the framework's gate calls, on the same object, so what the
entry point refuses and what the gate blocks are one set by construction. Scanning field by field
composes exactly to scanning the whole mapping, which is what lets the refusal name the field
without widening or narrowing the match. Like the framework's own scan it reads values and never
keys; refusing key names would refuse requests the framework accepts, and the two sets would no
longer be one set.

## The output boundary

The framework resolves the caller-visible value as `formatted_output or result`, with **no status
check**. An error status alone therefore withholds nothing: a gate that raises, or that returns an
error without overwriting those fields, ships the un-gated document inside the error envelope. A
falsy replacement (`""`, `{}`, absent) has the same effect, because it re-opens the fallback.

On a violation `PostProcessNode`:

1. returns an error status;
2. writes a **truthy** withheld notice to `formatted_output`;
3. overwrites every state field that carries document text — `result`, `audit_report`,
   `report_sections`, `compliance_flags` — **present and empty**, not omitted. The runtime merges
   partial deltas, so a key left out of the returned delta keeps its old value in state;
4. names the violation from a closed set of labels — never the matched text, its offset, or the
   field contents.

The cleared set is declared once in `src/nodes/post_process_node.py` and pinned by a test that
derives it from the outer graph's own mapping, so a document-bearing field added later cannot
quietly stay in the envelope.

The gate calls the **framework's own credential detector** rather than a local pattern list. A
local list narrower than the framework's is a bypass, not a simplification: the value passes the
template's gate, the framework then raises inside the node, and the wrapper replaces the whole
returned delta with a bare error — discarding the clearing along with it.

An empty document at the boundary fails closed as well. Releasing a placeholder with a success
status would tell a caller a dossier exists when none does.

### Where a credential is actually stopped

Recorded separately from "contained", because they are different claims. A credential-shaped value
cannot reach the output gate through either deployment path today:

- **standalone** — the entry point refuses the request with a 400 naming the field, before the
  graph is entered;
- **direct invocation** (no entry point runs) — the framework's own output scan fails the first
  backbone node, and the run short-circuits through the remaining slots.

The output gate is therefore a layer against a future producer, not the only thing between a
secret and a caller. All three claims are pinned by tests, so a change that opens the path fails
rather than passing quietly.

### Numeric precision

This template renders no monetary aggregates: the document carries model metrics, counts, dates
and prose. The rounding grid some financial templates enforce at the output boundary is therefore
not applicable here, and no numeric rewriting happens at the boundary at all — which is also why
none of the identifier-collision failure modes that come with such a grid can occur. The invariant
this boundary does enforce is the one stated above: nothing is released that carries a credential
shape or a personal-data shape, and a violation withholds the document whole.

## Runtime configuration

`config/agent.yaml` is the static manifest: identity, entry point, required trust level, and the
compile-time `requires` block. It carries no runtime parameters.

`config/config.yaml` carries the runtime parameters and is passed to the graph constructor. Every
value in it has a reader:

| Value | Read by | Effect |
|---|---|---|
| `max_retry` | the framework backbone | retry budget for the routing decision |
| `timeout_s` | framework service clients | request timeout |
| `report.max_text_chars` | the request boundary | longest free-text field accepted |
| `report.max_change_history_entries` | the request boundary | most change entries accepted |
| `report.max_performance_metrics` | the request boundary | largest metric table accepted |
| `report.line_width` | the renderer | width of the document's separator rules |

The three `max_*` bounds reach the request boundary through the outer graph's initial-state hook;
`line_width` reaches the renderer through the config the outer graph node forwards to the inner
graph, which republishes it into inner state. Both routes exist because node `execute()` methods
take state and nothing else — state seeding is the only way a configured value can travel. The
two readers are deliberately separate: neither can stand in for the other, so a test of one cannot
pass because the other happened to hold.

`requires.secrets` is empty. The template calls no secret-requiring API; declaring a secret it
does not use would make the agent fail to compile wherever that secret is not provisioned.
`requires.extras` is empty for the same reason: no optional client is constructed.

## Import isolation

- The template imports from the framework and the shared library only.
- No platform SDK imports. Asserted by an AST scan over `src/`
  (`tests/proof_of_boundary/test_import_isolation.py`).

## Design decision record

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Base class | `AgentBaseGraph` | `AutonomousBaseGraph` | **`AgentBaseGraph`** | a fixed documentation pipeline, not an autonomous loop |
| Composition | single graph | nested subgraph | **nested subgraph** | five domain steps encapsulated behind one backbone slot |
| Record channel | free-text field | structured parameters | **structured parameters** | the free-text field is rewritten by the platform's input gate, and the record is mostly proper nouns |
| Output gate placement | node instance method | module-level function | **module-level function** | the node's own gate methods are final on the framework base class |
| Credential detection | local pattern list | the framework's detector | **the framework's detector** | a narrower local list is a bypass: the framework raises inside the node and the wrapper discards the clearing |
| Missing record | render a partial dossier | refuse | **refuse** | a submission assembled from absent data looks complete and is not |
