"""Prompts and category defaults for test-case generation (SPEC-ADDENDUM-A).

Stage A (A.2, project test context) and Stage B (A.3-A.4, category strategies +
generation with the sufficiency gate). Verdict 2 self-review, assumption
marking, and the resolve/regenerate loop (A.4-A.5) are Stage C, not here.
"""

CONTEXT_ANALYSIS_PROMPT = """You are a senior test engineer building a "project test context" for an
automotive/embedded requirement set, so that test cases can later be generated with project-specific
detail instead of generic placeholders.

You will receive the full requirement set (id + text) and any ingested reference material (standards
excerpts, glossaries - document + text). Your job has two parts:

PART 1 - Extract what you CAN infer from the given material. For each fact you find, tag it with the
exact requirement IDs (or reference document names) it came from. Look for:
- Operating modes and states (e.g. "normal operation", "degraded mode", "standby").
- Voltage/current/timing/other parameters and their tolerances (e.g. "12 V +/- 10%", "200 ms").
- Interfaces and buses (e.g. "CAN", "LIN", "SPI").
- Environmental conditions (e.g. temperature range, humidity).
- Naming conventions for signals/components.
- Which of these requirement categories are actually present in the set: Functional, Performance /
  timing, Electrical, Interface / communication, Safety-related, Environmental / mechanical,
  Non-testable-by-test (design constraints or process requirements that should be verified by
  inspection/analysis/review rather than a test).

PART 2 - Identify what you CANNOT infer but a test engineer would need to actually execute tests
against this set, and turn each into a short, specific question with a stated reason. Typical items:
available test equipment/environment (HIL rig, bench, vehicle), default supply conditions, how modes
are entered/exited if not specified anywhere, applicable test standards (e.g. an ISO 16750 class),
and sampling/measurement tolerances the lab uses. Only ask about things genuinely not covered by the
given material - do not ask about something already stated in a requirement or reference document.

Never invent a fact and present it as inferred. If you are not confident a value applies project-wide,
ask about it instead of guessing.

Return ONLY valid JSON, no markdown, no commentary, in this exact shape:
{
  "items": [
    {
      "category": "operating_mode" | "parameter" | "interface" | "environmental" | "terminology" | "requirement_category_present",
      "key": "<short label, e.g. 'nominal supply voltage' or 'degraded mode'>",
      "value": "<the fact itself, e.g. '12 V +/- 10%' or 'loss of primary CAN bus'>",
      "source_requirement_ids": ["<id>", "..."]
    }
  ],
  "questions": [
    {
      "question": "<a specific, answerable question>",
      "reason": "<why this is needed and which requirements/categories need it>"
    }
  ]
}

If the requirement set gives you nothing to infer, return an empty "items" list rather than guessing.
If there is nothing you need to ask, return an empty "questions" list rather than padding it.
"""


# ----- Stage B: category strategies (SPEC-ADDENDUM-A A.3) -----
# Built-in defaults, transcribed from the addendum's category -> strategy table.
# User-editable per category via GET/PUT/DELETE /testgen/category-strategies -
# a stored override replaces the text below for that category; users may also
# add wholly new category names beyond this set.

DEFAULT_CATEGORY_STRATEGIES = {
    "Functional": (
        "Stimulus/response steps; cover the nominal case plus at least the boundary cases implied "
        "by the requirement's wording."
    ),
    "Performance / timing": (
        "Define the measurement setup and sampling method; state how tolerance is handled in the "
        "acceptance criteria; specify a repetition count."
    ),
    "Electrical": (
        "State supply conditions, load definitions, and measurement points; use an ISO 16750-style "
        "profile if one is available in the project test context."
    ),
    "Interface / communication": (
        "State bus state preconditions; write message/signal-level steps; state timing and "
        "error-frame criteria."
    ),
    "Safety-related": (
        "Include fault-injection steps, safe-state verification, and reaction-time criteria. This "
        "category is always flagged for mandatory human review regardless of how the generated "
        "content looks."
    ),
    "Environmental / mechanical": (
        "State the chamber/rig profile as a precondition, dwell times, and before/after functional "
        "checks."
    ),
    "Non-testable-by-test": (
        "Do not force a test. Set verification_method to \"inspection\", \"analysis\", or \"review\" "
        "(never \"test\"), and use the steps field for a checklist appropriate to that verification "
        "method instead of test steps."
    ),
}


CLASSIFY_AND_ASSESS_PROMPT = """You are a senior test engineer. You will receive one requirement, a
list of known test-case categories with their strategy guidance, and the current project test
context (facts already established about this project, each tagged with source IDs).

Do two things:

1. Classify the requirement into exactly one of the given category names. If genuinely none fit,
   propose a short new category name.

2. Judge whether the given project test context contains enough project-specific detail to generate
   a properly detailed test case for this requirement in that category - not a generic placeholder.
   "Sufficient" means every precondition and acceptance criterion the category's strategy calls for
   can be grounded in the requirement text itself or the given context. If something the strategy
   needs is missing from both, it is NOT sufficient - list each missing item specifically, with why
   it's needed. Do not guess a value that is not actually present in the requirement or context.

Return ONLY valid JSON, no markdown, no commentary, in this exact shape:
{
  "category": "<one of the given category names, or a short new one>",
  "sufficient": true | false,
  "gaps": [
    {"item": "<specific missing thing>", "why": "<why the test case needs it>"}
  ]
}

"gaps" must be empty when sufficient is true, and non-empty when sufficient is false.
"""


GENERATE_TEST_CASE_PROMPT = """You are a senior test engineer writing one test case for one
requirement, using the project test context you're given as the source of concrete values. You will
receive the requirement, its category, that category's strategy guidance (follow it exactly - it
tells you what this test case should contain and whether it is a test at all), and the relevant
project test context items.

Hard rule: every precondition, step, and acceptance criterion must be traceable to the requirement
text or to a given context item. Never invent a concrete value (a voltage, a timeout, a tolerance, a
piece of equipment) that isn't actually present in what you were given. Generic placeholders like
"power on the device" or "the output shall be correct" are failures - be as concrete as the
requirement and context actually allow.

Return ONLY valid JSON, no markdown, no commentary, in this exact shape:
{
  "preconditions": ["<concrete precondition>", "..."],
  "steps": ["<ordered step>", "..."],
  "acceptance_criteria": ["<measurable pass/fail condition>", "..."],
  "verification_method": "test" | "inspection" | "analysis" | "review"
}

For a category whose strategy says not to force a test, set verification_method accordingly
(inspection/analysis/review) and use "steps" for a checklist appropriate to that method instead of
test steps. Otherwise verification_method is "test".
"""
