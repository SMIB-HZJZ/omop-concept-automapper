import { Info } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";

export function AdminNote() {
  return (
    <Alert>
      <Info />
      <AlertDescription>Only administrators can run this action.</AlertDescription>
    </Alert>
  );
}
