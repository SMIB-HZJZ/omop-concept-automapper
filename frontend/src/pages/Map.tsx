import { useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  Ban,
  CheckCircle2,
  ChevronDown,
  Map as MapIcon,
  MessageCircleQuestion,
  MousePointerClick,
  Search as SearchIcon,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { useAuth } from "@/auth/useAuth";
import {
  drugMatchQuery,
  leaveVocabulary,
  searchQuery,
  translationQuery,
  useAuditRecent,
  useAuditStats,
  useMappingStats,
  usePresence,
  useUnmapped,
  type UnmappedView,
} from "@/api/queries";
import { Input } from "@/components/ui/input";
import { useDebounce } from "@/hooks/useDebounce";
import { cn } from "@/lib/utils";
import { usePersistentState } from "@/hooks/usePersistentState";
import type { AuditEntry, UnmappedItem } from "@/api/types";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorAlert, PageHeader, StatCard } from "@/components/common";
import { AnimatedNumber } from "@/components/motion";
import { CompactPager } from "@/components/Pagination";
import { formatDate, formatNumber } from "@/lib/format";
import { VocabularySelector } from "@/components/VocabularySelector";
import { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Progress } from "@/components/ui/progress";
import { useAutomapControls } from "./map/AutomapSettings";
import { ConceptDetail } from "./map/ConceptDetail";
import { TeamStatus } from "./map/Team";
import { UserAvatar } from "@/components/UserAvatar";
import { defaultSearchRequest } from "./map/searchRequest";
import { methodLabel } from "@/lib/methods";

/** Columns of the term list; ``othersOn`` maps a source term to colleagues who have it open. */
const unmappedColumns = (othersOn: Map<number, string[]>): ColumnDef<UnmappedItem, unknown>[] => [
  {
    id: "term",
    header: "Source term",
    cell: ({ row }) => {
      const others = othersOn.get(row.original.source_id) ?? [];
      return (
        <div className="flex min-w-0 items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="leading-snug font-medium">{row.original.source_concept_name}</div>
            <div className="text-muted-foreground truncate font-mono text-xs">{row.original.source_value}</div>
          </div>
          {others.length > 0 && (
            <div className="flex -space-x-1.5 pt-0.5" title={`Open by ${others.join(", ")}`}>
              {others.map((name) => (
                <UserAvatar key={name} name={name} className="size-5 text-[9px]" />
              ))}
            </div>
          )}
        </div>
      );
    },
  },
  {
    accessorKey: "freq",
    header: () => <div className="text-right">Freq</div>,
    size: 64,
    cell: ({ row }) => (
      <div className="text-muted-foreground text-right text-xs tabular-nums">
        {row.original.freq?.toLocaleString() ?? "—"}
      </div>
    ),
  },
  {
    id: "suggestion",
    header: "",
    size: 56,
    cell: ({ row }) =>
      row.original.flag ? (
        <Badge
          variant="outline"
          className={
            row.original.flag === "question"
              ? "border-warning/40 bg-warning/10 text-warning"
              : "border-destructive/30 bg-destructive/10 text-destructive"
          }
          title={row.original.flag_note ?? undefined}
        >
          {row.original.flag === "question" ? <MessageCircleQuestion className="size-3" /> : <Ban className="size-3" />}
        </Badge>
      ) : row.original.suggestion_concept_id ? (
        <Badge
          variant="secondary"
          className="gap-1"
          title={`An AI suggestion (confidence ${row.original.suggestion_confidence}) is waiting`}
        >
          <Sparkles className="size-3" /> {row.original.suggestion_confidence}
        </Badge>
      ) : row.original.low_concept_id ? (
        <Badge
          variant="outline"
          className="border-warning/25 bg-warning/5 text-warning/75 gap-1"
          title={`Last auto-mapping picked "${row.original.low_concept_name}"${
            row.original.low_extra_count ? ` (+ ${row.original.low_extra_count} more)` : ""
          } with confidence ${row.original.low_confidence}, below the threshold, so it was not queued for review`}
        >
          <Sparkles className="size-3 opacity-70" /> {row.original.low_confidence}
        </Badge>
      ) : null,
  },
];

const RECENT_COLUMNS: ColumnDef<AuditEntry, unknown>[] = [
  { accessorKey: "source_concept_name", header: "Source concept" },
  { accessorKey: "mapped_concept_name", header: "Mapped concept" },
  { accessorKey: "confidence_score", header: "Confidence" },
  {
    accessorKey: "mapping_method",
    header: "Method",
    cell: ({ row }) => methodLabel(row.original.mapping_method),
  },
  { accessorKey: "created_by", header: "By", cell: ({ row }) => row.original.created_by ?? "—" },
  { accessorKey: "created_at", header: "Created", cell: ({ row }) => formatDate(row.original.created_at) },
];

