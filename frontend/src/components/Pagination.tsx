import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const PAGE_SIZES = [25, 50, 100];

/** Page number + page size controls for server-paginated tables. */
export function PageControls({
  idPrefix,
  page,
  pages,
  perPage,
  onPageChange,
  onPerPageChange,
  itemLabel = "Rows",
}: {
  idPrefix: string;
  page: number;
  pages: number;
  perPage: number;
  onPageChange: (page: number) => void;
  onPerPageChange: (perPage: number) => void;
  itemLabel?: string;
}) {
  return (
    <>
      <div className="grid gap-1.5">
        <Label htmlFor={`${idPrefix}-page`}>Page</Label>
        <Input
          id={`${idPrefix}-page`}
          type="number"
          className="w-24"
          min={1}
          max={pages}
          value={page}
          onChange={(e) => onPageChange(Math.min(Math.max(1, Number(e.target.value) || 1), pages))}
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor={`${idPrefix}-per-page`}>{itemLabel} per page</Label>
        <Select
          value={String(perPage)}
          onValueChange={(v) => {
            onPerPageChange(Number(v));
            onPageChange(1);
          }}
        >
          <SelectTrigger id={`${idPrefix}-per-page`} className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((n) => (
              <SelectItem key={n} value={String(n)}>
                {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="text-sm">
        Page <b>{page}</b> of <b>{pages}</b>
      </div>
    </>
  );
}
