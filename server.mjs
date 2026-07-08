import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { validateReviewPayload } from "./src/review-contract.mjs";
import { findConflictCandidates, mergeCandidateConflicts } from "./src/conflict-precheck.mjs";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const publicDir = join(__dirname, "public");
const srcDir = join(__dirname, "src");
const promptPath = join(__dirname, "reviewer-system-prompt.md");

const PORT = Number(process.env.PORT || 3000);
const OLLAMA_URL = process.env.OLLAMA_URL || "http://localhost:11434";
const OLLAMA_MODEL = process.env.OLLAMA_MODEL || "gemma3:1b";

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".md": "text/markdown; charset=utf-8"
};

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url || "/", `http://${request.headers.host}`);

    if (request.method === "GET" && url.pathname === "/api/prompt") {
      const prompt = await readFile(promptPath, "utf8");
      return sendJson(response, 200, { prompt, model: OLLAMA_MODEL, ollamaUrl: OLLAMA_URL });
    }

    if (request.method === "POST" && url.pathname === "/api/review") {
      return await handleReview(request, response);
    }

    if (request.method === "GET") {
      return await serveStatic(url.pathname, response);
    }

    sendJson(response, 405, { error: "Method not allowed" });
  } catch (error) {
    sendJson(response, 500, { error: error.message || "Unexpected server error" });
  }
});

server.listen(PORT, () => {
  console.log(`Requirement reviewer running at http://localhost:${PORT}`);
  console.log(`Ollama endpoint: ${OLLAMA_URL}`);
  console.log(`Default model: ${OLLAMA_MODEL}`);
});

async function handleReview(request, response) {
  const body = await readBody(request);
  const payload = JSON.parse(body || "{}");
  const requirements = Array.isArray(payload.requirements) ? payload.requirements : [];
  const projectContext = String(payload.projectContext || "").trim();
  const model = String(payload.model || OLLAMA_MODEL).trim();

  if (!requirements.length) {
    return sendJson(response, 400, { error: "No requirements supplied." });
  }

  const prompt = await readFile(promptPath, "utf8");
  const conflictCandidates = findConflictCandidates(requirements);
  const userMessage = {
    projectContext,
    threshold: 85,
    instructions: [
      "Review the full requirement set.",
      "Return only the JSON object matching the schema.",
      "Include all requirement IDs in the requirements array.",
      "List conflicts and mitigations separately from individual wording scores."
    ],
    requirements,
    conflictCandidates
  };

  const ollamaResponse = await fetch(`${OLLAMA_URL}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model,
      stream: false,
      format: "json",
      options: { temperature: 0.05, num_ctx: 8192 },
      messages: [
        { role: "system", content: prompt },
        { role: "user", content: JSON.stringify(userMessage) }
      ]
    })
  });

  if (!ollamaResponse.ok) {
    const message = await ollamaResponse.text();
    return sendJson(response, 502, {
      error: `Ollama request failed (${ollamaResponse.status}).`,
      detail: message
    });
  }

  const ollamaJson = await ollamaResponse.json();
  const content = ollamaJson?.message?.content || "";
  const review = mergeCandidateConflicts(
    validateReviewPayload(extractJson(content), requirements),
    conflictCandidates
  );
  sendJson(response, 200, review);
}

async function serveStatic(pathname, response) {
  const requestedPath = pathname === "/" ? "/index.html" : pathname;
  const safePath = normalize(requestedPath).replace(/^(\.\.[/\\])+/, "");
  const rootDir = safePath.startsWith("\\src\\") || safePath.startsWith("/src/") ? __dirname : publicDir;
  const fullPath = join(rootDir, safePath);

  if (!fullPath.startsWith(publicDir) && !fullPath.startsWith(srcDir)) {
    return sendJson(response, 403, { error: "Forbidden" });
  }

  try {
    const content = await readFile(fullPath);
    response.writeHead(200, { "content-type": MIME_TYPES[extname(fullPath)] || "application/octet-stream" });
    response.end(content);
  } catch {
    sendJson(response, 404, { error: "Not found" });
  }
}

function extractJson(text) {
  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");
  if (first === -1 || last === -1 || last <= first) {
    throw new Error("Reviewer did not return a JSON object.");
  }
  return JSON.parse(text.slice(first, last + 1));
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let data = "";
    request.setEncoding("utf8");
    request.on("data", chunk => {
      data += chunk;
      if (data.length > 5_000_000) {
        reject(new Error("Request body is too large."));
        request.destroy();
      }
    });
    request.on("end", () => resolve(data));
    request.on("error", reject);
  });
}

function sendJson(response, status, payload) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}
