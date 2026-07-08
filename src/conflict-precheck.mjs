const NEGATIVE_PATTERNS = [
  /\bshall not\b/i,
  /\bmust not\b/i,
  /\bwill not\b/i,
  /\bnot\b/i,
  /\bnever\b/i,
  /\bprohibit/i,
  /\bdisable/i
];

const POSITIVE_PATTERNS = [
  /\bshall\b/i,
  /\bmust\b/i,
  /\bwill\b/i,
  /\btransmit\b/i,
  /\bprovide\b/i,
  /\benable\b/i,
  /\ballow\b/i
];

const STOPWORDS = new Set([
  "the",
  "and",
  "shall",
  "must",
  "will",
  "when",
  "while",
  "with",
  "from",
  "between",
  "greater",
  "than",
  "less",
  "system",
  "data"
]);

export function findConflictCandidates(requirements) {
  const candidates = [];
  for (let leftIndex = 0; leftIndex < requirements.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < requirements.length; rightIndex += 1) {
      const left = requirements[leftIndex];
      const right = requirements[rightIndex];
      const similarity = jaccard(keywords(left.text), keywords(right.text));
      const leftNegative = matchesAny(left.text, NEGATIVE_PATTERNS);
      const rightNegative = matchesAny(right.text, NEGATIVE_PATTERNS);
      const leftPositive = matchesAny(left.text, POSITIVE_PATTERNS);
      const rightPositive = matchesAny(right.text, POSITIVE_PATTERNS);

      if (similarity >= 0.3 && leftNegative !== rightNegative && leftPositive && rightPositive) {
        candidates.push({
          id: `PC-${String(candidates.length + 1).padStart(3, "0")}`,
          requirementIds: [left.id, right.id],
          type: "contradiction",
          severity: "high",
          evidence: `${left.id} requires the behavior while ${right.id} prohibits the same or overlapping behavior.`,
          mitigation: "Clarify the applicable mode, speed range, operating condition, or product variant; otherwise choose one behavior and update the conflicting requirement."
        });
        continue;
      }

      const threshold = thresholdConflict(left, right, similarity);
      if (threshold) {
        candidates.push({
          id: `PC-${String(candidates.length + 1).padStart(3, "0")}`,
          requirementIds: [left.id, right.id],
          type: "threshold mismatch",
          severity: "medium",
          evidence: threshold,
          mitigation: "Align the threshold values or split the requirements by interface, mode, or variant."
        });
      }
    }
  }
  return candidates;
}

export function mergeCandidateConflicts(review, candidates) {
  const existingKeys = new Set(review.conflicts.map(conflictKey));
  const missing = candidates.filter(candidate => !existingKeys.has(conflictKey(candidate)));
  const conflictIds = new Set(candidates.flatMap(candidate => candidate.requirementIds));
  const requirements = review.requirements.map(requirement => {
    if (!conflictIds.has(requirement.id)) {
      return requirement;
    }

    const score = requirement.score === 0 ? 75 : Math.min(requirement.score, 82);
    const flags = new Set([...(requirement.flags || []), "set-level conflict"]);
    return {
      ...requirement,
      score,
      flags: [...flags],
      improvement: requirement.improvement || "Resolve the set-level conflict by clarifying mode, condition, threshold, or selected behavior."
    };
  });
  const averageScore = requirements.length
    ? Math.round(requirements.reduce((sum, requirement) => sum + requirement.score, 0) / requirements.length)
    : 0;
  const belowThresholdCount = requirements.filter(requirement => requirement.score < 85).length;

  return {
    ...review,
    summary: {
      ...review.summary,
      averageScore,
      belowThresholdCount,
      conflictCount: review.conflicts.length + missing.length
    },
    requirements,
    conflicts: [...review.conflicts, ...missing]
  };
}

function conflictKey(conflict) {
  return [...conflict.requirementIds].sort().join("|");
}

function thresholdConflict(left, right, similarity) {
  if (similarity < 0.35) {
    return "";
  }

  const leftNumbers = numbersWithUnits(left.text);
  const rightNumbers = numbersWithUnits(right.text);
  for (const leftNumber of leftNumbers) {
    for (const rightNumber of rightNumbers) {
      if (leftNumber.unit !== rightNumber.unit || leftNumber.operator !== rightNumber.operator) {
        continue;
      }
      const delta = Math.abs(leftNumber.value - rightNumber.value);
      if (delta > Math.max(leftNumber.value, rightNumber.value) * 0.25) {
        return `${left.id} and ${right.id} use different ${leftNumber.operator} values: ${leftNumber.value}${leftNumber.unit} vs ${rightNumber.value}${rightNumber.unit}.`;
      }
    }
  }
  return "";
}

function numbersWithUnits(text) {
  const matches = [...text.matchAll(/(.{0,25}?)(\d+(?:\.\d+)?)\s*(km\/h|ms|s|v|a|%|lux|hz)?/gi)];
  return matches.map(match => ({
    value: canonicalValue(Number(match[2]), String(match[3] || "").toLowerCase()),
    unit: canonicalUnit(String(match[3] || "").toLowerCase()),
    operator: operatorFromContext(match[1])
  }));
}

function canonicalValue(value, unit) {
  return unit === "ms" ? value / 1000 : value;
}

function canonicalUnit(unit) {
  return unit === "ms" ? "s" : unit;
}

function operatorFromContext(context) {
  if (/within|less than|below|under|max|maximum|no more than/i.test(context)) {
    return "maximum";
  }
  if (/greater than|above|over|min|minimum|at least/i.test(context)) {
    return "minimum";
  }
  return "value";
}

function matchesAny(text, patterns) {
  return patterns.some(pattern => pattern.test(text));
}

function keywords(text) {
  return new Set(
    String(text || "")
      .toLowerCase()
      .match(/[a-z0-9]+/g)
      ?.filter(word => word.length > 2 && !STOPWORDS.has(word)) || []
  );
}

function jaccard(left, right) {
  if (!left.size || !right.size) {
    return 0;
  }
  const intersection = [...left].filter(word => right.has(word)).length;
  const union = new Set([...left, ...right]).size;
  return intersection / union;
}
