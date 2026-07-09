"""Prompts for test-case-generation project context analysis (SPEC-ADDENDUM-A, Stage A: A.2)."""

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
