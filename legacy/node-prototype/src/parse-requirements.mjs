export function parseRequirementFile(filename, text) {
  const extension = filename.toLowerCase().split(".").pop();
  if (extension === "json") {
    return parseJsonRequirements(text);
  }
  if (extension === "csv") {
    return parseCsvRequirements(text);
  }
  throw new Error("Upload a .csv or .json file.");
}

export function parseJsonRequirements(text) {
  const payload = JSON.parse(text);
  const rows = Array.isArray(payload) ? payload : payload.requirements;
  if (!Array.isArray(rows)) {
    throw new Error("JSON must be an array or an object with a requirements array.");
  }

  return rows.map((row, index) => normalizeRequirement(row, index)).filter(Boolean);
}

export function parseCsvRequirements(text) {
  const rows = parseCsvRows(text);
  if (rows.length < 2) {
    throw new Error("CSV must include a header row and at least one requirement.");
  }

  const headers = rows[0].map(header => header.trim());
  const records = rows.slice(1);
  return records.map((record, index) => {
    const object = Object.fromEntries(headers.map((header, columnIndex) => [header, record[columnIndex] || ""]));
    return normalizeRequirement(object, index);
  }).filter(Boolean);
}

export function parseCsvRows(text) {
  const rows = [];
  let row = [];
  let value = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];

    if (char === '"' && quoted && next === '"') {
      value += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(value);
      value = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") {
        index += 1;
      }
      row.push(value);
      if (row.some(cell => cell.trim())) {
        rows.push(row);
      }
      row = [];
      value = "";
    } else {
      value += char;
    }
  }

  row.push(value);
  if (row.some(cell => cell.trim())) {
    rows.push(row);
  }
  return rows;
}

function normalizeRequirement(row, index) {
  if (typeof row === "string") {
    const text = row.trim();
    return text ? { id: `REQ-${String(index + 1).padStart(3, "0")}`, text } : null;
  }

  if (!row || typeof row !== "object") {
    return null;
  }

  const lookup = new Map(Object.entries(row).map(([key, value]) => [key.toLowerCase().trim(), value]));
  const text = stringValue(firstValue(lookup, ["text", "requirement", "description", "statement"]));
  if (!text) {
    return null;
  }

  const id = stringValue(firstValue(lookup, ["id", "req_id", "requirement_id", "key"])) || `REQ-${String(index + 1).padStart(3, "0")}`;
  const source = stringValue(firstValue(lookup, ["source", "stakeholder_source", "parent_source"]));
  const parents = listValue(firstValue(lookup, ["parents", "parent", "stakeholder_requirement", "stakeholder_requirements"]));

  return {
    id,
    text,
    source,
    parents,
    domain: stringValue(firstValue(lookup, ["domain"])),
    component: stringValue(firstValue(lookup, ["component", "system_element"])),
    type: stringValue(firstValue(lookup, ["type", "requirement_type"])),
    asil: stringValue(firstValue(lookup, ["asil"]))
  };
}

function firstValue(lookup, keys) {
  for (const key of keys) {
    if (lookup.has(key)) {
      return lookup.get(key);
    }
  }
  return "";
}

function stringValue(value) {
  if (Array.isArray(value)) {
    return value.join(", ").trim();
  }
  return String(value || "").trim();
}

function listValue(value) {
  if (Array.isArray(value)) {
    return value.map(item => String(item).trim()).filter(Boolean);
  }
  return String(value || "")
    .split(/[;,]/)
    .map(item => item.trim())
    .filter(Boolean);
}
