# Test Specification — FIN-C2-056

## 1. Strategy

The suite answers three questions, in this order of importance:

1. **Does the deployed agent serve a request?** A suite that only calls `execute()` directly, or
   that hand-builds a state with the trust level pre-set, can be entirely green on an agent that
   refuses every real call. So the end-to-end tests drive the real HTTP application object, at the
   trust level the manifest declares, through the same bearer-token boundary a deployment uses.
2. **Does the output depend on the input?** Every rendered value is traced back to the caller's
   record, and two different records are asserted to produce two different documents.
3. **Is every caller field bounded, and is a refused document actually withheld?**

Assertions are behavioural: a status, a refusal, an absence from the envelope — never the wording
of a framework message, which changes between versions.

### Files

| File | Scope |
|---|---|
| `tests/conftest.py` | the canonical model record the whole suite works from |
| `tests/unit/test_caller_contract.py` | what the request contract accepts and refuses |
| `tests/unit/test_nodes.py` | each workflow node, and the two-layer graph wiring |
| `tests/unit/test_post_process_node.py` | the output gate: release, withhold, and the cleared set |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | the framework's final gate methods cannot be overridden |
| `tests/integration/test_invoke_e2e.py` | end to end through the HTTP entry point |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | node wrapper order, trust gate, backbone order |
| `tests/proof_of_boundary/test_import_isolation.py` | AST scan: no platform SDK imports |
| `tests/proof_of_boundary/test_state_safety.py` | AST scan: state is serialization-safe, no credential fields |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | human-review propagation: skip stub, no cross-boundary interrupt |

### The canonical record

`tests/conftest.py` holds one model record, and `deploy/invoke_payload.json` carries the same
record inside the request shape the entry point accepts. Two tests assert that equality — one in
the unit suite, one in the boundary suite — so the deployment payload and the suite cannot drift
apart: a change to either fails on the other.

That record is a high-risk one: a credit-scoring model at risk tier `high` and materiality
`material`, so all three filing triggers fire.

## 2. Framework compliance

| ID | Test | Expected | Where |
|---|---|---|---|
| TC-01 | state is a flat `TypedDict`, no object graphs | AST scan: 0 violations | `test_state_safety.py` |
| TC-02 | an invalid or absent record is refused at the boundary | error status, field named | `TestPreProcessNode`, `TestRecordChannel` |
| TC-03 | no credential-named field in state | AST scan + entry-point screen | `test_state_safety.py`, `TestCredentialShapedContext` |
| TC-04 | `execute(self, state)` and nothing else | signature asserted | `test_execute_takes_state_and_nothing_else` |
| TC-05 | every node emits a domain audit event | gate scan over `src/` | `scripts/check_audit_trace.py` |
| TC-06/07 | the framework's final gate methods cannot be overridden | `TypeError` at class definition | `test_framework_compliance_tc06_tc07.py` |
| TC-08 | the trust gate runs before `execute()` | ANONYMOUS refused, VERIFIED_EXTERNAL admitted | `TestTrustGate` |
| TC-08a | boundary node VERIFIED_EXTERNAL, all others ANONYMOUS | per-node assertion | `test_trust_level_*` |
| TC-11 | the output gate withholds and clears | error status, fields present and empty | `TestWithholding` |

## 3. Boundary tests

| ID | Boundary | Expected | Where |
|---|---|---|---|
| PB-2 | state serialization | primitives and JSON strings only | `test_state_safety.py` |
| PB-4 | import isolation | 0 platform SDK imports under `src/` | `test_import_isolation.py` |
| PB-5 | checkpoint safety | no credential-named fields, no prohibited types | `test_state_safety.py` |
| PB-6 | node wrapper order | trust gate -> node_start -> input gate -> execute -> output gate -> node_complete, for every node | `TestInvokeOrder` |
| PB-6b | backbone order | `[Initialize, PreProcess, ModelAuditGraphNode, PostProcess, Finalize]` | `TestBackboneInvokeOrder` |
| PB-6c | a real external caller | VERIFIED_EXTERNAL end to end, never an internal context | `TestBackboneInvokeOrder`, `TestTheDeployedAgentWorks` |
| PB-6d | payload alignment | `deploy/invoke_payload.json` is the canonical record | two tests, unit and boundary |
| PB-7 | human-review propagation | skipped: no cross-boundary interrupt in this workflow | `test_pb7_hitl_interrupt_propagation.py` |

## 4. End to end, through the HTTP entry point

