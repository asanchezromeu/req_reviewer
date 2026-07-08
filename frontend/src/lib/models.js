export const INCOSE_RULE_KEYS = [
  { key: "necessary", label: "Necessary" },
  { key: "singular", label: "Singular" },
  { key: "unambiguous", label: "Unambiguous" },
  { key: "complete", label: "Complete" },
  { key: "verifiable", label: "Verifiable" },
  { key: "feasible", label: "Feasible" },
  { key: "consistent", label: "Consistent" },
  { key: "traceable", label: "Traceable" },
];

export const scoreColor = (n) => {
  if (n >= 85) return { bg: "bg-[#099250]", text: "text-white", label: "PASS" };
  if (n >= 60) return { bg: "bg-[#F5A623]", text: "text-black", label: "WARN" };
  return { bg: "bg-[#E52B20]", text: "text-white", label: "FAIL" };
};
