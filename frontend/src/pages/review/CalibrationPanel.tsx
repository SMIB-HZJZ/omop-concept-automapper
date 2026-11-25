import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { toast } from "sonner";

import { api, unwrap } from "@/api/client";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useAutomapThreshold } from "@/hooks/useAutomapThreshold";
import { methodLabel } from "@/lib/methods";

const percent = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

/** How often reviewers accepted suggestions per confidence level, and the threshold that keeps
 * at least 95% of auto-accepted mappings correct. */
export function CalibrationPanel({ vocabularyId }: { vocabularyId: number | null }) {
  const [threshold, setThreshold] = useAutomapThreshold();
  const calibration = useQuery({
    queryKey: ["calibration", vocabularyId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/suggestions/calibration", {
          params: { query: { vocabulary_id: vocabularyId ?? undefined } },
        }),
      ),
  });
  const data = calibration.data;
  if (!data || data.decided < 20) return null; // too few decisions to say anything

  const recommended = data.recommended_threshold;
  return (
    <Alert>
      <Gauge />
      <AlertDescription className="space-y-1">
        <div>
          From {data.decided.toLocaleString()} review decisions:{" "}
          {recommended ? (
            <>
              suggestions with confidence ≥ <b>{recommended}</b> were right in at least {percent(data.target_precision)}{" "}
              of cases.
            </>
          ) : (
            <>
              no confidence level was right in {percent(data.target_precision)} of cases yet, so there is no
              recommendation.
            </>
          )}{" "}
          Current auto-map threshold: <b>{threshold}</b>.
          {recommended && recommended !== threshold && (
            <Button
              size="sm"
              variant="link"
              className="h-auto px-1"
              onClick={() => {
                setThreshold(recommended);
                toast.success(`Auto-map threshold set to ${recommended}`);
              }}
            >
              use {recommended}
            </Button>
          )}
        </div>
        <div className="text-muted-foreground text-xs">
          Precision by method:{" "}
          {data.methods
            .map((m) => `${methodLabel(m.method)} ${percent(m.precision)} (${m.accepted + m.rejected})`)
            .join(" · ")}
        </div>
      </AlertDescription>
    </Alert>
  );
}
