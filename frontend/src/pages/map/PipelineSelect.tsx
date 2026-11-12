import { useWorkflows } from "@/api/queries";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export const DEFAULT_PIPELINE = "__default__";

export function PipelineSelect({ value, onChange, id }: { value: string; onChange: (v: string) => void; id?: string }) {
  const workflows = useWorkflows();
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={DEFAULT_PIPELINE}>Default pipeline</SelectItem>
        {(workflows.data?.pipelines ?? []).map((p) => (
          <SelectItem key={p.name} value={p.name}>
            {p.name}
            {p.is_default ? " (default)" : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
