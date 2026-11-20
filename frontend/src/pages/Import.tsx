import { useSearchParams } from "react-router";

import { PageHeader } from "@/components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmbeddingsTab } from "./import/EmbeddingsTab";
import { SourceTab } from "./import/SourceTab";
import { VocabularyTab } from "./import/VocabularyTab";
import { Check, Upload } from "lucide-react";

import { useEmbeddingStatus, useSourceVocabularies, useVocabularyStatus } from "@/api/queries";

const TABS = ["vocabulary", "source", "embeddings"] as const;
type Tab = (typeof TABS)[number];

/** Step number, or a tick once the step is done. */
function StepMark({ done, n }: { done: boolean; n: number }) {
  return done ? (
    <span className="bg-success/15 text-success flex size-5 items-center justify-center rounded-full" title="Done">
      <Check className="size-3" />
    </span>
  ) : (
    <span className="bg-muted text-muted-foreground flex size-5 items-center justify-center rounded-full text-[11px] font-semibold">
      {n}
    </span>
  );
}

function useImportProgress() {
  const vocabulary = useVocabularyStatus();
  const sources = useSourceVocabularies();
  const embeddings = useEmbeddingStatus([]);
  return {
    vocabulary: (vocabulary.data?.tables ?? []).some(
      (t) => t.table_name.toLowerCase() === "concept" && t.row_count > 0,
    ),
    source: (sources.data ?? []).length > 0,
    embeddings: (embeddings.data?.embedded ?? 0) > 0,
  };
}

export function ImportPage() {
  const done = useImportProgress();
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(tabParam ?? "") ? (tabParam as Tab) : "vocabulary";

  return (
    <div>
      <PageHeader
        icon={Upload}
        step={1}
        title="Import data"
        description="Load the OMOP vocabularies and your source terms, then embed the standard concepts so the AI can search them. Work through the tabs from left to right."
      />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          const next = new URLSearchParams(params);
          next.set("tab", v);
          setParams(next, { replace: true });
        }}
      >
        <TabsList className="mb-4 flex-wrap">
          <TabsTrigger value="vocabulary" className="gap-1.5">
            <StepMark done={done.vocabulary} n={1} /> OMOP vocabulary
          </TabsTrigger>
          <TabsTrigger value="source" className="gap-1.5">
            <StepMark done={done.source} n={2} /> Source terms
          </TabsTrigger>
          <TabsTrigger value="embeddings" className="gap-1.5">
            <StepMark done={done.embeddings} n={3} /> Embeddings
          </TabsTrigger>
        </TabsList>
        <TabsContent value="vocabulary">
          <VocabularyTab />
        </TabsContent>
        <TabsContent value="source">
          <SourceTab />
        </TabsContent>
        <TabsContent value="embeddings">
          <EmbeddingsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
