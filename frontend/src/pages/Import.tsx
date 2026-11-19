import { useSearchParams } from "react-router";

import { PageHeader } from "@/components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Atc7Tab } from "./import/Atc7Tab";
import { EmbeddingsTab } from "./import/EmbeddingsTab";
import { SourceTab } from "./import/SourceTab";
import { VocabularyTab } from "./import/VocabularyTab";
import { Upload } from "lucide-react";

const TABS = ["vocabulary", "source", "atc7", "embeddings"] as const;
type Tab = (typeof TABS)[number];

export function ImportPage() {
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
          <TabsTrigger value="vocabulary">1 · OMOP vocabulary</TabsTrigger>
          <TabsTrigger value="source">2 · Source terms</TabsTrigger>
          <TabsTrigger value="atc7">3 · ATC7 codes</TabsTrigger>
          <TabsTrigger value="embeddings">4 · Embeddings</TabsTrigger>
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
