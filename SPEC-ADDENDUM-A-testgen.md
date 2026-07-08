# SPEC Addendum A — Test Case Generation

Companion to `SPEC.md`. Same architectural rules apply (API-first, `llm_complete` dispatch,
structured LLM I/O with schema validation, Ollama-local by default, no invented facts). This
addendum adds one capability group: **generating test cases from requirements**. Claude Code:
do not start implementing this before Phases 0–3 of `SPEC.md` are done; it depends on the
retrieval layer and the structured-output contract module.

## A.1 Capability

For a given requirement (or a batch), the engine generates a test case with, at minimum:

- **Preconditions** — the state the system/test bench must be in before execution.
- **Test steps** — ordered, executable actions with expected observations where applicable.
- **Acceptance criteria** — measurable pass/fail conditions.

The defining constraint: **preconditions and acceptance criteria must be project-specific, never
generic.** "Power on the device" or "the output shall be correct" are failures. "Supply the ECU
with 12 V ± 0.1 V via bench PSU, KL15 active, CAN bus in operational state" is the bar. Concrete
values, modes, signals, and tolerances must come from the requirement set itself, from the
project test context (A.2), or from the user — never invented silently.

## A.2 Project test context (whole-set analysis + elicitation)

Test cases cannot be tailored one requirement at a time in isolation. Before any generation, the
engine builds a **project test context** in two steps:

**Step 1 — Automatic analysis of the whole requirement set.** The engine scans the full set (plus
Tier-2 reference KBs from `SPEC.md` §2.1) and extracts what it can infer: operating modes and
states, voltage/current/timing parameters and their tolerances, interfaces and buses, environmental
conditions, naming conventions for signals and components, and the requirement categories present.
The result is a draft context document with each item tagged by the source requirement IDs it was
derived from.

**Step 2 — Targeted elicitation.** The engine then asks the user only what it could *not* infer
but knows it will need — as a finite, structured questionnaire, not open-ended chat. Typical items:
available test equipment and environment (HIL rig, bench, vehicle), default supply conditions,
how modes are entered/exited if not specified, applicable test standards (e.g. ISO 16750 class),
sampling/measurement tolerances the lab uses. Each question states *why* it is being asked (which
requirements need it).

The merged result is persisted as the project test context, versioned, and reviewable/editable by
the user through the API. Every generated test case records which context version it was built
against, so regenerating after a context change is deterministic and traceable.

## A.3 Category-aware generation

The requirement's category drives the test-case shape. The engine uses the category stored with
the requirement if present; otherwise it classifies first (the existing `classify/set` capability)
and marks the classification as engine-assigned.

Minimum category → strategy mapping (extend per project via the test context):

| Category | Drives |
|---|---|
| Functional | Stimulus/response steps; nominal + at least the boundary cases implied by the requirement wording |
| Performance / timing | Measurement setup, sampling method, tolerance handling in acceptance criteria; repetition count |
| Electrical | Supply conditions, load definitions, measurement points, ISO 16750-style profiles if in context |
| Interface / communication | Bus state preconditions, message/signal-level steps, timing and error-frame criteria |
| Safety-related | Fault-injection steps, safe-state verification, reaction-time criteria; flag for mandatory human review |
| Environmental / mechanical | Chamber/rig profile as precondition, dwell times, before/after functional checks |
| Non-testable-by-test (design constraints, process) | Do **not** force a test: propose the appropriate verification method instead (inspection, analysis, review) and generate the checklist for it |

The last row is a rule, not an option: if the correct verification method for a requirement is not
"test", the engine must say so rather than produce a fake test case.

## A.4 Sufficiency judgment (the gate)

Generation is a two-verdict pipeline per requirement. Nothing is emitted as a finished test case
without passing the gate.

