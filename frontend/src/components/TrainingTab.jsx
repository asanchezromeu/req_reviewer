import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "sonner";
import { Loader2, Plus, Trash2, Upload, FileJson } from "lucide-react";
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

export default function TrainingTab() {
  const [label, setLabel] = useState("good");
  const [text, setText] = useState("");
  const [explanation, setExplanation] = useState("");
  const [corrected, setCorrected] = useState("");
  const [saving, setSaving] = useState(false);

  const [examples, setExamples] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    refresh();
  }, []);

  const refresh = async () => {
    try {
      const [ex, ds] = await Promise.all([api.listExamples(), api.listDatasets()]);
      setExamples(ex);
      setDatasets(ds);
    } catch {
      toast.error("Failed to load training data");
    }
  };

  const save = async () => {
    if (!text.trim()) {
      toast.error("Add a requirement text");
      return;
    }
    setSaving(true);
    try {
      await api.createExample({ label, requirement_text: text, explanation, corrected_text: corrected });
      toast.success("Example saved");
      setText(""); setExplanation(""); setCorrected("");
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Save failed");
    } finally { setSaving(false); }
  };

  const remove = async (id) => {
    await api.deleteExample(id);
    await refresh();
  };

  const onUpload = async (e) => {
    const inputEl = e.target;
    const f = inputEl.files && inputEl.files[0];
    if (!f) return;
    setUploading(true);
    try {
      const r = await api.uploadDataset(f);
      toast.success(`Dataset uploaded · ${r.sample_count} samples`);
      await refresh();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
      if (inputEl) inputEl.value = "";
    }
  };

  const removeDataset = async (id) => {
    await api.deleteDataset(id);
    await refresh();
  };

  return (
    <div className="grid grid-cols-12 gap-6" data-testid="training-tab">
      <div className="col-span-12 lg:col-span-5 space-y-6">
        <Panel title="Add Example" testid="add-example-panel">
          <div className="grid grid-cols-2 gap-px bg-[#DEE2E6] border border-[#DEE2E6] mb-4">
            {["good", "bad"].map((l) => (
              <button
                key={l}
                data-testid={`label-${l}`}
                onClick={() => setLabel(l)}
                className={`px-4 py-3 text-sm font-medium transition-colors ${
                  label === l
                    ? l === "good"
                      ? "bg-[#099250] text-white"
                      : "bg-[#E52B20] text-white"
                    : "bg-white text-neutral-600 hover:bg-neutral-50"
                }`}
              >
                {l === "good" ? "✓ Good example" : "✗ Bad example"}
              </button>
            ))}
          </div>
          <div className="space-y-3">
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Requirement text
              </Label>
              <Textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={4}
                placeholder="The system shall …"
                className="rounded-none mt-1.5 border-[#DEE2E6] font-mono text-xs"
                data-testid="example-text"
              />
            </div>
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Explanation (optional)
              </Label>
              <Textarea
                value={explanation}
                onChange={(e) => setExplanation(e.target.value)}
                rows={2}
                placeholder="Why is it good / bad?"
                className="rounded-none mt-1.5 border-[#DEE2E6] text-xs"
                data-testid="example-explanation"
              />
            </div>
            {label === "bad" && (
              <div>
                <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                  Corrected version (optional)
                </Label>
                <Textarea
                  value={corrected}
                  onChange={(e) => setCorrected(e.target.value)}
                  rows={2}
                  placeholder="A corrected, INCOSE-compliant version"
                  className="rounded-none mt-1.5 border-[#DEE2E6] font-mono text-xs"
                  data-testid="example-corrected"
                />
              </div>
            )}
            <Button
              onClick={save}
              disabled={saving}
              className="w-full rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-11"
              data-testid="save-example-btn"
            >
              {saving ? <Loader2 className="animate-spin mr-2" size={14} /> : <Plus size={14} className="mr-2" />}
              Save example
            </Button>
          </div>
        </Panel>

        <Panel
          title="Upload Training Dataset"
          testid="upload-dataset-panel"
          action={
            <label className="cursor-pointer inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 border border-[#DEE2E6] hover:bg-neutral-50 transition-colors">
              <Upload size={12} />
              {uploading ? "Uploading…" : "Upload .jsonl"}
              <input
                type="file"
                accept=".jsonl,.json,.csv"
                className="hidden"
                onChange={onUpload}
                data-testid="upload-dataset-input"
              />
            </label>
          }
        >
          <p className="text-xs text-neutral-600 leading-relaxed">
            Accepted: JSONL with{" "}
            <span className="font-mono">{`{"messages":[{role,content},...]}`}</span> per line,
            or CSV/JSON with <span className="font-mono">prompt</span>/
            <span className="font-mono">completion</span> columns.
            These datasets feed the Distillation step.
          </p>
        </Panel>
      </div>

      <div className="col-span-12 lg:col-span-7 space-y-6">
        <Panel
          title="Saved Examples"
          testid="examples-panel"
          action={
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
              {examples.length} total
            </span>
          }
        >
          {examples.length === 0 ? (
            <div className="text-sm text-neutral-500 italic">No examples yet.</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-[#DEE2E6]">
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-20">Label</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Text</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {examples.map((ex) => (
                  <TableRow key={ex.id} className="border-[#DEE2E6]" data-testid={`example-${ex.id}`}>
                    <TableCell>
                      <span className={`px-2 py-0.5 text-[10px] font-mono font-bold ${
                        ex.label === "good" ? "bg-[#099250] text-white" : "bg-[#E52B20] text-white"
                      }`}>
                        {ex.label.toUpperCase()}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm">
                      <div className="font-mono text-xs text-neutral-800">{ex.requirement_text}</div>
                      {ex.explanation && (
                        <div className="text-[11px] text-neutral-500 mt-1">{ex.explanation}</div>
                      )}
                      {ex.corrected_text && (
                        <div className="text-[11px] text-[#099250] mt-1 font-mono">→ {ex.corrected_text}</div>
                      )}
                    </TableCell>
                    <TableCell>
                      <button
                        onClick={() => remove(ex.id)}
                        className="text-neutral-400 hover:text-[#E52B20] transition-colors"
                        data-testid={`delete-example-${ex.id}`}
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

        <Panel
          title="Datasets"
          testid="datasets-panel"
          action={
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
              {datasets.length} total
            </span>
          }
        >
          {datasets.length === 0 ? (
            <div className="text-sm text-neutral-500 italic">No datasets uploaded yet.</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-[#DEE2E6]">
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Name</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-32">Samples</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500 w-48">Created</TableHead>
                  <TableHead className="w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {datasets.map((ds) => (
                  <TableRow key={ds.id} className="border-[#DEE2E6]" data-testid={`dataset-${ds.id}`}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <FileJson size={14} className="text-neutral-500" />
                        <span className="font-mono text-xs">{ds.name}</span>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{ds.sample_count}</TableCell>
                    <TableCell className="font-mono text-[10px] text-neutral-500">
                      {ds.created_at?.slice(0, 19).replace("T", " ")}
                    </TableCell>
                    <TableCell>
                      <button
                        onClick={() => removeDataset(ds.id)}
                        className="text-neutral-400 hover:text-[#E52B20] transition-colors"
                        data-testid={`delete-dataset-${ds.id}`}
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
      </div>
    </div>
  );
}
