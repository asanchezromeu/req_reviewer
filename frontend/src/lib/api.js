import axios from "axios";

const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
export const API = `${BACKEND_URL}/api`;

const client = axios.create({ baseURL: API });

export const api = {
  models: () => client.get("/models").then((r) => r.data),
  ollamaModels: (url) =>
    client.get("/ollama/models", { params: { url } }).then((r) => r.data),
  incoseRules: () => client.get("/incose/rules").then((r) => r.data),

  // Requirement sets
  uploadRequirements: (file, name) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    return client.post("/requirements/upload", fd).then((r) => r.data);
  },
  listSets: () => client.get("/requirements/sets").then((r) => r.data),
  getSet: (id) => client.get(`/requirements/sets/${id}`).then((r) => r.data),
  deleteSet: (id) => client.delete(`/requirements/sets/${id}`).then((r) => r.data),

  // Raspberry Pi showcase
  showcaseRequirements: () =>
    client.get("/showcase/requirements").then((r) => r.data),
  saveShowcaseRequirements: (payload) =>
    client.put("/showcase/requirements", payload).then((r) => r.data),
  importShowcaseRequirements: (file, embeddingModel, ollamaUrl) => {
    const fd = new FormData();
    fd.append("file", file);
    return client
      .post("/showcase/import", fd, {
        params: {
          embedding_model: embeddingModel,
          ollama_url: ollamaUrl,
        },
      })
      .then((r) => r.data);
  },
  showcaseIndexStatus: () =>
    client.get("/showcase/index/status").then((r) => r.data),
  searchShowcase: (payload) =>
    client.post("/showcase/search", payload).then((r) => r.data),

  // Analysis
  analyzeIndividual: (payload) =>
    client.post("/analyze/individual", payload).then((r) => r.data),
  analyzeSet: (payload) => client.post("/analyze/set", payload).then((r) => r.data),
  ask: (payload) => client.post("/summarize/ask", payload).then((r) => r.data),
  classifySet: (payload) => client.post("/classify/set", payload).then((r) => r.data),

  // Prompts library (tailoring + classifier)
  listPrompts: (kind) =>
    client.get("/prompts", { params: kind ? { kind } : {} }).then((r) => r.data),
  createPrompt: (payload) => client.post("/prompts", payload).then((r) => r.data),
  deletePrompt: (id) => client.delete(`/prompts/${id}`).then((r) => r.data),
  generatePrompt: (payload) =>
    client.post("/prompts/generate", payload).then((r) => r.data),

  // Training
  listExamples: () => client.get("/training/examples").then((r) => r.data),
  createExample: (payload) =>
    client.post("/training/examples", payload).then((r) => r.data),
  deleteExample: (id) => client.delete(`/training/examples/${id}`).then((r) => r.data),

  uploadDataset: (file, name) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    return client.post("/training/datasets", fd).then((r) => r.data);
  },
  listDatasets: () => client.get("/training/datasets").then((r) => r.data),
  deleteDataset: (id) =>
    client.delete(`/training/datasets/${id}`).then((r) => r.data),

  // Distillation
  startJob: (payload) =>
    client.post("/distillation/jobs", payload).then((r) => r.data),
  listJobs: () => client.get("/distillation/jobs").then((r) => r.data),
  refreshJob: (id, openai_api_key) => {
    const fd = new FormData();
    fd.append("openai_api_key", openai_api_key);
    return client.post(`/distillation/jobs/${id}/refresh`, fd).then((r) => r.data);
  },
  deleteJob: (id) => client.delete(`/distillation/jobs/${id}`).then((r) => r.data),
};
