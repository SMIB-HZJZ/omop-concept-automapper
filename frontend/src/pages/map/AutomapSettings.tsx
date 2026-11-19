import { Bot, Settings2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { api, unwrap } from "@/api/client";
import { useMeta } from "@/api/queries";
import { taskVocabularyId } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { MultiSelect } from "@/components/MultiSelect";
import { TaskProgress } from "@/components/TaskProgress";
import { DrugModeNote } from "@/components/VocabularyTypeSwitch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Slider } from "@/components/ui/slider";
import { DEFAULT_THRESHOLD, useAutomapThreshold } from "@/hooks/useAutomapThreshold";
import { DOMAIN_MODES, type DomainMode, useDomainMode } from "@/hooks/useDomainMode";
import { usePersistentState } from "@/hooks/usePersistentState";
import { useStartTask } from "@/hooks/useStartTask";
import { useLatestTask } from "@/hooks/useTasks";
import { AutomapResult } from "./AutomapResult";
import { DEFAULT_PIPELINE, PipelineSelect } from "./PipelineSelect";

export function useAutomapControls({ vocabularyId, isDrug }: { vocabularyId: number; isDrug: boolean }) {
  const meta = useMeta();
  const { run, busy } = useStartTask();
  const [domains, setDomains] = usePersistentState<string[]>("automap-domains", []);
  const [threshold, setThreshold] = useAutomapThreshold();
  const [domainMode, setDomainMode] = useDomainMode();
  // Drug mode comes from the vocabulary type (set on the vocabulary bar)
  const drugSpecific = isDrug;
  const [mode, setMode] = useState<"review" | "commit">("review");
  const [pipeline, setPipeline] = useState(DEFAULT_PIPELINE);
  const [limit, setLimit] = useState("");
  const [open, setOpen] = useState(false);

  const latest = useLatestTask("automap.run", (t: Task) => taskVocabularyId(t) === vocabularyId);

  const submit = async () => {
    if (domains.length === 0) {
      toast.warning("Please select at least one target domain for auto-mapping.");
      return;
    }
    const started = await run(
      async () =>
        unwrap(
          await api.POST("/api/source-vocabularies/{vocabulary_id}/automap", {
            params: { path: { vocabulary_id: vocabularyId } },
            body: {
              domains,
              domain_mode: domainMode,
              threshold,
              drug_specific: drugSpecific,
              mode,
              pipeline: pipeline === DEFAULT_PIPELINE ? null : pipeline,
              limit: limit ? Number(limit) : null,
            },
          }),
        ),
      "Auto-mapping",
    );
    if (started) setOpen(false);
  };

  return {
    trigger: (
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="cta" className="gap-2">
            <Bot className="size-4" /> Auto-map with AI
            <Settings2 className="size-4 opacity-70" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side="bottom"
          collisionPadding={12}
          className="max-h-(--radix-popover-content-available-height) w-96 space-y-4 overflow-y-auto"
        >
          <div className="font-medium">Auto-mapping configuration</div>
          <div className="grid gap-1.5">
            <Label htmlFor="automap-domains">Target domains</Label>
            <MultiSelect
              id="automap-domains"
              options={meta.data?.domains ?? []}
              value={domains}
              onChange={setDomains}
              placeholder="Select domains…"
            />
          </div>
          <div className="grid gap-1.5">
            <Label id="domain-mode-label">Domains are</Label>
            <RadioGroup
              aria-labelledby="domain-mode-label"
              value={domainMode}
              onValueChange={(v) => setDomainMode(v as DomainMode)}
            >
              {DOMAIN_MODES.map((m) => (
                <div key={m.value} className="flex items-start gap-2">
                  <RadioGroupItem value={m.value} id={`domain-mode-${m.value}`} className="mt-0.5" />
                  <Label htmlFor={`domain-mode-${m.value}`} className="grid gap-0.5 font-normal">
                    <span className="font-medium">{m.label}</span>
                    <span className="text-muted-foreground text-xs">{m.hint}</span>
                  </Label>
                </div>
              ))}
            </RadioGroup>
          </div>
          <div className="grid gap-2">
            <Label>
              Confidence threshold: <span className="tabular-nums">{threshold}</span>
            </Label>
            <Slider
              thumbLabel="Confidence threshold"
              min={1}
              max={10}
              step={1}
              value={[threshold]}
              onValueChange={(v) => setThreshold(v[0] ?? DEFAULT_THRESHOLD)}
            />
            <p className="text-muted-foreground text-xs">
              Minimum confidence score (1–10) required to auto-map a concept. AI suggest uses the same threshold.
            </p>
          </div>
          <DrugModeNote isDrug={drugSpecific} />
          <div className="grid gap-1.5">
            <Label id="automap-mode-label">Mode</Label>
            <RadioGroup
              aria-labelledby="automap-mode-label"
              value={mode}
              onValueChange={(v) => setMode(v as "review" | "commit")}
            >
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
            <Input
              id="automap-limit"
              type="number"
              min={1}
              placeholder="All unmapped concepts"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </div>
          <Button variant="cta" className="w-full gap-2" onClick={() => void submit()} disabled={busy}>
            <Bot className="size-4" /> Start auto mapping
          </Button>
        </PopoverContent>
      </Popover>
    ),
    progress: latest.data ? <TaskProgress task={latest.data} renderResult={(t) => <AutomapResult task={t} />} /> : null,
  };
}
