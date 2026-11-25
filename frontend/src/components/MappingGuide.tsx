import { BookOpen } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="text-muted-foreground space-y-1.5 text-sm [&_b]:text-foreground [&_li]:ml-4 [&_li]:list-disc">
        {children}
      </div>
    </section>
  );
}

/** The team's mapping conventions in short: what to pick, which match type, when to flag. */
export function MappingGuide() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <BookOpen className="size-4" /> Mapping guide
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Mapping guide</DialogTitle>
          <DialogDescription>How to choose a concept, a match type, or a flag.</DialogDescription>
        </DialogHeader>
        <div className="space-y-5">
          <Section title="1. Pick the concept that means the same">
            <ul>
              <li>Map to a standard concept that says what the source term says: no less, no more.</li>
              <li>
                Check the domain: a diagnosis goes to <b>Condition</b>, a lab test to <b>Measurement</b>, a medicine to{" "}
                <b>Drug</b>, an intervention to <b>Procedure</b>.
              </li>
              <li>
                Read the whole term, abbreviations included. The vocabulary&apos;s “About this list” says what its terms
                record.
              </li>
            </ul>
          </Section>
          <Section title="2. Say how close it is (match type)">
            <ul>
              <li>
                <b>Exact</b>: same meaning. The default.
              </li>
              <li>
                <b>Broader</b>: the concept is more general and drops a detail of the term (e.g. a stated stage or
                site). Use it when no exact concept exists.
              </li>
              <li>
                <b>Narrower</b>: the concept adds detail the term does not state. Avoid it unless the context really
                implies that detail.
              </li>
              <li>
                <b>Related</b>: associated, but not the same thing. A last resort; consider flagging instead.
              </li>
            </ul>
          </Section>
          <Section title="3. One concept, or several">
            <ul>
              <li>Map to one concept whenever one covers the term.</li>
              <li>
                Use several only when no single concept exists, e.g. a term that combines two conditions. Each must
                cover a part of the term.
              </li>
              <li>
                Drugs: map to the most specific drug the term states (ingredient, strength, form). Don&apos;t add a
                brand or pack size the term doesn&apos;t name.
              </li>
            </ul>
          </Section>
          <Section title="4. Don't guess: flag">
            <ul>
              <li>
                <b>Ask a colleague</b> when the term is ambiguous or you are unsure; add a note saying why.
              </li>
              <li>
                <b>Can&apos;t be mapped</b> when no concept fits, or the term has no clinical meaning (e.g. “other”, a
                free-text remark).
              </li>
              <li>On Check &amp; commit, any mapping can get a question; settle all questions before a release.</li>
            </ul>
          </Section>
          <Section title="5. AI suggestions">
            <ul>
              <li>Confidence is a guide, not a guarantee: check the meaning and the domain as you would by hand.</li>
              <li>
                Reject what is wrong; the term goes back to the terms still to map. Accepts are recorded under your
                name.
              </li>
            </ul>
          </Section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
