import { Eye, Users } from "lucide-react";

import { useTeamActivity } from "@/api/queries";
import { UserAvatar } from "@/components/UserAvatar";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** Who is working in this vocabulary right now, and a "Team" panel with today's numbers. */
export function TeamStatus({ vocabularyId, online }: { vocabularyId: number; online: string[] }) {
  return (
    <div className="flex items-center gap-2">
      {online.length > 0 && (
        <div className="flex items-center gap-1.5" title={`Working now: ${online.join(", ")}`}>
          <div className="flex -space-x-1.5">
            {online.slice(0, 5).map((name) => (
              <UserAvatar key={name} name={name} />
            ))}
          </div>
          {online.length > 5 && <span className="text-muted-foreground text-xs">+{online.length - 5}</span>}
        </div>
      )}
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm" className="gap-1.5">
            <Users className="size-4" /> Team
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[26rem]">
          <TeamTable vocabularyId={vocabularyId} online={online} />
        </PopoverContent>
      </Popover>
    </div>
  );
}

function TeamTable({ vocabularyId, online }: { vocabularyId: number; online: string[] }) {
  const activity = useTeamActivity(vocabularyId);
  const rows = activity.data ?? [];
  return (
    <div className="space-y-3">
      <div className="text-sm font-medium">Team in this vocabulary</div>
      {activity.isLoading ? (
        <div className="text-muted-foreground text-sm">Loading…</div>
      ) : rows.length === 0 && online.length === 0 ? (
        <p className="text-muted-foreground text-sm">Nobody has mapped or reviewed terms here yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground text-left text-xs">
              <th className="pb-1 font-medium">Person</th>
              <th className="pb-1 text-right font-medium" title="Terms mapped by hand today (in total)">
                Mapped
              </th>
              <th className="pb-1 text-right font-medium" title="AI suggestions accepted or rejected today (in total)">
                Reviewed
              </th>
              <th className="pb-1 text-right font-medium" title="Their flags still open">
                Flags
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.user_id} className="border-t">
                <td className="py-1.5">
                  <span className="flex items-center gap-2">
                    <UserAvatar name={r.username} />
                    {r.username}
                    {online.includes(r.username) && <Eye className="text-success size-3.5" aria-label="online" />}
                  </span>
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  <b>{r.mapped_today}</b> <span className="text-muted-foreground text-xs">({r.mapped_total})</span>
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  <b>{r.reviewed_today}</b> <span className="text-muted-foreground text-xs">({r.reviewed_total})</span>
                </td>
                <td className="py-1.5 text-right tabular-nums">{r.flags_open || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-muted-foreground text-xs">Today in bold, all time in brackets. Updates every minute.</p>
    </div>
  );
}
