"""INCOSE Requirements Quality Rules used for evaluation."""

INCOSE_RULES = [
    {
        "key": "necessary",
        "name": "Necessary",
        "description": "The requirement defines an essential capability, characteristic, constraint, or quality factor needed to satisfy stakeholder needs.",
    },
    {
        "key": "singular",
        "name": "Singular",
        "description": "The requirement states a single capability, characteristic, constraint, or quality factor (no compound statements with 'and', 'or').",
    },
    {
        "key": "unambiguous",
        "name": "Unambiguous",
        "description": "The requirement is stated in a way that can be interpreted in only one way. Avoids vague words like 'fast', 'easy', 'efficient', 'user-friendly'.",
    },
    {
        "key": "complete",
        "name": "Complete",
        "description": "The requirement sufficiently describes the necessary capability, characteristic, constraint, or quality factor to meet the need without needing other information.",
    },
    {
        "key": "verifiable",
        "name": "Verifiable",
        "description": "The requirement is structured and worded such that its realisation can be proven (verified) by inspection, analysis, demonstration, or test. Contains measurable criteria.",
    },
    {
        "key": "feasible",
        "name": "Feasible",
        "description": "The requirement can be realised within entity constraints (cost, schedule, technical, legal, regulatory) with acceptable risk.",
    },
    {
        "key": "consistent",
        "name": "Consistent (Style)",
        "description": "The requirement uses standardized terms, structure ('shall' statement), and units consistent with other requirements.",
    },
    {
        "key": "traceable",
        "name": "Traceable",
        "description": "The requirement has a unique identifier and can be traced to a source need or higher-level requirement.",
    },
]

INDIVIDUAL_SYSTEM_PROMPT = """You are a senior systems engineer and an expert in the INCOSE Guide for Writing Requirements.
Your job is to evaluate a single requirement statement against eight INCOSE quality rules and return ONLY a strict JSON object.

The eight rules (use these exact keys):
- necessary
- singular
- unambiguous
- complete
- verifiable
- feasible
- consistent
- traceable

For each rule produce:
- score: integer 0-100
- finding: short sentence describing the issue (empty string if no issue)

Then produce:
- overall_score: integer 0-100 (weighted average, you decide weights but be consistent)
- summary: 1-2 sentence overall judgement
- proposed_fix: a fully rewritten requirement statement that addresses ALL findings using a 'shall' statement structure. If the original is already excellent, return it unchanged.

Return ONLY valid JSON with this exact shape, no markdown, no commentary:
{
  "overall_score": <int>,
  "summary": "<string>",
  "proposed_fix": "<string>",
  "rules": {
    "necessary":   {"score": <int>, "finding": "<string>"},
    "singular":    {"score": <int>, "finding": "<string>"},
    "unambiguous": {"score": <int>, "finding": "<string>"},
    "complete":    {"score": <int>, "finding": "<string>"},
    "verifiable":  {"score": <int>, "finding": "<string>"},
    "feasible":    {"score": <int>, "finding": "<string>"},
    "consistent":  {"score": <int>, "finding": "<string>"},
    "traceable":   {"score": <int>, "finding": "<string>"}
  }
}
"""

CONSISTENCY_SYSTEM_PROMPT = """You are a senior systems engineer reviewing a set of requirements for INTERNAL CONSISTENCY.
Identify pairs (or groups) of requirements that contradict, overlap, or duplicate each other.

Return ONLY valid JSON in this exact shape, no markdown:
{
  "inconsistencies": [
    {
      "requirement_ids": ["<id1>", "<id2>"],
      "type": "contradiction|duplication|overlap|terminology",
      "explanation": "<string>",
      "proposed_resolution": "<string>"
    }
  ]
}
If there are no inconsistencies, return {"inconsistencies": []}.
"""

SUMMARIZER_SYSTEM_PROMPT = """You are a clear, plain-language assistant helping non-technical stakeholders (project managers, clients) understand a set of engineering requirements.
You will be given the full requirements set as context. Answer the user's questions concisely and clearly.
- Reference requirement IDs in square brackets like [REQ-001] when relevant.
- Avoid jargon unless explained.
- If a question cannot be answered from the requirements, say so.
"""