**Verdict 1 — before generating.** Given the requirement, its category, and the project test
context, the engine judges: `sufficient` / `insufficient`. If insufficient, it does not generate a
degraded test case. Instead it returns, per requirement, the list of **specific missing items**
("degraded-mode entry condition not defined anywhere in the set or context"; "tolerance for the
5 A nominal current not specified") and, for each item, two resolution paths the user chooses
between:

1. **Answer** — the user provides the missing information (it is then written back into the
   project test context, benefiting all future generations), or
2. **Authorize fill-in** — the user grants permission for the engine to fill that specific gap
   from domain know-how. Authorization is per-gap or per-batch, never implicit.

**Verdict 2 — after generating.** A separate self-review pass checks the produced test case
against the anti-genericity bar (A.1): every precondition and acceptance criterion must be either
traceable to a requirement ID, traceable to a context item, or explicitly marked as an authorized
assumption. Anything failing this check sends the test case back with the gap surfaced, same
resolution paths as Verdict 1.

**Assumption marking.** Every fill-in the engine was authorized to make appears in the output as
`assumptions: [{text, rationale, confidence}]` attached to the test case, and inline markers in the
affected precondition/criterion. The consuming tool must be able to render "this value is the
engine's assumption, not a spec value" unambiguously. An unmarked assumption is a defect.

## A.5 Interaction model & API

The elicitation and gap-resolution loops make this the first *conversational* capability in the
engine, but the API stays request/response and stateless-per-call: state lives in stored objects
(context, generation session), not in a chat transcript. This keeps it embeddable in the host tool,
which will render the questions in its own UI.

```
POST /api/v1/testgen/context/analyze      # run whole-set analysis → draft context + open questions
GET  /api/v1/testgen/context              # current context (versioned)
PATCH /api/v1/testgen/context             # user answers / edits context items
GET  /api/v1/testgen/context/questions    # outstanding elicitation questions, each with reasons

POST /api/v1/testgen/generate             # {requirement_ids | all, options} → per requirement:
                                          #   {status: generated | needs_input,
                                          #    test_case?, gaps?:[{item, why, resolution_options}]}
POST /api/v1/testgen/resolve              # {gap_id, resolution: answer{...} | authorize_fill}
                                          #   → regenerates affected test cases
GET  /api/v1/testgen/testcases            # list; each carries requirement_id, category,
                                          #   context_version, assumptions[], review_flags[]
```

Batch behavior: `generate` over a set returns a mixed result — finished test cases for sufficient
requirements, gap lists for the rest — in one response. It never blocks the whole batch on one
requirement's missing input.

Output format: JSON as above is canonical; add export to the host tool's format once known
(candidate: CSV and a ReqIF-adjacent structure — same open decision as `SPEC.md` §7 imports).

## A.6 Placement in the migration plan

This becomes **Phase 6** (after `SPEC.md` Phase 5), with an internal order of: context
analysis + elicitation store → category strategies → generation with Verdict 1 → self-review
Verdict 2 + assumption marking → resolve/regenerate loop.

## A.7 Testing & quality bar

Fixture-driven, mock `llm_complete`, as in `SPEC.md` §6. Specific assertions this addendum adds:

- **Anti-genericity check** is itself testable: a lint pass over generated preconditions/criteria
  flagging banned generic phrasings and criteria without a measurable quantity or observable state;
  fixtures include requirements engineered to tempt generic output.
- **Gate honesty:** fixtures with deliberately underspecified requirements must yield
  `needs_input` with the correct missing items — never a generated test case.
- **Traceability:** every precondition/criterion in fixture outputs resolves to a requirement ID,
  context item, or marked assumption; zero unmarked assumptions.
- **Method routing:** design-constraint fixtures must route to inspection/analysis, not test.

## A.8 Open decisions (ask before assuming)

- Test case ID scheme and linkage model expected by the host requirements-management tool
  (1 requirement → n test cases presumably; is n → m needed?).
- Is there an existing category taxonomy in your process (ASPICE work products, in-house list) the
  engine must adopt verbatim, or does it propose one?
- Safety-related requirements: generate-with-mandatory-review-flag (as specced) or exclude from
  automatic generation entirely?
- Which test management tool, if any, should exports target (Polarion, codeBeamer, Jama, plain
  CSV) — affects the export schema.
