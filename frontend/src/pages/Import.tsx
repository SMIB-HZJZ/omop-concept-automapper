import { useSearchParams } from "react-router";

import { PageHeader } from "@/components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Atc7Tab } from "./import/Atc7Tab";
import { EmbeddingsTab } from "./import/EmbeddingsTab";
import { SourceTab } from "./import/SourceTab";
import { VocabularyTab } from "./import/VocabularyTab";

const TABS = ["vocabulary", "source", "atc7", "embeddings"] as const;
type Tab = (typeof TABS)[number];

export function ImportPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(tabParam ?? "") ? (tabParam as Tab) : "vocabulary";

  return (
    <div>
      <PageHeader
        title="Data Import & Embedding"
        description="Import OMOP standard vocabularies and your source concepts, process ATC7 codes, and embed concepts for similarity search."
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
          <TabsTrigger value="vocabulary">OMOP Vocabulary Tables</TabsTrigger>
          <TabsTrigger value="source">Source Concepts</TabsTrigger>
          <TabsTrigger value="atc7">ATC7 Processing</TabsTrigger>
          <TabsTrigger value="embeddings">Embedding Management</TabsTrigger>
        </TabsList>
        <TabsContent value="vocabulary">
          <VocabularyTab />
        </TabsContent>
        <TabsContent value="source">
          <SourceTab />
        </TabsContent>
        <TabsContent value="atc7">
          <Atc7Tab />
        </TabsContent>
        <TabsContent value="embeddings">
          <EmbeddingsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
