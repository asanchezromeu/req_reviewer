import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  FileUp,
  Loader2,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";

const newRequirement = (index) => ({
  id: `REQ-${String(index).padStart(3, "0")}`,
  text: "",
  source: "",
});

const asArray = (value) => (Array.isArray(value) ? value : []);

const formatNumber = (value, fallback = "n/a") => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : fallback;
};

function IndexBadge({ index }) {
  const state = index?.state || "empty";
  const labels = {
    empty: "No requirements",
    pending: "Index pending",
    indexing: `Indexing ${index?.indexed || 0}/${index?.total || 0}`,
    ready: `Index ready · ${index?.indexed || 0}`,
    error: "Index error",
  };
  const color = {
    empty: "bg-neutral-100 text-neutral-600",
    pending: "bg-amber-50 text-amber-800",
    indexing: "bg-blue-50 text-blue-800",
    ready: "bg-emerald-50 text-emerald-800",
    error: "bg-red-50 text-red-800",
  };

  return (
    <div
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold ${color[state]}`}
      title={index?.error || `Embedding model: ${index?.model || "embeddinggemma"}`}
    >
      {state === "indexing" ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : state === "ready" ? (
        <Check className="h-3.5 w-3.5" />
      ) : (
        <span className="h-2 w-2 rounded-full bg-current opacity-70" />
      )}
      {labels[state]}
    </div>
  );
}

export default function ShowcaseWorkspace() {
  const [requirements, setRequirements] = useState([]);
  const [index, setIndex] = useState({ state: "empty", total: 0, indexed: 0 });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");
  const [summaryMode, setSummaryMode] = useState(false);
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState(null);
  const [showSources, setShowSources] = useState(false);
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [embeddingModel, setEmbeddingModel] = useState("embeddinggemma");
  const [llmModel, setLlmModel] = useState("gemma3:1b");
  const [minSimilarity, setMinSimilarity] = useState("0.30");
  const fileRef = useRef(null);

  const validRequirements = useMemo(
    () => requirements.filter((item) => item.id.trim() && item.text.trim()),
    [requirements],
  );
  const requirementMatches = useMemo(() => {
    if (result?.mode !== "requirement") return [];
    if (Array.isArray(result.requirements)) return result.requirements;
    return result.requirement ? [result.requirement] : [];
  }, [result]);
  const summarySources = useMemo(
    () => (result?.mode === "summary" ? asArray(result.sources) : []),
    [result],
  );

  const refresh = async () => {
    const data = await api.showcaseRequirements();
    setRequirements(data.requirements);
    setIndex(data.index);
    setDirty(false);
  };

  useEffect(() => {
    refresh().catch((error) => toast.error(error.message));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      api.showcaseIndexStatus().then(setIndex).catch(() => {});
    }, 1500);
    return () => window.clearInterval(timer);
  }, []);

  const updateRequirement = (position, field, value) => {
    setRequirements((current) =>
      current.map((item, indexValue) =>
        indexValue === position ? { ...item, [field]: value } : item,
      ),
    );
    setDirty(true);
  };

  const addRequirement = () => {
    const used = new Set(requirements.map((item) => item.id));
    let next = requirements.length + 1;
    while (used.has(`REQ-${String(next).padStart(3, "0")}`)) next += 1;
    setRequirements((current) => [...current, newRequirement(next)]);
    setDirty(true);
  };

  const removeRequirement = (position) => {
    setRequirements((current) => current.filter((_, indexValue) => indexValue !== position));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const data = await api.saveShowcaseRequirements({
        requirements: validRequirements,
        embedding_model: embeddingModel,
        ollama_url: ollamaUrl,
      });
      setRequirements(data.requirements);
      setIndex(data.index);
      setDirty(false);
      toast.success("Requirements saved. Embeddings are refreshing in the background.");
    } catch (error) {
      toast.error(error.response?.data?.detail || "Could not save requirements");
    } finally {
      setSaving(false);
    }
  };

  const importFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const data = await api.importShowcaseRequirements(
        file,
        embeddingModel,
        ollamaUrl,
      );
      setRequirements(data.requirements);
      setIndex(data.index);
      setDirty(false);
      setResult(null);
      toast.success(`Imported ${data.requirements.length} requirements`);
    } catch (error) {
      toast.error(error.response?.data?.detail || "Could not import the file");
    } finally {
      event.target.value = "";
    }
  };

  const runSearch = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setResult(null);
    setShowSources(false);
    try {
      const data = await api.searchShowcase({
        query,
        mode: summaryMode ? "summary" : "requirement",
        embedding_model: embeddingModel,
        llm_model: llmModel,
        ollama_url: ollamaUrl,
        min_similarity: Number(minSimilarity) || 0.30,
      });
      setResult(data);
    } catch (error) {
      toast.error(error.response?.data?.detail || "Search failed");
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-7">
      <section className="rounded-xl border bg-white p-5 shadow-sm">
        <form onSubmit={runSearch} className="space-y-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
            <div className="relative flex-1">
              <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-neutral-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="h-14 pl-12 pr-4 text-base"
                placeholder={
                  summaryMode
                    ? "What does management need to understand?"
                    : "Describe the requirement you are looking for"
                }
              />
            </div>
            <div className="flex min-w-fit items-center justify-center gap-3 rounded-lg border bg-neutral-50 px-4 py-3">
              <span className={!summaryMode ? "font-semibold text-teal-800" : "text-neutral-500"}>
                Requirement
              </span>
              <Switch
                checked={summaryMode}
                onCheckedChange={setSummaryMode}
                aria-label="Toggle between requirement search and summary"
              />
              <span className={summaryMode ? "font-semibold text-teal-800" : "text-neutral-500"}>
                Summary
              </span>
            </div>
            <Button className="h-14 px-7" disabled={searching || !query.trim()}>
              {searching ? <Loader2 className="animate-spin" /> : summaryMode ? <Sparkles /> : <Search />}
              {summaryMode ? "Summarize" : "Find"}
            </Button>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-neutral-500">
            <span>
              {summaryMode
                ? `Uses up to 10 strong matches; weak scores below ${minSimilarity} are discarded.`
                : `Returns all requirements matching at least ${minSimilarity} relevance score.`}
            </span>
            <IndexBadge index={index} />
          </div>
        </form>

        {result?.mode === "requirement" && (
          <div className="mt-5 border-t pt-5">
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-teal-700">
              Requirement matches
            </div>
            {result.message ? (
              <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
                {result.message}
              </div>
            ) : requirementMatches.length === 0 ? (
              <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
                No requirement was similar enough to the query.
              </div>
            ) : (
              <div className="mt-2 divide-y overflow-hidden rounded-lg border bg-slate-950 text-white">
                {requirementMatches.map((requirement) => (
                  <div key={requirement.id} className="p-5">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="font-mono text-sm text-teal-300">{requirement.id}</span>
                      <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs text-slate-300">
                        score {formatNumber(requirement.score ?? requirement.similarity)}
                      </span>
                    </div>
                    <div className="mt-2 text-lg leading-relaxed">{requirement.text}</div>
                    {requirement.source && (
                      <div className="mt-2 text-xs text-slate-400">{requirement.source}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div className="mt-2 text-xs text-neutral-500">
              Discarded {result.discarded ?? 0} weak matches below {formatNumber(result.threshold)} score.
            </div>
          </div>
        )}

        {result?.mode === "summary" && (
          <div className="mt-5 border-t pt-5">
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-teal-700">
              Executive summary
            </div>
            <div className="mt-2 whitespace-pre-wrap text-base leading-7 text-slate-800">
              {result.answer}
            </div>
            <div className="mt-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              Used {summarySources.length} strong match{summarySources.length === 1 ? "" : "es"}.
              {" "}Discarded {result.discarded ?? 0} weak match{result.discarded === 1 ? "" : "es"}
              {" "}below {formatNumber(result.threshold)} score.
              {result.ambiguous && summarySources.length > 1 ? " Multiple relevant requirements were found." : ""}
              {result.llm_fallback ? " Returned fast safe summary without local text generation." : ""}
            </div>
            <button
              type="button"
              onClick={() => setShowSources((value) => !value)}
              disabled={summarySources.length === 0}
              className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-teal-700"
            >
              {showSources ? <ChevronUp /> : <ChevronDown />}
              {showSources ? "Hide evidence" : `Show ${summarySources.length} strong matches`}
            </button>
            {showSources && (
              <div className="mt-3 divide-y rounded-lg border">
                {summarySources.map((source) => (
                  <div key={source.id} className="p-3">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs font-semibold text-teal-700">
                        {source.id}
                      </span>
                      <span className="rounded-full bg-teal-50 px-2 py-0.5 text-[11px] text-teal-800">
                        score {formatNumber(source.score ?? source.similarity)}
                      </span>
                    </div>
                    <span className="text-sm text-neutral-700">{source.text}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <section className="rounded-xl border bg-white shadow-sm">
        <div className="flex flex-col gap-4 border-b p-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-xl font-bold text-slate-900">Requirements workspace</h2>
            <p className="mt-1 text-sm text-neutral-500">
              Edit locally, then save once to persist and refresh semantic search.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".json,.csv"
              onChange={importFile}
              className="hidden"
            />
            <Button variant="outline" onClick={() => fileRef.current?.click()}>
              <FileUp />
              Import JSON/CSV
            </Button>
            <Button variant="outline" onClick={addRequirement}>
              <Plus />
              Add requirement
            </Button>
            <Button onClick={save} disabled={saving || !dirty}>
              {saving ? <Loader2 className="animate-spin" /> : <Save />}
              Save changes
            </Button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse">
            <thead>
              <tr className="bg-neutral-50 text-left text-xs uppercase tracking-[0.12em] text-neutral-500">
                <th className="w-44 px-5 py-3 font-semibold">ID</th>
                <th className="px-5 py-3 font-semibold">Requirement</th>
                <th className="w-52 px-5 py-3 font-semibold">Source</th>
                <th className="w-16 px-5 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y">
              {requirements.map((requirement, position) => (
                <tr key={`${requirement.id}-${position}`} className="align-top">
                  <td className="p-4 pl-5">
                    <Input
                      value={requirement.id}
                      onChange={(event) => updateRequirement(position, "id", event.target.value)}
                      className="font-mono"
                    />
                  </td>
                  <td className="p-4">
                    <Textarea
                      value={requirement.text}
                      onChange={(event) => updateRequirement(position, "text", event.target.value)}
                      className="min-h-[76px] resize-y leading-relaxed"
                      placeholder="The system shall..."
                    />
                  </td>
                  <td className="p-4">
                    <Input
                      value={requirement.source || ""}
                      onChange={(event) => updateRequirement(position, "source", event.target.value)}
                      placeholder="Optional"
                    />
                  </td>
                  <td className="p-4 pr-5 text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => removeRequirement(position)}
                      aria-label={`Delete ${requirement.id}`}
                    >
                      <Trash2 className="text-neutral-500" />
                    </Button>
                  </td>
                </tr>
              ))}
              {requirements.length === 0 && (
                <tr>
                  <td colSpan="4" className="px-5 py-16 text-center text-neutral-500">
                    Import a requirements file or add the first requirement.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <details className="rounded-xl border bg-white px-5 py-4 text-sm shadow-sm">
        <summary className="cursor-pointer font-semibold text-slate-800">Demo runtime settings</summary>
        <div className="mt-4 grid gap-4 md:grid-cols-4">
          <label className="space-y-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Ollama URL
            </span>
            <Input value={ollamaUrl} onChange={(event) => setOllamaUrl(event.target.value)} />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Embedding model
            </span>
            <Input
              value={embeddingModel}
              onChange={(event) => setEmbeddingModel(event.target.value)}
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Summary model
            </span>
            <Input value={llmModel} onChange={(event) => setLlmModel(event.target.value)} />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Minimum score
            </span>
            <Input
              value={minSimilarity}
              onChange={(event) => setMinSimilarity(event.target.value)}
              inputMode="decimal"
            />
          </label>
        </div>
      </details>
    </div>
  );
}