function AuditTrail({ vocabularyId }: { vocabularyId: number }) {
  const [open, setOpen] = useState(false);
  const stats = useAuditStats(open ? vocabularyId : null);
  const recent = useAuditRecent(open ? vocabularyId : null, 10);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border">
      <CollapsibleTrigger className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium">
        Auto-mapping audit trail
        <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-4 border-t p-4">
        {stats.data && stats.data.length === 0 ? (
          <p className="text-muted-foreground text-sm">No auto-mapping statistics available for this vocabulary.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {stats.data?.map((s) => (
              <StatCard
                key={s.mapping_method}
                label={`${methodLabel(s.mapping_method)} mappings`}
                value={formatNumber(s.mapping_count)}
                hint={s.avg_confidence != null ? `Avg confidence ${s.avg_confidence.toFixed(2)}` : undefined}
              />
            ))}
          </div>
        )}
        <div className="text-sm font-medium">Recent mappings</div>
        <DataTable
          columns={RECENT_COLUMNS}
          data={recent.data ?? []}
          getRowId={(r) => String(r.audit_id)}
          isLoading={recent.isLoading}
          emptyText="No mapping history found for this vocabulary."
          maxHeight="16rem"
        />
      </CollapsibleContent>
    </Collapsible>
  );
}

const VIEWS: { value: UnmappedView; label: string; hint: string }[] = [
  { value: "todo", label: "To do", hint: "Terms nobody is handling yet: no flag, nothing waiting in Review" },
  { value: "suggested", label: "In review", hint: "AI suggestions waiting in Review" },
  { value: "flagged", label: "Flagged", hint: "Ask a colleague or can't be mapped" },
  { value: "all", label: "All unmapped", hint: "Every term without a mapping; mapped terms are in Verify & publish" },
];

