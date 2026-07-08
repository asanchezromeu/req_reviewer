import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "sonner";
import { Loader2, Upload, Play, AlertTriangle, MessageSquareText, Sparkles, Tag } from "lucide-react";
import { API, api } from "@/lib/api";
import { INCOSE_RULE_KEYS, scoreColor } from "@/lib/models";

const Panel = ({ title, children, action, testid }) => (
  <section className="bg-white border border-[#DEE2E6]" data-testid={testid}>
    <header className="flex items-center justify-between px-5 py-3 border-b border-[#DEE2E6]">
      <h3 className="text-xs font-mono uppercase tracking-[0.2em] text-neutral-500">{title}</h3>
      {action}
    </header>
    <div className="p-5">{children}</div>
  </section>
);

const ModelSelector = ({
  models,
  sourceType,
  setSourceType,
  provider,
  setProvider,
  model,
  setModel,
  ollamaUrl,
  setOllamaUrl,
  localModels,
  localModelsLoading,
  refreshLocalModels,
}) => {
  const list = provider === "ollama" ? localModels : (models[provider] || []);
  return (
    <div className="space-y-3">
      <div>
        <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
          Model source
        </Label>
        <div className="mt-1.5 grid grid-cols-2 gap-2">
          {[
            { value: "online", label: "Online models" },
            { value: "local", label: "Local models" },
          ].map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={async () => {
                setSourceType(option.value);
                const nextProvider = option.value === "local" ? "ollama" : "openai";
                setProvider(nextProvider);
                if (option.value === "local") {
                  const discovered = await refreshLocalModels();
                  setModel(discovered[0]?.id || "gemma3:1b");
                } else {
                  setModel((models[nextProvider] || [])[0]?.id || "");
                }
              }}
              className={`model-source-button ${sourceType === option.value ? "active" : ""}`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
      <div>
        <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
          Provider
        </Label>
        <Select value={provider} onValueChange={(v) => { setProvider(v); setModel((models[v] || [])[0]?.id || ""); }}>
          <SelectTrigger data-testid="provider-select" className="rounded-none mt-1.5 border-[#DEE2E6] h-10">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="rounded-none">
            {sourceType === "local" ? (
              <SelectItem value="ollama">Ollama</SelectItem>
            ) : (
              <>
                <SelectItem value="openai">OpenAI</SelectItem>
                <SelectItem value="anthropic">Anthropic</SelectItem>
                <SelectItem value="gemini">Google Gemini</SelectItem>
              </>
            )}
          </SelectContent>
        </Select>
      </div>
      <div>
        <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
          Model
        </Label>
        <Select value={model} onValueChange={setModel}>
          <SelectTrigger data-testid="model-select" className="rounded-none mt-1.5 border-[#DEE2E6] h-10">
            <SelectValue placeholder="Choose model" />
          </SelectTrigger>
          <SelectContent className="rounded-none">
            {list.map((m) => (
              <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      </div>
      {sourceType === "local" && (
        <div>
          <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
            Ollama server
          </Label>
          <Input
            value={ollamaUrl}
            onChange={(event) => setOllamaUrl(event.target.value)}
            onBlur={refreshLocalModels}
            className="mt-1.5 h-10"
            placeholder="http://localhost:11434"
            data-testid="ollama-url"
          />
        </div>
      )}
      {sourceType === "local" && (
        <p className="text-[11px] text-neutral-500">
          {localModelsLoading
            ? "Checking installed Ollama models..."
            : `${localModels.length} installed model${localModels.length === 1 ? "" : "s"} detected.`}
        </p>
      )}
    </div>
  );
};

const ScoreBadge = ({ value }) => {
  const c = scoreColor(value);
  return (
    <div className="inline-flex items-center gap-2">
      <span className={`px-2 py-0.5 text-[11px] font-mono font-bold ${c.bg} ${c.text}`}>
        {value}
      </span>
      <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
        {c.label}
      </span>
    </div>
  );
};

const RuleBreakdown = ({ rules }) => (
  <div className="grid grid-cols-1 sm:grid-cols-2 gap-px bg-[#DEE2E6] border border-[#DEE2E6]">
    {INCOSE_RULE_KEYS.map((r) => {
      const v = rules?.[r.key];
      if (!v) return null;
      const c = scoreColor(v.score);
      return (
        <div key={r.key} className="bg-white px-4 py-3" data-testid={`rule-${r.key}`}>
          <div className="flex items-center justify-between">
            <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-neutral-500">
              {r.label}
            </div>
            <span className={`px-1.5 py-0.5 text-[10px] font-mono font-bold ${c.bg} ${c.text}`}>
              {v.score}
            </span>
          </div>
          {v.finding ? (
            <div className="mt-1.5 text-xs text-neutral-700 leading-snug">{v.finding}</div>
          ) : (
            <div className="mt-1.5 text-xs text-neutral-400 italic">No finding</div>
          )}
        </div>
      );
    })}
  </div>
);

export default function AnalyzeTab({ models }) {
  const [mode, setMode] = useState("set"); // individual | set | classify | ask
  const [sourceType, setSourceType] = useState("online");
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState("gpt-4o-mini");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [localModels, setLocalModels] = useState([]);
  const [localModelsLoading, setLocalModelsLoading] = useState(false);

  // Tailoring & classifier prompts
  const [tailoringPrompts, setTailoringPrompts] = useState([]);
  const [classifierPrompts, setClassifierPrompts] = useState([]);
  const [tailoringPromptId, setTailoringPromptId] = useState("");
  const [classifierPromptId, setClassifierPromptId] = useState("");

  // Individual
  const [reqText, setReqText] = useState("The system shall be fast and easy to use.");
  const [individualResult, setIndividualResult] = useState(null);
  const [indLoading, setIndLoading] = useState(false);

  // Sets
  const [sets, setSets] = useState([]);
  const [selectedSetId, setSelectedSetId] = useState("");
  const [setResult, setSetResult] = useState(null);
  const [setLoading, setSetLoading] = useState(false);
  const [uploading, setUploading] = useState(false);

  // Classify
  const [classifyResult, setClassifyResult] = useState(null);
  const [classifyLoading, setClassifyLoading] = useState(false);

  // Q&A
  const [question, setQuestion] = useState("");
  const [chat, setChat] = useState([]);
  const [askLoading, setAskLoading] = useState(false);

  useEffect(() => {
    refreshSets();
    refreshPrompts();
  }, []);

  const refreshPrompts = async () => {
    try {
      const all = await api.listPrompts();
      setTailoringPrompts(all.filter((p) => p.kind === "tailoring"));
      setClassifierPrompts(all.filter((p) => p.kind === "classifier"));
    } catch { /* silent */ }
  };

  const refreshSets = async () => {
    try {
      const s = await api.listSets();
      setSets(s);
      setSelectedSetId((prev) => prev || (s[0]?.id ?? ""));
    } catch (e) {
      toast.error("Failed to load sets");
    }
  };

  const refreshLocalModels = async () => {
    setLocalModelsLoading(true);
    try {
      const discovered = await api.ollamaModels(ollamaUrl);
      setLocalModels(discovered);
      if (!discovered.length) {
        toast.error("Ollama is running but no installed models were found");
      }
      return discovered;
    } catch (error) {
      setLocalModels([]);
      toast.error(error?.response?.data?.detail || `Could not connect to Ollama at ${ollamaUrl}`);
      return [];
    } finally {
      setLocalModelsLoading(false);
    }
  };

  const onUpload = async (e) => {
    const inputEl = e.target;
    const f = inputEl.files && inputEl.files[0];
    if (!f) return;
    setUploading(true);
    try {
      const res = await api.uploadRequirements(f);
      toast.success(`Uploaded ${res.count} requirements`);
      setSelectedSetId(res.set_id);
      await refreshSets();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const message = typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : `Upload failed. Check that the API is running at ${API}.`;
      toast.error(message);
    } finally {
      setUploading(false);
      if (inputEl) inputEl.value = "";
    }
  };

  const runIndividual = async () => {
    if (!reqText.trim()) {
      toast.error("Enter a requirement");
      return;
    }
    setIndLoading(true); setIndividualResult(null);
    try {
      const r = await api.analyzeIndividual({
        text: reqText, provider, model, ollama_url: sourceType === "local" ? ollamaUrl : undefined,
        tailoring_prompt_id: tailoringPromptId || undefined,
      });
      setIndividualResult(r);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Analysis failed");
    } finally { setIndLoading(false); }
  };

  const runSet = async () => {
    if (!selectedSetId) {
      toast.error("Upload or select a set");
      return;
    }
    setSetLoading(true); setSetResult(null);
    try {
      const r = await api.analyzeSet({
        set_id: selectedSetId, provider, model, ollama_url: sourceType === "local" ? ollamaUrl : undefined,
        tailoring_prompt_id: tailoringPromptId || undefined,
      });
      setSetResult(r);
      toast.success(`Average score ${r.average_score}/100`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Set analysis failed");
    } finally { setSetLoading(false); }
  };

  const runClassify = async () => {
    if (!selectedSetId) {
      toast.error("Upload or select a set");
      return;
    }
    if (!classifierPromptId) {
      toast.error("Pick a classifier prompt (create one in the Tailoring tab)");
      return;
    }
    setClassifyLoading(true); setClassifyResult(null);
    try {
      const r = await api.classifySet({
        set_id: selectedSetId, provider, model, prompt_id: classifierPromptId,
        ollama_url: sourceType === "local" ? ollamaUrl : undefined,
      });
      setClassifyResult(r);
      toast.success(`Classified ${r.results.length} requirements`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Classification failed");
    } finally { setClassifyLoading(false); }
  };

  const ask = async () => {
    if (!selectedSetId) {
      toast.error("Pick a set first");
      return;
    }
    if (!question.trim()) return;
    const q = question.trim();
    setQuestion("");
    const newChat = [...chat, { role: "user", content: q }];
    setChat(newChat);
    setAskLoading(true);
    try {
      const r = await api.ask({
        set_id: selectedSetId, question: q, provider, model, history: chat,
        ollama_url: sourceType === "local" ? ollamaUrl : undefined,
        tailoring_prompt_id: tailoringPromptId || undefined,
      });
      setChat([...newChat, { role: "assistant", content: r.answer }]);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Ask failed");
    } finally { setAskLoading(false); }
  };

  const sortedResults = useMemo(() => {
    if (!setResult?.results) return [];
    return [...setResult.results].sort((a, b) => a.overall_score - b.overall_score);
  }, [setResult]);

  return (
    <div className="grid grid-cols-12 gap-6" data-testid="analyze-tab">
      {/* LEFT — Config */}
      <div className="col-span-12 lg:col-span-4 space-y-6">
        <Panel
          title="① Mode"
          testid="mode-panel"
          action={
            <Badge variant="outline" className="rounded-none border-[#DEE2E6] font-mono text-[10px]">
              {mode.toUpperCase()}
            </Badge>
          }
        >
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-[#DEE2E6] border border-[#DEE2E6]">
            {[
              { v: "individual", l: "Individual" },
              { v: "set", l: "Full Set" },
              { v: "classify", l: "Classify" },
              { v: "ask", l: "Ask the Set" },
            ].map((m) => (
              <button
                key={m.v}
                data-testid={`mode-${m.v}`}
                onClick={() => setMode(m.v)}
                className={`px-3 py-2 text-xs font-medium transition-colors ${
                  mode === m.v ? "bg-[#0A0D14] text-white" : "bg-white text-neutral-700 hover:bg-neutral-50"
                }`}
              >
                {m.l}
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="② Model" testid="model-panel">
          <ModelSelector
            models={models}
            sourceType={sourceType}
            setSourceType={setSourceType}
            provider={provider}
            setProvider={setProvider}
            model={model}
            setModel={setModel}
            ollamaUrl={ollamaUrl}
            setOllamaUrl={setOllamaUrl}
            localModels={localModels}
            localModelsLoading={localModelsLoading}
            refreshLocalModels={refreshLocalModels}
          />
          <p className="mt-3 text-[11px] text-neutral-500 leading-snug">
            Lightweight models are faster and cheaper. Use larger models for higher accuracy on
            complex requirement sets.
          </p>
        </Panel>

        <Panel
          title="③ Source"
          testid="source-panel"
          action={
            <label className="cursor-pointer inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 border border-[#DEE2E6] hover:bg-neutral-50 transition-colors">
              <Upload size={12} />
              {uploading ? "Uploading…" : "Upload"}
              <input
                type="file"
                accept=".csv,.json"
                className="hidden"
                onChange={onUpload}
                data-testid="upload-requirements-input"
              />
            </label>
          }
        >
          {mode === "individual" ? (
            <Textarea
              data-testid="individual-text"
              value={reqText}
              onChange={(e) => setReqText(e.target.value)}
              rows={6}
              className="rounded-none border-[#DEE2E6] font-mono text-xs leading-relaxed"
              placeholder="The system shall ..."
            />
          ) : (
            <div className="space-y-3">
              <Select value={selectedSetId} onValueChange={setSelectedSetId}>
                <SelectTrigger className="rounded-none border-[#DEE2E6] h-10" data-testid="set-select">
                  <SelectValue placeholder="Select a requirements set" />
                </SelectTrigger>
                <SelectContent className="rounded-none">
                  {sets.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name} · {s.count}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {sets.length === 0 && (
                <div className="text-[11px] text-neutral-500 leading-snug border border-dashed border-[#DEE2E6] p-3">
                  Upload a CSV with columns <span className="font-mono">id,text</span> or a JSON
                  array of <span className="font-mono">{`{id,text}`}</span>. Or
                  <button
                    className="ml-1 underline"
                    data-testid="load-sample-btn"
                    onClick={async () => {
                      const sample = [
                        { id: "REQ-001", text: "The system shall be fast." },
                        { id: "REQ-002", text: "The system shall respond to user input within 200 ms under nominal load (50 concurrent users)." },
                        { id: "REQ-003", text: "The system shall be user-friendly and easy to navigate." },
                        { id: "REQ-004", text: "The system shall support TLS 1.3 for all external traffic and shall reject TLS 1.2 connections." },
                        { id: "REQ-005", text: "The system shall support TLS 1.2 for all external traffic." },
                        { id: "REQ-006", text: "The system shall log all errors and warnings to /var/log/app.log and email administrators when disk usage exceeds 80%." },
                      ];
                      const blob = new Blob([JSON.stringify(sample)], { type: "application/json" });
                      const file = new File([blob], "sample.json", { type: "application/json" });
                      setUploading(true);
                      try {
                        const r = await api.uploadRequirements(file, "Sample Set");
                        toast.success(`Loaded ${r.count} sample requirements`);
                        setSelectedSetId(r.set_id);
                        await refreshSets();
                      } finally { setUploading(false); }
                    }}
                  >
                    load a sample set
                  </button>.
                </div>
              )}
            </div>
          )}
        </Panel>

        <Panel
          title={mode === "classify" ? "④ Classifier prompt" : "④ Tailoring (optional)"}
          testid="tailoring-select-panel"
          action={
            <a
              href="#"
              onClick={(e) => { e.preventDefault(); refreshPrompts(); }}
              className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500 hover:text-[#0A0D14]"
              data-testid="refresh-prompts-link"
            >
              ↻ Refresh
            </a>
          }
        >
          {mode === "classify" ? (
            <Select value={classifierPromptId} onValueChange={setClassifierPromptId}>
              <SelectTrigger className="rounded-none border-[#DEE2E6] h-10" data-testid="classifier-prompt-select">
                <SelectValue placeholder="Pick a classifier prompt" />
              </SelectTrigger>
              <SelectContent className="rounded-none">
                {classifierPrompts.length === 0 && (
                  <SelectItem disabled value="__none">
                    None — create one in the Tailoring tab
                  </SelectItem>
                )}
                {classifierPrompts.map((p) => (
                  <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Select
              value={tailoringPromptId || "__none"}
              onValueChange={(v) => setTailoringPromptId(v === "__none" ? "" : v)}
            >
              <SelectTrigger className="rounded-none border-[#DEE2E6] h-10" data-testid="tailoring-prompt-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="rounded-none">
                <SelectItem value="__none">— No tailoring —</SelectItem>
                {tailoringPrompts.map((p) => (
                  <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <p className="mt-2 text-[11px] text-neutral-500 leading-snug">
            {mode === "classify"
              ? "Defines the categories. Build one in the Tailoring tab."
              : "Prepends a project-specific framing to every LLM call."}
          </p>
        </Panel>

        <Panel title="⑤ Run" testid="run-panel">
          {mode === "individual" && (
            <Button
              onClick={runIndividual}
              disabled={indLoading}
              data-testid="run-individual-btn"
              className="w-full rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-11"
            >
              {indLoading ? <Loader2 className="animate-spin mr-2" size={14} /> : <Play size={14} className="mr-2" />}
              Analyze requirement
            </Button>
          )}
          {mode === "set" && (
            <Button
              onClick={runSet}
              disabled={setLoading || !selectedSetId}
              data-testid="run-set-btn"
              className="w-full rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-11"
            >
              {setLoading ? <Loader2 className="animate-spin mr-2" size={14} /> : <Sparkles size={14} className="mr-2" />}
              Score full set
            </Button>
          )}
          {mode === "classify" && (
            <Button
              onClick={runClassify}
              disabled={classifyLoading || !selectedSetId}
              data-testid="run-classify-btn"
              className="w-full rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-11"
            >
              {classifyLoading ? <Loader2 className="animate-spin mr-2" size={14} /> : <Tag size={14} className="mr-2" />}
              Classify set
            </Button>
          )}
          {mode === "ask" && (
            <div className="text-[11px] text-neutral-500">
              Pick a set, type your question in the right panel and press Enter.
            </div>
          )}
        </Panel>
      </div>

      {/* RIGHT — Results */}
      <div className="col-span-12 lg:col-span-8 space-y-6">
        {mode === "individual" && (
          <Panel title="Result — Individual" testid="individual-result-panel">
            {!individualResult && !indLoading && (
              <div className="text-sm text-neutral-500 italic">Run an analysis to see results.</div>
            )}
            {indLoading && (
              <div className="flex items-center gap-2 text-sm text-neutral-600">
                <Loader2 className="animate-spin" size={14} /> Scoring requirement…
              </div>
            )}
            {individualResult && (
              <div className="space-y-5">
                <div className="flex items-start justify-between gap-4 border-b border-[#DEE2E6] pb-4">
                  <div>
                    <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                      Overall Score
                    </div>
                    <div className="mt-1 flex items-baseline gap-3">
                      <span className="font-display font-black text-5xl tracking-tighter">
                        {individualResult.overall_score}
                      </span>
                      <ScoreBadge value={individualResult.overall_score} />
                    </div>
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-neutral-500 mb-1.5">
                    Summary
                  </div>
                  <p className="text-sm text-neutral-800">{individualResult.summary}</p>
                </div>
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-neutral-500 mb-2">
                    INCOSE Rule Breakdown
                  </div>
                  <RuleBreakdown rules={individualResult.rules} />
                </div>
                <div className="bg-[#F1F3F5] border-l-2 border-[#099250] p-4">
                  <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-[#099250] mb-1.5">
                    Proposed fix
                  </div>
                  <p className="text-sm font-mono text-[#0A0D14]" data-testid="proposed-fix">
                    {individualResult.proposed_fix}
                  </p>
                </div>
              </div>
            )}
          </Panel>
        )}

        {mode === "set" && (
          <>
            {setResult && (
              <Panel title="Set Summary" testid="set-summary-panel">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-[#DEE2E6] border border-[#DEE2E6]">
                  <Metric label="Total" value={setResult.results.length} />
                  <Metric
                    label="Average"
                    value={`${setResult.average_score}/100`}
                  />
                  <Metric
                    label="Failing (<85)"
                    value={setResult.results.filter((r) => r.overall_score < 85).length}
                    danger
                  />
                  <Metric
                    label="Inconsistencies"
                    value={setResult.inconsistencies.length}
                    warn
                  />
                </div>
              </Panel>
            )}

            {setResult?.inconsistencies?.length > 0 && (
              <Panel
                title="Internal Inconsistencies"
                testid="inconsistencies-panel"
                action={
                  <Badge className="rounded-none bg-[#F5A623] text-black font-mono text-[10px]">
                    {setResult.inconsistencies.length}
                  </Badge>
                }
              >
                <div className="space-y-3">
                  {setResult.inconsistencies.map((inc, i) => (
                    <div
                      key={i}
                      data-testid={`inconsistency-${i}`}
                      className="border border-[#F5A623] bg-[#FFFBF0] p-4"
                    >
                      <div className="flex items-center gap-2 mb-2">
                        <AlertTriangle size={14} className="text-[#F5A623]" />
                        <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-[#F5A623]">
                          {inc.type}
                        </span>
                        <span className="font-mono text-xs text-neutral-700">
                          {inc.requirement_ids?.join(" ↔ ")}
                        </span>
                      </div>
                      <p className="text-sm text-neutral-800">{inc.explanation}</p>
                      {inc.proposed_resolution && (
                        <p className="mt-2 text-xs font-mono text-[#099250]">
                          → {inc.proposed_resolution}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            <Panel title="Per-Requirement Results" testid="results-panel">
              {!setResult && !setLoading && (
                <div className="text-sm text-neutral-500 italic">Run the set analysis to see results.</div>
              )}
              {setLoading && (
                <div className="flex items-center gap-2 text-sm text-neutral-600">
                  <Loader2 className="animate-spin" size={14} /> Scoring each requirement…
                </div>
              )}
              {setResult && (
                <Table data-testid="results-table">
                  <TableHeader>
                    <TableRow className="border-[#DEE2E6]">
                      <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-28">ID</TableHead>
                      <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Requirement</TableHead>
                      <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-32">Score</TableHead>
                      <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-20">Flags</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sortedResults.map((r) => {
                      const needsExpand = r.overall_score < 85 || r.has_inconsistency;
                      return (
                        <RequirementRow key={r.requirement_id} r={r} expandable={needsExpand} />
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </Panel>
          </>
        )}

        {mode === "classify" && (
          <>
            <Panel
              title="Classification result"
              testid="classify-result-panel"
              action={
                classifyResult && (
                  <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                    {classifyResult.results.length} classified
                  </span>
                )
              }
            >
              {!classifyResult && !classifyLoading && (
                <div className="text-sm text-neutral-500 italic">
                  Pick a classifier prompt and a set, then click <span className="font-mono">Classify set</span>.
                </div>
              )}
              {classifyLoading && (
                <div className="flex items-center gap-2 text-sm text-neutral-600">
                  <Loader2 className="animate-spin" size={14} /> Classifying requirements…
                </div>
              )}
              {classifyResult && (
                <div className="space-y-5">
                  <div>
                    <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500 mb-2">
                      Category distribution
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(classifyResult.distribution).map(([cat, n]) => (
                        <div
                          key={cat}
                          className="border border-[#DEE2E6] px-3 py-1.5 bg-[#F1F3F5]"
                          data-testid={`dist-${cat}`}
                        >
                          <span className="text-xs font-medium text-[#0A0D14]">{cat}</span>
                          <span className="ml-2 font-mono text-[11px] text-neutral-500">×{n}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <Table data-testid="classify-table">
                    <TableHeader>
                      <TableRow className="border-[#DEE2E6]">
                        <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-28">ID</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Requirement</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-40">Category</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-20">Conf.</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {classifyResult.results.map((r) => (
                        <TableRow key={r.requirement_id} className="border-[#DEE2E6] align-top" data-testid={`classify-row-${r.requirement_id}`}>
                          <TableCell className="font-mono text-xs text-[#0A0D14]">{r.requirement_id}</TableCell>
                          <TableCell className="text-sm">
                            <div>{r.requirement_text}</div>
                            {r.rationale && (
                              <div className="text-[11px] text-neutral-500 mt-1 italic">{r.rationale}</div>
                            )}
                          </TableCell>
                          <TableCell>
                            <div className="space-y-1">
                              <span className="px-1.5 py-0.5 text-[10px] font-mono font-bold bg-[#002FA7] text-white">
                                {r.primary_category}
                              </span>
                              {r.secondary_categories?.length > 0 && (
                                <div className="flex flex-wrap gap-1">
                                  {r.secondary_categories.map((c, i) => (
                                    <span key={i} className="px-1.5 py-0.5 text-[10px] font-mono bg-[#F1F3F5] border border-[#DEE2E6]">
                                      {c}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="font-mono text-xs">{r.confidence ?? "—"}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </Panel>
          </>
        )}

        {mode === "ask" && (
          <Panel
            title="Ask the requirements set"
            testid="ask-panel"
            action={
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                {sets.find((s) => s.id === selectedSetId)?.name || "no set"}
              </span>
            }
          >
            <div className="border border-[#DEE2E6] h-[420px] flex flex-col">
              <div className="flex-1 overflow-y-auto p-4 space-y-3" data-testid="chat-history">
                {chat.length === 0 && (
                  <div className="text-sm text-neutral-500 italic flex items-center gap-2">
                    <MessageSquareText size={14} />
                    Try: {`"Summarise the security-related requirements"`} or {`"What's missing?"`}
                  </div>
                )}
                {chat.map((m, i) => (
                  <div
                    key={i}
                    className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[80%] px-3 py-2 text-sm ${
                        m.role === "user"
                          ? "bg-[#0A0D14] text-white"
                          : "bg-[#F1F3F5] text-[#0A0D14] border border-[#DEE2E6]"
                      }`}
                      data-testid={`chat-msg-${i}`}
                    >
                      <div className="text-[10px] font-mono uppercase tracking-[0.2em] opacity-70 mb-1">
                        {m.role}
                      </div>
                      <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
                    </div>
                  </div>
                ))}
                {askLoading && (
                  <div className="flex items-center gap-2 text-sm text-neutral-500">
                    <Loader2 className="animate-spin" size={14} /> Thinking…
                  </div>
                )}
              </div>
              <div className="border-t border-[#DEE2E6] p-3 flex gap-2">
                <Input
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !askLoading && ask()}
                  placeholder="Ask a question about this requirements set…"
                  className="rounded-none border-[#DEE2E6] h-10"
                  data-testid="ask-input"
                />
                <Button
                  onClick={ask}
                  disabled={askLoading || !selectedSetId}
                  className="rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-10"
                  data-testid="ask-btn"
                >
                  Send
                </Button>
              </div>
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}

const Metric = ({ label, value, danger, warn }) => (
  <div className="bg-white p-4">
    <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">{label}</div>
    <div
      className={`mt-1 font-display font-black text-3xl tracking-tighter ${
        danger ? "text-[#E52B20]" : warn ? "text-[#F5A623]" : "text-[#0A0D14]"
      }`}
    >
      {value}
    </div>
  </div>
);

const RequirementRow = ({ r, expandable }) => {
  const [open, setOpen] = useState(false);
  const c = scoreColor(r.overall_score);
  return (
    <>
      <TableRow
        className={`border-[#DEE2E6] ${expandable ? "cursor-pointer" : ""} ${
          r.overall_score < 85 ? "bg-[#FFF5F5]" : ""
        } hover:bg-neutral-50 transition-colors`}
        onClick={() => expandable && setOpen(!open)}
        data-testid={`row-${r.requirement_id}`}
      >
        <TableCell className="font-mono text-xs text-[#0A0D14] align-top">{r.requirement_id}</TableCell>
        <TableCell className="text-sm align-top">{r.requirement_text}</TableCell>
        <TableCell className="align-top">
          <div className="flex items-center gap-2">
            <span className={`px-2 py-0.5 text-[11px] font-mono font-bold ${c.bg} ${c.text}`}>
              {r.overall_score}
            </span>
          </div>
        </TableCell>
        <TableCell className="align-top">
          <div className="flex flex-col gap-1">
            {r.has_inconsistency && (
              <span className="text-[9px] font-mono uppercase tracking-[0.15em] text-[#F5A623]">
                INC
              </span>
            )}
            {expandable && (
              <span className="text-[9px] font-mono uppercase tracking-[0.15em] text-neutral-500">
                {open ? "▾ Hide" : "▸ Show"}
              </span>
            )}
          </div>
        </TableCell>
      </TableRow>
      {open && expandable && (
        <TableRow className="border-[#DEE2E6] bg-[#FAFBFC]">
          <TableCell colSpan={4} className="p-5">
            <div className="space-y-4">
              <div>
                <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500 mb-1.5">
                  Summary
                </div>
                <p className="text-sm text-neutral-800">{r.summary}</p>
              </div>
              <div>
                <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500 mb-2">
                  INCOSE rule findings
                </div>
                <RuleBreakdown rules={r.rules} />
              </div>
              <div className="bg-white border-l-2 border-[#099250] p-4">
                <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-[#099250] mb-1.5">
                  Proposed fix
                </div>
                <p className="text-sm font-mono text-[#0A0D14]">{r.proposed_fix}</p>
              </div>
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
};
