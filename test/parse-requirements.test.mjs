import test from "node:test";
import assert from "node:assert/strict";
import { parseCsvRequirements, parseJsonRequirements } from "../src/parse-requirements.mjs";
import { validateReviewPayload } from "../src/review-contract.mjs";
import { findConflictCandidates, mergeCandidateConflicts } from "../src/conflict-precheck.mjs";

test("parses CSV with quoted conflicting requirement text", () => {
  const csv = [
    "id,source,parents,text",
    'REQ-1,SN-1,SN-1,"The camera shall transmit image data when speed is between 0 km/h and 200 km/h."',
    'REQ-2,SN-1,SN-1,"The camera shall not transmit image data when speed is greater than 120 km/h."'
  ].join("\n");

  const requirements = parseCsvRequirements(csv);

  assert.equal(requirements.length, 2);
  assert.equal(requirements[1].id, "REQ-2");
  assert.match(requirements[0].text, /200 km\/h/);
});

test("parses JSON with mixed key names and parent arrays", () => {
  const requirements = parseJsonRequirements(JSON.stringify({
    requirements: [
      {
        ID: "REQ-A",
        Source: "SN-A",
        Parents: ["SN-A"],
        Requirement: "The controller shall report a diagnostic event within 1 s."
      }
    ]
  }));

  assert.deepEqual(requirements[0].parents, ["SN-A"]);
  assert.equal(requirements[0].source, "SN-A");
  assert.equal(requirements[0].text, "The controller shall report a diagnostic event within 1 s.");
});

test("review contract preserves all input requirement ids", () => {
  const input = [{ id: "REQ-1" }, { id: "REQ-2" }];
  const review = validateReviewPayload({
    requirements: [{ id: "REQ-1", score: 91, flags: [], improvement: "" }],
    conflicts: []
  }, input);

  assert.equal(review.requirements.length, 2);
  assert.equal(review.requirements[1].id, "REQ-2");
  assert.notEqual(review.requirements[1].flags[0], "missing reviewer output");
});

test("review contract accepts requirement_id score rows", () => {
  const input = [{ id: "REQ-1", text: "The controller shall report voltage within 10 ms.", source: "SN-1" }];
  const review = validateReviewPayload({
    requirement_reviews: [
      {
        requirement_id: "REQ-1",
        quality_score: 81,
        issues: ["missing tolerance"],
        recommendation: "Add voltage tolerance and operating conditions."
      }
    ],
    conflicts: []
  }, input);

  assert.equal(review.requirements[0].score, 81);
  assert.equal(review.requirements[0].improvement, "Add voltage tolerance and operating conditions.");
});

test("review contract accepts guideline-style issue arrays", () => {
  const input = [
    { id: "REQ-1", text: "The controller shall enable output X.", source: "SN-1" },
    { id: "REQ-2", text: "The controller shall not enable output X.", source: "SN-1" }
  ];
  const review = validateReviewPayload({
    wording_quality_issues: [
      {
        requirement_id: "REQ-1",
        severity: "major",
        violated_principles: ["consistent"],
        suggested_rewrite: "Clarify the operating mode."
      }
    ],
    consistency_issues: [
      {
        id: "CI-1",
        issue_type: "contradiction",
        severity: "critical",
        related_requirement_ids: ["REQ-1", "REQ-2"],
        evidence: "Opposite enablement behavior.",
        mitigation: "Select one behavior or split by mode.",
        confidence: "high"
      }
    ]
  }, input);

  assert.equal(review.requirements[0].score, 70);
  assert.equal(review.conflicts[0].type, "contradiction");
  assert.deepEqual(review.conflicts[0].requirementIds, ["REQ-1", "REQ-2"]);
});

test("precheck catches opposite obligations over overlapping behavior", () => {
  const requirements = [
    {
      id: "REQ-1",
      text: "The camera system shall transmit image data continuously when vehicle speed is between 0 km/h and 200 km/h."
    },
    {
      id: "REQ-2",
      text: "The camera system shall not transmit image data when vehicle speed is greater than 120 km/h."
    }
  ];

  const candidates = findConflictCandidates(requirements);

  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].type, "contradiction");
});

test("candidate conflicts are preserved when model omits them", () => {
  const review = {
    summary: { requirementCount: 2, averageScore: 90, belowThresholdCount: 0, conflictCount: 0 },
    requirements: [
      { id: "REQ-1", score: 0, flags: ["missing reviewer output"], improvement: "Re-run." },
      { id: "REQ-2", score: 0, flags: ["missing reviewer output"], improvement: "Re-run." }
    ],
    conflicts: []
  };
  const candidate = {
    id: "PC-001",
    requirementIds: ["REQ-1", "REQ-2"],
    type: "contradiction",
    severity: "high",
    evidence: "Opposite obligations.",
    mitigation: "Clarify mode."
  };

  const merged = mergeCandidateConflicts(review, [candidate]);

  assert.equal(merged.summary.conflictCount, 1);
  assert.equal(merged.summary.belowThresholdCount, 2);
  assert.equal(merged.requirements[0].score, 75);
  assert.equal(merged.conflicts[0].type, "contradiction");
});
