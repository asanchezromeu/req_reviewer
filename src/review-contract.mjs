export function validateReviewPayload(payload, inputRequirements) {
  if (!payload || typeof payload !== "object") {
    throw new Error("Reviewer response is not an object.");
  }

  const inputIds = inputRequirements.map(requirement => String(requirement.id || "").trim()).filter(Boolean);
  const requirementRows = collectRequirementRows(payload);
  const byId = new Map(
    requirementRows
      .map(row => [normalizeId(firstDefined(row.id, row.requirementId, row.requirement_id, row.req_id)), row])
      .filter(([id]) => id)
  );

  const normalizedRequirements = inputIds.map(id => {
    const row = byId.get(normalizeId(id));
    if (!row) {
      return baselineRequirementReview(inputRequirements.find(requirement => String(requirement.id).trim() === id));
    }

    const score = clamp(Number(firstDefined(row.score, row.qualityScore, row.quality_score, row.rating)), 0, 100);
    return {
      id,
      score,
      flags: normalizeFlags(row),
      improvement: score < 85 ? String(firstDefined(row.improvement, row.recommendation, row.proposal, row.suggested_rewrite, "")) : ""
    };
  });

  const conflictRows = collectConflictRows(payload);
  const conflicts = conflictRows.length
    ? conflictRows.map((conflict, index) => ({
        id: String(conflict.id || `C-${String(index + 1).padStart(3, "0")}`),
        requirementIds: normalizeStringList(firstDefined(conflict.requirementIds, conflict.requirement_ids, conflict.related_requirement_ids)),
        type: String(firstDefined(conflict.type, conflict.issue_type, "conflict")),
        severity: normalizeSeverity(conflict.severity),
        evidence: cleanGeneratedText(firstDefined(conflict.evidence, conflict.risk)),
        mitigation: cleanGeneratedText(firstDefined(conflict.mitigation, conflict.recommendation, conflict.action))
      })).filter(conflict => conflict.requirementIds.length && conflict.evidence)
    : [];

  const averageScore = normalizedRequirements.length
    ? Math.round(normalizedRequirements.reduce((sum, row) => sum + row.score, 0) / normalizedRequirements.length)
    : 0;
  const belowThresholdCount = normalizedRequirements.filter(row => row.score < 85).length;

  return {
    summary: {
      requirementCount: normalizedRequirements.length,
      averageScore,
      belowThresholdCount,
      conflictCount: conflicts.length,
      overallAssessment: String(payload.summary?.overallAssessment || "").trim()
    },
    requirements: normalizedRequirements,
    conflicts
  };
}

function collectRequirementRows(payload) {
  if (Array.isArray(payload.requirements)) {
    return payload.requirements;
  }

  const alternativeArrays = [
    payload.requirementReviews,
    payload.requirement_reviews,
    payload.requirement_scores,
    payload.scores,
    payload.individual_requirements,
    payload.individualRequirements
  ];
  const directRows = alternativeArrays.find(Array.isArray);
  if (directRows) {
    return directRows;
  }

  if (Array.isArray(payload.wording_quality_issues)) {
    const rowsById = new Map();
    for (const issue of payload.wording_quality_issues) {
      const id = String(firstDefined(issue.requirement_id, issue.requirementId, issue.id)).trim();
      if (!id) {
        continue;
      }
      const severity = String(issue.severity || "").toLowerCase();
      const score = severity === "major" ? 70 : 82;
      rowsById.set(id, {
        id,
        score: Math.min(rowsById.get(id)?.score ?? 100, score),
        flags: normalizeStringList(issue.violated_principles),
        improvement: firstDefined(issue.suggested_rewrite, issue.mitigation, issue.evidence)
      });
    }
    return [...rowsById.values()];
  }

  return [];
}

function collectConflictRows(payload) {
  return [
    payload.conflicts,
    payload.consistency_issues,
    payload.consistencyIssues,
    payload.coverage_gaps,
    payload.coverageGaps
  ].find(Array.isArray) || [];
}

function baselineRequirementReview(requirement) {
  const text = String(requirement?.text || requirement?.requirement || "");
  const flags = [];
  let score = 100;

  if (!/\bshall\b|\bmust\b/i.test(text)) {
    score -= 12;
    flags.push("missing shall/must");
  }
  if (!/\d|\bwithin\b|\bat least\b|\bno more than\b|\bbetween\b|\bfrom\b|\btolerance\b/i.test(text)) {
    score -= 14;
    flags.push("missing measurable criterion");
  }
  if (/\bquickly\b|\bfast\b|\bappropriate\b|\bsufficient\b|\brobust\b|\buser-friendly\b|\bas needed\b/i.test(text)) {
    score -= 12;
    flags.push("ambiguous wording");
  }
  if (!requirement?.source && !(Array.isArray(requirement?.parents) && requirement.parents.length)) {
    score -= 8;
    flags.push("traceability missing");
  }

  score = clamp(score, 0, 100);
  return {
    id: String(requirement?.id || ""),
    score,
    flags: flags.length ? flags : ["baseline review"],
    improvement: score < 85
      ? "Add measurable criteria, clear conditions, traceability, or precise wording so the requirement is verifiable."
      : ""
  };
}

function normalizeFlags(row) {
  return normalizeStringList(firstDefined(row.flags, row.issues, row.violated_principles))
    .filter(flag => cleanGeneratedText(flag));
}

function cleanGeneratedText(value) {
  const text = String(value || "").trim();
  const placeholders = [
    "short explanation of the conflict",
    "short proposal to remove or manage the conflict",
    "short statement"
  ];
  return placeholders.includes(text.toLowerCase()) ? "" : text;
}

function normalizeStringList(value) {
  if (Array.isArray(value)) {
    return value.map(item => String(item).trim()).filter(Boolean);
  }
  if (typeof value === "string" && value.trim()) {
    return value.split(",").map(item => item.trim()).filter(Boolean);
  }
  return [];
}

function normalizeId(value) {
  return String(value || "").trim().toUpperCase();
}

function firstDefined(...values) {
  return values.find(value => value !== undefined && value !== null && value !== "");
}

function normalizeSeverity(value) {
  const normalized = String(value || "").toLowerCase();
  return ["high", "medium", "low"].includes(normalized) ? normalized : "medium";
}

function clamp(value, min, max) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.round(value)));
}
