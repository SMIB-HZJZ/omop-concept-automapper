/** What an AI suggest outcome means, in plain words. */
export function outcomeLabel(outcome: string, threshold: number): { text: string; tone: string } {
  switch (outcome) {
    case "accepted":
      return { text: `confident (≥ ${threshold})`, tone: "bg-success/15 text-success" };
    case "rejected":
      return { text: `unsure (below ${threshold})`, tone: "bg-warning/15 text-warning" };
    case "no_match":
      return { text: "no fitting concept", tone: "bg-muted text-muted-foreground" };
    case "no_candidates":
      return { text: "nothing found", tone: "bg-muted text-muted-foreground" };
    case "error":
      return { text: "failed", tone: "bg-destructive/15 text-destructive" };
    default:
      return { text: outcome, tone: "bg-muted text-muted-foreground" };
  }
}
