import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "sonner";
import { Loader2, RefreshCw, Trash2, Zap } from "lucide-react";
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

const OPENAI_FINETUNE_MODELS = [
  { id: "gpt-4o-mini-2024-07-18", label: "gpt-4o-mini (recommended)" },
  { id: "gpt-4o-2024-08-06", label: "gpt-4o" },
  { id: "gpt-3.5-turbo-0125", label: "gpt-3.5-turbo" },
];

const statusBadge = (s = "") => {
  const m = {
    queued: "bg-[#F1F3F5] text-[#0A0D14]",
    validating_files: "bg-[#002FA7] text-white",
    running: "bg-[#F5A623] text-black",
    succeeded: "bg-[#099250] text-white",
    failed: "bg-[#E52B20] text-white",
    cancelled: "bg-neutral-400 text-white",
    uploading_file: "bg-[#002FA7] text-white",
  };
  return m[s] || "bg-neutral-200 text-neutral-700";
};

export default function DistillationTab() {
  const [datasets, setDatasets] = useState([]);
  const [jobName, setJobName] = useState("My distillation run");
  const [datasetId, setDatasetId] = useState("");
  const [baseModel, setBaseModel] = useState("gpt-4o-mini-2024-07-18");
  const [epochs, setEpochs] = useState(3);
  const [apiKey, setApiKey] = useState("");
  const [starting, setStarting] = useState(false);
  const [jobs, setJobs] = useState([]);

  useEffect(() => {
    refresh();
  }, []);

  const refresh = async () => {
    try {
      const [ds, js] = await Promise.all([api.listDatasets(), api.listJobs()]);
      setDatasets(ds);
      setJobs(js);
      setDatasetId((prev) => prev || (ds[0]?.id ?? ""));
    } catch {
      toast.error("Failed to load");
    }
  };

  const start = async () => {
    if (!datasetId) {
      toast.error("Pick a dataset");
      return;
    }
    if (!apiKey.trim()) {
      toast.error("Provide your OpenAI API key");
      return;
    }
    setStarting(true);
    try {
      await api.startJob({
        name: jobName, dataset_id: datasetId, base_model: baseModel,
        openai_api_key: apiKey, n_epochs: Number(epochs) || 3,
      });
      toast.success("Fine-tune job submitted");
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to start job");
    } finally { setStarting(false); }
  };

  const refreshJob = async (id) => {
    if (!apiKey.trim()) {
      toast.error("Provide your OpenAI key to refresh");
      return;
    }
    try {
      await api.refreshJob(id, apiKey);
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Refresh failed");
    }
  };

  const removeJob = async (id) => {
    await api.deleteJob(id);
    await refresh();
  };

  return (
    <div className="grid grid-cols-12 gap-6" data-testid="distillation-tab">
      <div className="col-span-12 lg:col-span-5 space-y-6">
        <Panel title="Distillation Configuration" testid="distill-config">
          <div className="space-y-3">
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Job name
              </Label>
              <Input
                value={jobName}
                onChange={(e) => setJobName(e.target.value)}
                className="rounded-none mt-1.5 border-[#DEE2E6] h-10"
                data-testid="job-name"
              />
            </div>
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Base model (OpenAI fine-tunable)
              </Label>
              <Select value={baseModel} onValueChange={setBaseModel}>
                <SelectTrigger className="rounded-none mt-1.5 border-[#DEE2E6] h-10" data-testid="base-model-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="rounded-none">
                  {OPENAI_FINETUNE_MODELS.map((m) => (
                    <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Training dataset
              </Label>
              <Select value={datasetId} onValueChange={setDatasetId}>
                <SelectTrigger className="rounded-none mt-1.5 border-[#DEE2E6] h-10" data-testid="dataset-select">
                  <SelectValue placeholder="Pick a dataset" />
                </SelectTrigger>
                <SelectContent className="rounded-none">
                  {datasets.map((d) => (
                    <SelectItem key={d.id} value={d.id}>{d.name} · {d.sample_count}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {datasets.length === 0 && (
                <p className="text-[11px] text-neutral-500 mt-2">
                  Upload a dataset under the Training Data tab first.
                </p>
              )}
            </div>
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Epochs
              </Label>
              <Input
                type="number"
                min={1}
                max={10}
                value={epochs}
                onChange={(e) => setEpochs(e.target.value)}
                className="rounded-none mt-1.5 border-[#DEE2E6] h-10 font-mono"
                data-testid="epochs"
              />
            </div>
            <div>
              <Label className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-500">
                Your OpenAI API key
              </Label>
              <Input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
                className="rounded-none mt-1.5 border-[#DEE2E6] h-10 font-mono"
                data-testid="openai-key"
              />
              <p className="text-[10px] text-neutral-500 mt-1.5 leading-snug">
                Used directly to call OpenAI&apos;s <span className="font-mono">/v1/files</span> and{" "}
                <span className="font-mono">/v1/fine_tuning/jobs</span> endpoints. Not stored.
              </p>
            </div>
            <Button
              onClick={start}
              disabled={starting}
              className="w-full rounded-none bg-[#0A0D14] text-white hover:bg-[#212529] h-11"
              data-testid="start-distill-btn"
            >
              {starting ? <Loader2 className="animate-spin mr-2" size={14} /> : <Zap size={14} className="mr-2" />}
              Start fine-tuning
            </Button>
          </div>
        </Panel>
      </div>

      <div className="col-span-12 lg:col-span-7 space-y-6">
        <Panel
          title="Distillation Jobs"
          testid="jobs-panel"
          action={
            <button
              onClick={refresh}
              className="text-xs font-medium px-2.5 py-1 border border-[#DEE2E6] hover:bg-neutral-50 inline-flex items-center gap-1.5"
              data-testid="refresh-jobs-btn"
            >
              <RefreshCw size={12} /> Refresh list
            </button>
          }
        >
          {jobs.length === 0 ? (
            <div className="text-sm text-neutral-500 italic">No jobs yet.</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-[#DEE2E6]">
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Name</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Base</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Status</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase tracking-[0.2em] text-neutral-500">Job ID</TableHead>
                  <TableHead className="w-24"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((j) => (
                  <TableRow key={j.id} className="border-[#DEE2E6]" data-testid={`job-${j.id}`}>
                    <TableCell>
                      <div className="text-sm">{j.name}</div>
                      <div className="text-[10px] font-mono text-neutral-500">
                        {j.dataset_name}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{j.base_model}</TableCell>
                    <TableCell>
                      <span className={`px-2 py-0.5 text-[10px] font-mono font-bold ${statusBadge(j.status)}`}>
                        {(j.status || "").toUpperCase()}
                      </span>
                      {j.fine_tuned_model && (
                        <div className="text-[10px] font-mono text-[#099250] mt-1">{j.fine_tuned_model}</div>
                      )}
                      {j.error && <div className="text-[10px] text-[#E52B20] mt-1">{j.error}</div>}
                    </TableCell>
                    <TableCell className="font-mono text-[10px] text-neutral-500">
                      {j.openai_job_id || "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => refreshJob(j.id)}
                          className="text-neutral-500 hover:text-[#0A0D14] p-1"
                          data-testid={`refresh-job-${j.id}`}
                          title="Refresh status"
                        >
                          <RefreshCw size={13} />
                        </button>
                        <button
                          onClick={() => removeJob(j.id)}
                          className="text-neutral-400 hover:text-[#E52B20] p-1"
                          data-testid={`delete-job-${j.id}`}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Panel>

        <Panel title="What this does" testid="distill-help">
          <ul className="text-xs text-neutral-700 leading-relaxed list-disc pl-5 space-y-1.5">
            <li>Converts your dataset samples into OpenAI&apos;s chat JSONL format.</li>
            <li>Uploads the file to OpenAI as a <span className="font-mono">fine-tune</span> file.</li>
            <li>Creates an OpenAI fine-tuning job and tracks its ID here.</li>
            <li>Use {`"Refresh"`} to pull the latest status and the resulting fine-tuned model name.</li>
          </ul>
        </Panel>
      </div>
    </div>
  );
}