| Case | Expected |
|---|---|
| verified caller, canonical record | 200, success, the assembled dossier |
| no bearer token | 401 |
| the deployment payload | 200, success |
| every caller value present in the document | model id, name, developing team, validator, monitoring owner, rationale text — and no `[MASKED]` |
| two different records | two different documents |
| a low-risk record | `Filing Required: NO`; the canonical record gives `YES` |
| `report.line_width` changed | the document's separator rules change width |
| `report.max_change_history_entries` raised | a record refused at the declared cap is accepted at the raised one |
| a control token in any record field | refused, and never rendered |
| a control token in the request line | refused |
| a non-finite metric (`NaN`, `Infinity`, `-Infinity`) | refused; no `nan` in the output |
| free text beyond the declared cap | refused |
| a structured payload beyond the size cap | 413 at the entry point |
| an absent record | refused, naming `input_context.model_lifecycle` |
| a credential-shaped value in the record | 400 naming the field, value not echoed |
| a credential-shaped field NAME with an inert value | accepted — consistent with the framework's own scan, which reads values and never keys |
| ordinary domain text mentioning a bearer credential | accepted |

## 5. Business logic

| ID | Case | Expected |
|---|---|---|
| BL-01 | happy path | seven sections plus the compliance note, in order |
| BL-02 | model-type normalisation | `xgboost` renders as `Gradient Boosting (XGBoost)`; an unknown key is title-cased, never passed through raw |
| BL-03 | high-risk business function | `credit_scoring` flags true, `marketing_analytics` false |
| BL-04 | risk-tier escalation | material + high-risk + `low` escalates to `high`; high-risk + `low` alone to `medium`; an undeclared tier is treated as `medium`, not as the lowest |
| BL-05..07 | each filing trigger alone | tier, materiality, or business function each suffices, and the determination names which fired |
| BL-08 | no trigger | no filing required, with the reason stated |
| BL-09 | document assembly | seven headings + the compliance note; the filing line reflects the determination |
| BL-10 | empty change history / empty metric table | an explicit statement, not a blank section |
| BL-11 | absent optional field | a uniform `Not stated` placeholder — a reader must be able to tell "not supplied" from "empty" |
| BL-12 | graph key coupling | the inner output shape and the outer mapping carry the same five keys; the mapping does not release the document |

## 6. Negative and boundary cases

| Case | Where refused | Expected |
|---|---|---|
| empty request line | boundary | error, field named |
| request line beyond the cap | boundary | error |
| record inside the request line | boundary | error — the structured channel is required |
| record is not an object | boundary | error |
| any required field empty | boundary | error naming that field |
| identifier outside its alphabet | boundary | error naming the field, value not echoed |
| unknown risk tier | boundary | error listing the accepted values |
| metric name outside its alphabet | boundary | error |
| non-finite or over-magnitude number | boundary | error naming the field |
| a boolean or non-numeric string where a number is required | boundary | error |
| deeply nested structured parameters | boundary | error |
| personal data in record text | boundary | stripped inbound, refused outbound |
| a document carrying any framework credential pattern | output gate | withheld, fields cleared |
| an empty document | output gate | withheld — no placeholder at success |

## 7. Containment

The containment tests inject the fault on the **data path**, in the renderer that produces the
document — never in the gate. Patching the gate would test the patch.

The injected value is a personal-data shape rather than a credential shape, and the choice is
forced by where the framework's own scans sit: the framework fails the *producing* node on a
credential, so a credential injected into the renderer never reaches this template's gate at all.
Personal data is the class the framework does not scan for and this template's gate does.

| Assertion | Why |
|---|---|
| the clean path still produces the real document | the control — without it a refuse-everything gate passes everything below |
| the refusal happens at the output gate | `PostProcessNode` in `node_history`; a refusal upstream would make the rest pass for the wrong reason |
| the injected value never reaches the caller | the leak itself |
| no document text in the error envelope | the envelope's fallback resolves to `result`, so clearing must be real |
| the notice is truthy | a falsy replacement re-opens that fallback |
| no traceback, no source path in the envelope | error reasons are closed-set labels |
| the gate is not narrower than the framework's detector | a narrower gate makes the framework raise inside the node, and the wrapper discards the clearing |

## 8. Execution

- `pytest tests/` — the whole suite. No platform connection is required.
- Verified against the released framework wheel, not a stub: a stub pass is not a pass.
- `PB-7` is a deliberate skip: this workflow has no cross-boundary human-review interrupt.
