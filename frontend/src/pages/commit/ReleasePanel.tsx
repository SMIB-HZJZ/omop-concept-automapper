import { Download, Lock, LockOpen, MessageCircleQuestion, PackageCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { usePublishRelease, useReleases, useStartNextRelease } from "@/api/queries";
import type { ReleaseInfo } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorAlert } from "@/components/common";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatDate } from "@/lib/format";

type Changes = { added: number; changed: number; removed: number };

const stcmUrl = (vocabularyId: number, release: number | "draft") =>
  `/api/source-vocabularies/${vocabularyId}/source_to_concept_map.csv?release=${release}`;

/** "+3 added · 1 changed · 2 removed", or "no changes". */
function changeSummary(c: Changes): string {
  const parts = [
    c.added && `${c.added} added`,
    c.changed && `${c.changed} changed`,
    c.removed && `${c.removed} removed`,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "no changes";
}

/** Mapped terms someone flagged with a question: settle them before publishing. */
function OpenQuestions({ count }: { count: number }) {
  return (
    <Alert className="border-warning/40 bg-warning/10">
      <MessageCircleQuestion className="text-warning" />
      <AlertDescription>
        <span>
          {count === 1 ? "1 mapping has" : `${count} mappings have`} an open question. Use <b>Flagged only</b> below to
          see {count === 1 ? "it" : "them"}; clear a flag once it is settled.
        </span>
      </AlertDescription>
    </Alert>
  );
}

function ReleaseRow({ vocabularyId, release }: { vocabularyId: number; release: ReleaseInfo }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t py-2 text-sm first:border-t-0">
      <span className="font-medium">Release {release.number}</span>
      <span className="text-muted-foreground">
        {formatDate(release.released_at)}
        {release.released_by && ` · ${release.released_by}`}
      </span>
      <span className="text-muted-foreground">
        {release.mapped_sources.toLocaleString()} term{release.mapped_sources === 1 ? "" : "s"} ·{" "}
        {changeSummary(release)}
      </span>
      {release.note && <span className="italic">“{release.note}”</span>}
      <a
        href={stcmUrl(vocabularyId, release.number)}
        download
        className={buttonVariants({ variant: "ghost", size: "sm", className: "ml-auto" })}
        title="OMOP SOURCE_TO_CONCEPT_MAP of this release, with changed and removed mappings of earlier releases expired"
      >
        <Download /> source_to_concept_map
      </a>
    </li>
  );
}

/** Publish the mappings as an official release (which locks them), start the next release, and
 * download the OMOP source_to_concept_map of any release. */
export function ReleasePanel({ vocabularyId }: { vocabularyId: number }) {
  const { isAdmin } = useAuth();
  const state = useReleases(vocabularyId);
  const publish = usePublishRelease(vocabularyId);
  const next = useStartNextRelease(vocabularyId);
  const [note, setNote] = useState("");

  const s = state.data;
  if (!s) return <ErrorAlert error={state.error} title="Could not load releases" />;
  const latest = s.releases[0];
  const nextNumber = (latest?.number ?? 0) + 1;
  const nothingNew = !!latest && !!s.draft && changeSummary(s.draft) === "no changes";

  const onPublish = () =>
    publish.mutate(note, {
      onSuccess: () => {
        toast.success(`Published release ${nextNumber}; the mappings are locked`);
        setNote("");
      },
      onError: (e) => toast.error(errorMessage(e)),
    });
  const onNext = () =>
    next.mutate(undefined, {
      onSuccess: () => toast.success(`Release ${nextNumber} started: mappings can be changed again`),
      onError: (e) => toast.error(errorMessage(e)),
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PackageCheck className="size-5" /> Releases
          {s.locked ? (
            <Badge className="gap-1">
              <Lock className="size-3" /> Release {latest?.number} · locked
            </Badge>
          ) : (
            <Badge variant="outline" className="gap-1">
              <LockOpen className="size-3" /> {latest ? `Draft of release ${nextNumber}` : "Not released yet"}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {s.locked ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm">
              Release {latest?.number} is the official version: its mappings cannot be changed. To change them, start
              release {nextNumber}; release {latest?.number} stays available as published.
            </p>
            {isAdmin && (
              <ConfirmDialog
                trigger={
                  <Button variant="outline" className="ml-auto" disabled={next.isPending}>
                    <LockOpen /> Start release {nextNumber}
                  </Button>
                }
                title={`Start release ${nextNumber}?`}
                description={
                  <p>
                    Mapping, unmapping, auto-mapping and accepting suggestions become possible again. The changes go
                    into release {nextNumber} when it is published; release {latest?.number} does not change.
                  </p>
                }
                confirmLabel={`Start release ${nextNumber}`}
                onConfirm={onNext}
              />
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {s.draft_note && (
              <Alert>
                <AlertDescription>{s.draft_note}</AlertDescription>
              </Alert>
            )}
            {s.open_questions > 0 && <OpenQuestions count={s.open_questions} />}
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm">
                {latest
                  ? `Changes since release ${latest.number}: ${changeSummary(s.draft!)}.`
                  : "Publish the mappings as release 1 when they are ready: the release becomes the official version and locks them."}
              </p>
              {latest && (
                <a
                  href={stcmUrl(vocabularyId, "draft")}
                  download
                  className={buttonVariants({ variant: "ghost", size: "sm" })}
                  title={`OMOP SOURCE_TO_CONCEPT_MAP as release ${nextNumber} would be, if published today`}
                >
                  <Download /> Preview
                </a>
              )}
              {isAdmin && (
                <ConfirmDialog
                  trigger={
                    <Button variant="cta" className="ml-auto" disabled={publish.isPending || nothingNew}>
                      <Lock /> Publish release {nextNumber}
                    </Button>
                  }
                  title={`Publish release ${nextNumber}?`}
                  description={
                    <>
                      <p>
                        The current mappings become release {nextNumber}, the official version, and the vocabulary is
                        locked: no more mapping, unmapping, auto-mapping or accepting suggestions until an admin starts
                        release {nextNumber + 1}.
                      </p>
                      {latest && (
                        <p>
                          Mappings of release {latest.number} that were changed or removed stay in the
                          source_to_concept_map with a valid_end_date of the day before this release.
                        </p>
                      )}
                      {s.open_questions > 0 && <OpenQuestions count={s.open_questions} />}
                      <div className="grid gap-1.5 pt-1">
                        <Label htmlFor="release-note">Note (optional)</Label>
                        <Textarea
                          id="release-note"
                          maxLength={500}
                          rows={2}
                          value={note}
                          onChange={(e) => setNote(e.target.value)}
                          placeholder="e.g. Reviewed by the coding team"
                        />
                      </div>
                    </>
                  }
                  confirmLabel={`Publish release ${nextNumber}`}
                  onConfirm={onPublish}
                />
              )}
            </div>
          </div>
        )}
        {s.releases.length > 0 && (
          <ul className="rounded-lg border px-3">
            {s.releases.map((r) => (
              <ReleaseRow key={r.number} vocabularyId={vocabularyId} release={r} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
