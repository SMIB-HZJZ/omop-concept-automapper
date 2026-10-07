import { useQueryClient } from "@tanstack/react-query";
import { Bot, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useMeta, useStartAutomap, useWorkflows } from "@/api/queries";
import type { Task } from "@/api/types";
import { MultiSelect } from "@/components/MultiSelect";
import { TaskProgress } from "@/components/TaskProgress";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { usePersistentState } from "@/hooks/usePersistentState";
import { useLatestTask } from "@/hooks/useTasks";

export const DEFAULT_PIPELINE = "__default__";

export function PipelineSelect({ value, onChange, id }: { value: string; onChange: (v: string) => void; id?: string }) {
  const workflows = useWorkflows();
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={DEFAULT_PIPELINE}>Default pipeline</SelectItem>
        {(workflows.data?.pipelines ?? []).map((p) => (
          <SelectItem key={p.name} value={p.name}>
            {p.name}
            {p.is_default ? " (default)" : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function AutomapResult({ result }: { result: Record<string, unknown> }) {
  const n = (k: string) => Number(result[k] ?? 0).toLocaleString();
  const outcomes = (result.outcomes ?? {}) as Record<string, number>;
  return (
    <div className="space-y-1 text-sm">
      <div>
        Processed {n("processed")} of {n("total_concepts")} concepts · mapped {n("mapped")} · queued for review{" "}
        {n("queued")} · skipped {n("skipped")} · errors {n("errors")}
      </div>
      {Object.keys(outcomes).length > 0 && (
        <div className="text-muted-foreground text-xs">
          Outcomes: {Object.entries(outcomes).map(([k, v]) => `${k} ${v}`).join(", ")}
        </div>
      )}
      {Number(result.queued ?? 0) > 0 && (
        <Link to="/review" className="text-primary underline">
          Review the suggestions
        </Link>
      )}
    </div>
  );
}

export function useAutomapControls({ vocabularyId, isDrug }: { vocabularyId: number; isDrug: boolean }) {
  const qc = useQueryClient();
  const meta = useMeta();
  const start = useStartAutomap(vocabularyId);
  const [domains, setDomains] = usePersistentState<string[]>("automap-domains", []);
  const [threshold, setThreshold] = useState(8);
  const [drugSpecific, setDrugSpecific] = useState(isDrug);
  const [mode, setMode] = useState<"review" | "commit">("review");
  const [pipeline, setPipeline] = useState(DEFAULT_PIPELINE);
  const [limit, setLimit] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => setDrugSpecific(isDrug), [isDrug, vocabularyId]);

  const latest = useLatestTask(
    "automap.run",
    (t: Task) => (t.params as { vocabulary_id?: number } | null)?.vocabulary_id === vocabularyId,
  );

  const submit = () => {
    if (domains.length === 0) {
      toast.warning("Please select at least one target domain for auto-mapping.");
      return;
    }
    start.mutate(
      {
        domains,
        threshold,
        drug_specific: drugSpecific,
        mode,
        pipeline: pipeline === DEFAULT_PIPELINE ? null : pipeline,
        limit: limit ? Number(limit) : null,
      },
      {
        onSuccess: () => {
          toast.success("Auto-mapping started");
          setOpen(false);
          void qc.invalidateQueries({ queryKey: ["tasks"] });
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  };

  return {
    trigger: (
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" className="gap-2">
            <Settings2 className="size-4" /> Auto map settings
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" side="bottom" collisionPadding={12} className="max-h-(--radix-popover-content-available-height) w-96 space-y-4 overflow-y-auto">
          <div className="font-medium">Auto-mapping configuration</div>
          <div className="grid gap-1.5">
            <Label>Target domains</Label>
            <MultiSelect options={meta.data?.domains ?? []} value={domains} onChange={setDomains} placeholder="Select domains…" />
          </div>
          <div className="grid gap-2">
            <Label>
              Confidence threshold: <span className="tabular-nums">{threshold}</span>
            </Label>
            <Slider min={1} max={10} step={1} value={[threshold]} onValueChange={(v) => setThreshold(v[0])} />
            <p className="text-muted-foreground text-xs">Minimum confidence score (1–10) required to auto-map a concept.</p>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox id="drug-specific" checked={drugSpecific} onCheckedChange={(v) => setDrugSpecific(!!v)} />
            <Label htmlFor="drug-specific">Drug-specific mapping</Label>
          </div>
          {drugSpecific && (
            <Alert>
              <AlertDescription className="text-xs">
                ATC7 enhanced mapping: ATC7 codes are extracted from the start of source values (e.g. A10BA02) and used
                to limit the search space.
              </AlertDescription>
            </Alert>
          )}
          <div className="grid gap-1.5">
            <Label>Mode</Label>
            <RadioGroup value={mode} onValueChange={(v) => setMode(v as "review" | "commit")}>
              <div className="flex items-center gap-2">
                <RadioGroupItem value="review" id="mode-review" />
                <Label htmlFor="mode-review">Review queue (recommended)</Label>
              </div>
              <div className="flex items-center gap-2">
                <RadioGroupItem value="commit" id="mode-commit" />
                <Label htmlFor="mode-commit">Commit directly</Label>
              </div>
            </RadioGroup>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="automap-pipeline">Pipeline</Label>
            <PipelineSelect id="automap-pipeline" value={pipeline} onChange={setPipeline} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="automap-limit">Limit (for a trial run)</Label>
            <Input id="automap-limit" type="number" min={1} placeholder="All unmapped concepts" value={limit} onChange={(e) => setLimit(e.target.value)} />
          </div>
          <Button className="w-full gap-2" onClick={submit} disabled={start.isPending}>
            <Bot className="size-4" /> Start auto mapping
          </Button>
        </PopoverContent>
      </Popover>
    ),
    progress: latest.data ? (
      <TaskProgress task={latest.data} renderResult={(r) => <AutomapResult result={r} />} />
    ) : null,
  };
}
