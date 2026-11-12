import { Info } from "lucide-react";

import { api, unwrap } from "@/api/client";
import { useAtc7Stats } from "@/api/queries";
import { taskResult } from "@/api/taskResults";
import { useAuth } from "@/auth/useAuth";
import { ErrorAlert, StatCard } from "@/components/common";
import { TaskProgress } from "@/components/TaskProgress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useStartTask } from "@/hooks/useStartTask";
import { useLatestTask } from "@/hooks/useTasks";
import { formatNumber } from "@/lib/format";
import { AdminNote } from "./AdminNote";

export function Atc7Tab() {
  const { isAdmin } = useAuth();
  const stats = useAtc7Stats();
  const latest = useLatestTask("atc7.process");
  const { run, busy } = useStartTask();
  const active = latest.data && !latest.data.is_terminal;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>ATC7 code processing</CardTitle>
          <CardDescription>
            After importing the OMOP vocabulary tables, process ATC7 codes for drug concepts. This finds all ATC7 codes
            that drug concepts belong to through relationships and hierarchies.
          </CardDescription>
        </CardHeader>
      </Card>
      <Alert>
        <Info />
        <AlertTitle>Prerequisites</AlertTitle>
        <AlertDescription>Import the CONCEPT, CONCEPT_RELATIONSHIP and CONCEPT_ANCESTOR tables first.</AlertDescription>
      </Alert>

      {isAdmin ? (
        <Button
          disabled={busy || !!active}
          onClick={() => void run(async () => unwrap(await api.POST("/api/atc7/process")), "ATC7 processing")}
        >
          Process ATC7 codes
        </Button>
      ) : (
        <AdminNote />
      )}

      {latest.data && (
        <TaskProgress
          task={latest.data}
          renderResult={(task) => {
            const drugs = taskResult(task, "atc7.process")?.drugs_with_atc7 ?? 0;
            return drugs > 0
              ? `Processed ATC7 codes for ${formatNumber(drugs)} drug concepts.`
              : "No ATC7 codes found. Ensure all vocabulary tables are imported.";
          }}
        />
      )}

      <section className="space-y-3">
        <h3 className="font-medium">ATC7 statistics</h3>
        <ErrorAlert error={stats.error} />
        {stats.data && stats.data.total_drugs_with_atc7 === 0 ? (
          <Alert>
            <Info />
            <AlertDescription>No ATC7 data found. Process ATC7 codes first.</AlertDescription>
          </Alert>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <StatCard label="Drugs with ATC7 codes" value={formatNumber(stats.data?.total_drugs_with_atc7)} />
            <StatCard label="Average ATC7 codes per drug" value={(stats.data?.avg_atc7_per_drug ?? 0).toFixed(1)} />
          </div>
        )}
      </section>
    </div>
  );
}