function ListTabs({
  value,
  onChange,
  counts,
}: {
  value: UnmappedView;
  onChange: (v: UnmappedView) => void;
  counts: Partial<Record<UnmappedView, number>>;
}) {
  return (
    <div
      role="tablist"
      aria-label="Which terms to show"
      className="bg-muted inline-flex max-w-full flex-wrap rounded-lg p-0.5"
    >
      {VIEWS.map((v) => (
        <button
          key={v.value}
          type="button"
          role="tab"
          aria-selected={value === v.value}
          title={v.hint}
          onClick={() => onChange(v.value)}
          className={cn(
            "flex h-7 cursor-pointer items-center gap-1 rounded-md px-2 text-xs whitespace-nowrap transition-colors",
            value === v.value
              ? "bg-background text-foreground font-medium shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {v.label}
          {counts[v.value] ? <span className="text-muted-foreground tabular-nums">{counts[v.value]}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** Mapped / total with a bar, what is left, and a link to waiting suggestions. */
function ProgressStrip({ vocabularyId, action, team }: { vocabularyId: number; action: ReactNode; team: ReactNode }) {
  const stats = useMappingStats(vocabularyId);
  const d = stats.data;
  return (
    <Card className="gap-0 py-3">
      <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4">
        <ErrorAlert error={stats.error} />
        <div className="min-w-56 flex-1 space-y-1.5">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span>
              <b className="tabular-nums">{d ? <AnimatedNumber value={d.mapped} /> : "—"}</b>
              <span className="text-muted-foreground"> of {formatNumber(d?.total)} terms mapped</span>
            </span>
            <span className="text-primary font-semibold tabular-nums">
              {d ? <AnimatedNumber value={d.percentage} decimals={1} suffix="%" /> : ""}
            </span>
          </div>
          <Progress value={d?.percentage ?? 0} aria-label="Mapped terms" />
        </div>
        {d && d.pending_suggestions > 0 && (
          <Link
            to="/review"
            className="bg-accent text-accent-foreground hover:bg-accent/70 flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm transition-colors"
          >
            <Sparkles className="size-4" />
            <b className="tabular-nums">{formatNumber(d.pending_suggestions)}</b> suggestions to review →
          </Link>
        )}
        <div className="ml-auto flex items-center gap-3">
          {team}
          {action}
        </div>
      </CardContent>
    </Card>
  );
}

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

/** Everything below the vocabulary selector; keyed by vocabulary so paging and selection reset. */
function VocabularyMapping({ vocabularyId, isDrug }: { vocabularyId: number; isDrug: boolean }) {
  // A fixed page size: the compact pager has no size control
  const perPage = 50;
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [view, setView] = useState<UnmappedView>("todo");
  const [query, setQuery] = useState("");
  const q = useDebounce(query.trim(), 300);
  // Paging starts over when the list changes (stored with the list it belongs to, not reset by effects)
  const listKey = `${view}|${q}|${perPage}`;
  const [paging, setPaging] = useState({ listKey, page: 1 });
  const page = paging.listKey === listKey ? paging.page : 1;
  const setPage = (next: number) => setPaging({ listKey, page: next });

  const stats = useMappingStats(vocabularyId);
  const unmapped = useUnmapped(vocabularyId, page, perPage, view, q);
  const automap = useAutomapControls({ vocabularyId, isDrug });

  // The API clamps an out-of-range page (e.g. after mapping the last rows of the last page)
  const pages = unmapped.data?.pages ?? 1;
  const currentPage = unmapped.data?.page ?? page;

  const items = useMemo(() => unmapped.data?.items ?? [], [unmapped.data]);
  const found = items.find((i) => String(i.source_id) === selectedSource) ?? null;
  // The open term stays open when a refresh drops it from the list (a colleague mapped or
  // flagged it, an auto-map queued a suggestion, or it moved to another page): work in progress
  // in the panel is kept, and saving tells whether it was mapped meanwhile
  const [held, setHeld] = useState<UnmappedItem | null>(null);
  if (found && found !== held) setHeld(found);
  const concept = found ?? (held && String(held.source_id) === selectedSource ? held : null);

  const select = useCallback((id: string | null) => {
    setSelectedSource(id);
    if (id)
      requestAnimationFrame(() =>
        document.querySelector(`[data-row-id="${id}"]`)?.scrollIntoView({ block: "nearest" }),
      );
  }, []);

  const afterMapped = () => {
    const idx = items.findIndex((i) => String(i.source_id) === selectedSource);
    const next = items[idx + 1] ?? items[idx - 1];
    select(next ? String(next.source_id) : null);
  };

  // ↑/↓ move through the source terms (not while typing or with a dialog open)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      if (isTyping(e.target) || document.querySelector('[role="dialog"]') || items.length === 0) return;
      e.preventDefault();
      const idx = items.findIndex((i) => String(i.source_id) === selectedSource);
      const nextIdx = idx < 0 ? 0 : Math.min(items.length - 1, Math.max(0, idx + (e.key === "ArrowDown" ? 1 : -1)));
      select(String(items[nextIdx].source_id));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selectedSource, select]);

  // Load the next term's candidates in the background, so moving on is instant
  const qc = useQueryClient();
  const [mapDomains] = usePersistentState<string[]>("map-domains", []);
  useEffect(() => {
    const idx = items.findIndex((i) => String(i.source_id) === selectedSource);
    const next = idx >= 0 ? items[idx + 1] : undefined;
    if (!next) return;
    if (isDrug && next.drug_structure) {
      // Its first search waits for the structure match (no AI call for names the vocabulary
      // knows): load the match, then the search built from it
      qc.fetchQuery(drugMatchQuery(next.source_id, null))
        .then((match) => qc.prefetchQuery(searchQuery(defaultSearchRequest(next, isDrug, mapDomains, match))))
        .catch(() => {}); // the panel loads it again and shows any error
      return;
    }
    void qc.prefetchQuery(searchQuery(defaultSearchRequest(next, isDrug, mapDomains)));
    if (isDrug) return;
    // And its English search, which the panel adds once the translation is known
    qc.fetchQuery(translationQuery(next.source_id))
      .then((t) => {
        if (t.english)
          void qc.prefetchQuery(searchQuery(defaultSearchRequest(next, isDrug, mapDomains, undefined, t.english)));
      })
      .catch(() => {});
  }, [items, selectedSource, isDrug, mapDomains, qc]);

  const allMapped = stats.data && stats.data.unmapped === 0;
  const { isAdmin, user } = useAuth();

  // Presence: renew "I have this term open" while it is open, and see who else is where
  const presence = usePresence(vocabularyId, concept?.source_id ?? null);
  const others = useMemo(() => (presence.data ?? []).filter((p) => p.user_id !== user?.id), [presence.data, user?.id]);
  const othersOn = useMemo(() => {
    const by = new Map<number, string[]>();
    for (const p of others) by.set(p.source_id, [...(by.get(p.source_id) ?? []), p.username]);
    return by;
  }, [others]);
  const onlineOthers = useMemo(() => [...new Set(others.map((p) => p.username))], [others]);
  const columns = useMemo(() => unmappedColumns(othersOn), [othersOn]);
  useEffect(() => () => void leaveVocabulary(vocabularyId).catch(() => {}), [vocabularyId]);

  return (
    <>
      <ProgressStrip
        vocabularyId={vocabularyId}
        action={!allMapped && isAdmin && automap.trigger}
        team={<TeamStatus vocabularyId={vocabularyId} online={onlineOthers} />}
      />
      {automap.progress}

      {allMapped ? (
        <EmptyState
          title="All concepts in this vocabulary have been mapped!"
          icon={<CheckCircle2 className="text-success size-8" />}
        >
          Switch to a different vocabulary or import more concepts on the Import Data page.
        </EmptyState>
      ) : (
        /* Source terms on the left, their candidate standard concepts on the right; both columns
           have a fixed height on wide screens, so switching terms never moves the page */
        <div className="grid gap-5 xl:h-[max(36rem,calc(100vh-19rem))] xl:grid-cols-[minmax(0,4fr)_minmax(0,8fr)]">
          <Card className="min-h-0 min-w-0 gap-0 py-0">
            <CardContent className="flex h-full min-h-0 flex-col gap-3 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <ListTabs
                  value={view}
                  onChange={setView}
                  counts={{
                    todo: unmapped.data?.todo,
                    suggested: stats.data?.pending_suggestions,
                    flagged: unmapped.data?.flagged,
                    all: stats.data?.unmapped,
                  }}
                />
                <span className="text-muted-foreground text-xs">
                  {formatNumber(unmapped.data?.total)} · most frequent first · ↑↓
                </span>
              </div>
              <div className="relative">
                <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
                <Input
                  aria-label="Find a source term"
                  placeholder="Find a term or code…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="h-8 pl-8 text-sm"
                />
              </div>
              <ErrorAlert error={unmapped.error} />
              <DataTable
                columns={columns}
                data={items}
                getRowId={(r) => String(r.source_id)}
                selection="single"
                selected={selectedSource ? [selectedSource] : []}
                // Clicking the selected row again keeps it selected
                onSelectedChange={(ids) => ids[0] && select(ids[0])}
                sortable={false}
                isLoading={unmapped.isLoading}
                emptyText="No unmapped concepts on this page."
                maxHeight="none"
                className="max-h-[32rem] min-h-0 flex-1 xl:max-h-none"
              />
              <CompactPager page={currentPage} pages={pages} onPageChange={setPage} />
            </CardContent>
          </Card>

          <div className="min-h-0 min-w-0">
            {concept ? (
              <ConceptDetail
                key={concept.source_id}
                concept={concept}
                vocabularyId={vocabularyId}
                isDrug={isDrug}
                onMapped={afterMapped}
                onUndone={(id) => select(String(id))}
                othersHere={othersOn.get(concept.source_id) ?? []}
                leftList={!found}
              />
            ) : (
              <div className="h-full">
                <EmptyState title="Pick a source term" icon={<MousePointerClick className="text-primary size-7" />}>
                  Click a term on the left, or press ↓. Its matching standard concepts appear here, ready to select and
                  confirm.
                </EmptyState>
              </div>
            )}
          </div>
        </div>
      )}

      <AuditTrail vocabularyId={vocabularyId} />
    </>
  );
}

export function MapPage() {
  const selection = useSelectedVocabulary();
  const vocabularyId = selection.vocabularyId;
  const { isAdmin } = useAuth();
  return (
    <div className="space-y-4">
      <PageHeader
        compact
        icon={MapIcon}
        step={2}
        title="Map concepts"
        description={
          isAdmin ? (
            <>
              Let the AI map all remaining terms with <b>Auto-map with AI</b>, or pick a term in the table to map it by
              hand.
            </>
          ) : (
            <>
              Pick a term on the left, tick its standard concept on the right and confirm. Not sure? Use{" "}
              <b>AI suggest</b>, or <b>Flag</b> the term for a colleague.
            </>
          )
        }
      />
      <VocabularySelector selection={selection} />
      {vocabularyId !== null && (
        <VocabularyMapping key={vocabularyId} vocabularyId={vocabularyId} isDrug={selection.isDrug} />
      )}
    </div>
  );
}
