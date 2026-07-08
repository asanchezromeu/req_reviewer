import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "sonner";
import { Loader2, Sparkles, Save, Trash2, Tag, Telescope } from "lucide-react";
import { api } from "@/lib/api";

const Panel = ({ title, children, action, testid }) => (
  <section className="bg-white border border-[#DEE2E6]" data-testid={testid}>
    <header className="flex items-center justify-between px-5 py-3 border-b border-[#DEE2E6]">
      <h3 className="text-xs font-mono uppercase tracking-[0.2em] text-neutral-500">{title}</h3>
      {action}
    </header>
    <div className="p-5">{children}</div>
  </section>
);

const KindPill = ({ kind }) => (
  <span
    className={`px-2 py-0.5 text-[10px] font-mono font-bold ${
      kind === "classifier" ? "bg-[#002FA7] text-white" : "bg-[#0A0D14] text-white"
    }`}
  >
    {kind === "classifier" ? <Tag size={9} className="inline mr-1" /> : <Telescope size={9} className="inline mr-1" />}
    {kind.toUpperCase()}
  </span>
);

export default function TailoringTab({ models }) {
  const [kind, setKind] = useState("tailoring");
  const [sourceType, setSourceType] = useState("online");
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState("gpt-4o-mini");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [projectDescription, setProjectDescription] = useState("");
  const [categoriesHint, setCategoriesHint] = useState("");
  const [generating, setGenerating] = useState(false);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [categories, setCategories] = useState("");
  const [saving, setSaving] = useState(false);

  const [prompts, setPrompts] = useState([]);

  useEffect(() => { refresh(); }, []);

  const refresh = async () => {
    try {
      const list = await api.listPrompts();
      setPrompts(list);
    } catch { toast.error("Failed to load prompts"); }
  };

  const generate = async () => {
    if (!projectDescription.trim()) {
      toast.error("Describe your project first");
      return;
    }
    setGenerating(true);
    try {
      const r = await api.generatePrompt({
        kind,
        project_description: projectDescription,
        provider,
        model,
        ollama_url: sourceType === "local" ? ollamaUrl : undefined,
        categories_hint: categoriesHint.trim()
          ? categoriesHint.split(",").map((s) => s.trim()).filter(Boolean)
          : undefined,
      });
      setName(r.name || "");
      setDescription(r.description || "");
      setSystemPrompt(r.system_prompt || "");
      setCategories((r.categories || []).join(", "));
      toast.success("Draft prompt generated — review and save");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Generation failed");
    } finally { setGenerating(false); }
  };

  const save = async () => {
    if (!name.trim() || !systemPrompt.trim()) {
      toast.error("Name and system prompt are required");
      return;
    }
    setSaving(true);
    try {
      const cats = categories.trim()
        ? categories.split(",").map((s) => s.trim()).filter(Boolean)
        : [];
      await api.createPrompt({
        name, kind, system_prompt: systemPrompt, description, categories: cats,
      });
      toast.success("Prompt saved to library");
      setName(""); setDescription(""); setSystemPrompt(""); setCategories("");
      setProjectDescription(""); setCategoriesHint("");
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Save failed");
    } finally { setSaving(false); }
  };

  const remove = async (id) => {
    await api.deletePrompt(id);
    await refresh();
  };

  const modelList = models?.[provider] || [];

  return (
    <div className="grid grid-cols-12 gap-6" data-testid="tailoring-tab">
      <div className="col-span-12 lg:col-span-7 space-y-6">
        <Panel title="① Kind" testid="kind-panel">
          <div className="grid grid-cols-2 gap-px bg-[#DEE2E6] border border-[#DEE2E6]">
            {[
              { v: "tailoring", l: "Project Tailoring", desc: "Frames INCOSE analysis with project context" },
              { v: "classifier", l: "Classifier", desc: "Defines categories for requirement classification" },
            ].map((k) => (
              <button
                key={k.v}
                data-testid={`kind-${k.v}`}
                onClick={() => setKind(k.v)}
                className={`p-4 text-left transition-colors ${
                  kind === k.v ? "bg-[#0A0D14] text-white" : "bg-white text-neutral-700 hover:bg-neutral-50"
                }`}
              >
                <div className="text-sm font-medium">{k.l}</div>
                <div className={`text-[11px] mt-0.5 ${kind === k.v ? "text-neutral-300" : "text-neutral-500"}`}>
                  {k.desc}
                </div>
              </button>
            ))}
          </div>
        </Panel>

        <Panel
          title="② AI-assisted generator"
          testid="generator-panel"
          action={
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
              Optional · describe & generate
            </span>
          }
        >
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
                    onClick={() => {
                      setSourceType(option.value);
                      const nextProvider = option.value === "local" ? "ollama" : "openai";
                      setProvider(nextProvider);
                      setModel((models?.[nextProvider] || [])[0]?.id || "");
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
                <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">Provider</Label>
                <Select
                  value={provider}
                  onValueChange={(v) => { setProvider(v); setModel((models?.[v] || [])[0]?.id || ""); }}
                >
                  <SelectTrigger className="rounded-none mt-1.5 border-[#DEE2E6] h-10" data-testid="gen-provider">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="rounded-none">
                    {sourceType === "local" ? (
                      <SelectItem value="ollama">Ollama</SelectItem>
                    ) : (
                      <>
                        <SelectItem value="openai">OpenAI</SelectItem>
                        <SelectItem value="anthropic">Anthropic</SelectItem>
                        <SelectItem value="gemini">Gemini</SelectItem>
                      </>
                    )}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">Model</Label>
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger className="rounded-none mt-1.5 border-[#DEE2E6] h-10" data-testid="gen-model">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="rounded-none">
                    {modelList.map((m) => (
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
                  placeholder="http://localhost:11434"
                  className="mt-1.5 h-10"
                  data-testid="gen-ollama-url"
                />
              </div>
            )}
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Describe your project / domain / standards
              </Label>
              <Textarea
                value={projectDescription}
                onChange={(e) => setProjectDescription(e.target.value)}
                rows={4}
                placeholder="e.g. Avionics flight-control software. Safety-critical, DO-178C DAL-A. Requirements must be deterministic, bounded WCET, and verifiable by formal methods or full MC/DC test coverage."
                className="rounded-none mt-1.5 border-[#DEE2E6] text-sm"
                data-testid="project-description"
              />
            </div>
            {kind === "classifier" && (
              <div>
                <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                  Category hints (comma-separated, optional)
                </Label>
                <Input
                  value={categoriesHint}
                  onChange={(e) => setCategoriesHint(e.target.value)}
                  placeholder="Functional, Performance, Safety, Interface, …"
                  className="rounded-none mt-1.5 border-[#DEE2E6] h-10 text-sm"
                  data-testid="categories-hint"
                />
              </div>
            )}
            <Button
              onClick={generate}
              disabled={generating}
              className="w-full rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-11"
              data-testid="generate-prompt-btn"
            >
              {generating ? <Loader2 className="animate-spin mr-2" size={14} /> : <Sparkles size={14} className="mr-2" />}
              Generate draft prompt
            </Button>
          </div>
        </Panel>

        <Panel
          title="③ Review / paste / save"
          testid="save-panel"
          action={
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
              {kind === "classifier" ? "Saves with categories" : "Plain tailoring prompt"}
            </span>
          }
        >
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">Name</Label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="DO-178C avionics"
                  className="rounded-none mt-1.5 border-[#DEE2E6] h-10"
                  data-testid="prompt-name"
                />
              </div>
              <div>
                <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                  Description
                </Label>
                <Input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="one-line summary"
                  className="rounded-none mt-1.5 border-[#DEE2E6] h-10"
                  data-testid="prompt-description"
                />
              </div>
            </div>
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                System prompt
              </Label>
              <Textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={8}
                placeholder="Paste a system prompt here, or click Generate above. This text is prepended to the analyzer/classifier system message."
                className="rounded-none mt-1.5 border-[#DEE2E6] font-mono text-xs"
                data-testid="system-prompt"
              />
            </div>
            {kind === "classifier" && (
              <div>
                <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                  Categories (comma-separated)
                </Label>
                <Input
                  value={categories}
                  onChange={(e) => setCategories(e.target.value)}
                  placeholder="Functional, Performance, Safety, Interface"
                  className="rounded-none mt-1.5 border-[#DEE2E6] h-10 text-sm"
                  data-testid="prompt-categories"
                />
              </div>
            )}
            <Button
              onClick={save}
              disabled={saving}
              className="w-full rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-11"
              data-testid="save-prompt-btn"
            >
              {saving ? <Loader2 className="animate-spin mr-2" size={14} /> : <Save size={14} className="mr-2" />}
              Save to library
            </Button>
          </div>
        </Panel>
      </div>

      <div className="col-span-12 lg:col-span-5 space-y-6">
        <Panel
          title="Prompt library"
          testid="library-panel"
          action={
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
              {prompts.length} saved
            </span>
          }
        >
          {prompts.length === 0 ? (
            <div className="text-sm text-neutral-500 italic">
              No prompts saved yet. Generate or paste one on the left, then save.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-[#DEE2E6]">
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Kind</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Name</TableHead>
                  <TableHead className="w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {prompts.map((p) => (
                  <TableRow key={p.id} className="border-[#DEE2E6] align-top" data-testid={`prompt-${p.id}`}>
                    <TableCell><KindPill kind={p.kind} /></TableCell>
                    <TableCell>
                      <div className="font-medium text-sm">{p.name}</div>
                      {p.description && (
                        <div className="text-[11px] text-neutral-500 mt-0.5">{p.description}</div>
                      )}
                      {p.categories?.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {p.categories.map((c) => (
                            <span key={c} className="px-1.5 py-0.5 text-[10px] font-mono bg-[#F1F3F5] border border-[#DEE2E6]">
                              {c}
                            </span>
                          ))}
                        </div>
                      )}
                      <details className="mt-2">
                        <summary className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-500 cursor-pointer hover:text-[#0A0D14]">
                          View prompt
                        </summary>
                        <pre className="mt-2 text-[11px] font-mono whitespace-pre-wrap bg-[#F1F3F5] border border-[#DEE2E6] p-3 text-neutral-800 max-h-48 overflow-auto">
{p.system_prompt}
                        </pre>
                      </details>
                    </TableCell>
                    <TableCell>
                      <button
                        onClick={() => remove(p.id)}
                        className="text-neutral-400 hover:text-[#E52B20] transition-colors"
                        data-testid={`delete-prompt-${p.id}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Panel>

        <Panel title="How it&apos;s used" testid="how-used">
          <ul className="text-xs text-neutral-700 leading-relaxed list-disc pl-5 space-y-1.5">
            <li><b>Tailoring</b> prompts are prepended to the INCOSE analyzer / Q&amp;A — pick one in the Analyze tab to bias scoring to your project context.</li>
            <li><b>Classifier</b> prompts power the new Classify mode in the Analyze tab — they define the categories every requirement gets bucketed into.</li>
          </ul>
        </Panel>
      </div>
    </div>
  );
}
