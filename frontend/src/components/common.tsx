import { AlertCircle, Inbox, type LucideIcon } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";

import { errorMessage } from "@/api/client";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { useAuth } from "@/auth/useAuth";
import { AnimatedNumber } from "@/components/motion";
import { cn } from "@/lib/utils";

/** Workflow steps shown as "Step n of 4" on the pages that belong to the mapping workflow. */
const WORKFLOW_STEPS = 4;

export function PageHeader({
  title,
  description,
  actions,
  icon: Icon,
  step,
  compact,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: LucideIcon;
  /** Position in the Import → Map → Review → Commit workflow */
  step?: number;
  /** Smaller, single-block header for work pages where vertical space matters */
  compact?: boolean;
}) {
  // The four-step workflow is the admin's view; users only have mapping and review
  const { isAdmin } = useAuth();
  if (!isAdmin) step = undefined;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className={cn("flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between", compact ? "mb-2" : "mb-6")}
    >
      <div className="flex items-start gap-4">
        {Icon && !compact && (
          <div className="bg-brand hidden size-11 shrink-0 items-center justify-center rounded-xl text-white shadow-[var(--shadow-glow)] sm:flex">
            <Icon className="size-5" />
          </div>
        )}
        <div>
          {step !== undefined && (
            <div
              className={cn(
                "text-primary mb-1 text-xs font-semibold tracking-wider uppercase",
                compact && "[@media(max-height:820px)]:hidden",
              )}
            >
              Step {step} of {WORKFLOW_STEPS}
            </div>
          )}
          <h1 className={cn("font-semibold tracking-tight", compact ? "text-xl" : "text-2xl")}>{title}</h1>
          {description && (
            <p
              className={cn(
                "text-muted-foreground max-w-3xl leading-relaxed",
                compact ? "text-xs [@media(max-height:820px)]:hidden" : "mt-1 text-sm",
              )}
            >
              {description}
            </p>
          )}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </motion.div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  className,
  tone,
}: {
  label: string;
  /** A number counts up when it changes; other content is shown as is */
  value: ReactNode;
  hint?: ReactNode;
  className?: string;
  /** Colour accent: success for "done", warning for "needs attention" */
  tone?: "success" | "warning" | "primary";
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      whileHover={{ y: -2 }}
      className="h-full"
    >
      <Card
        className={cn(
          "relative h-full gap-1 overflow-hidden py-4 transition-shadow hover:shadow-[var(--shadow-lift)]",
          className,
        )}
      >
        {tone && (
          <div
            aria-hidden
            className={cn(
              "absolute inset-y-0 left-0 w-1",
              tone === "success" && "bg-success",
              tone === "warning" && "bg-warning",
              tone === "primary" && "bg-brand",
            )}
          />
        )}
        <CardContent className="px-4">
          <div className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{label}</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">
            {typeof value === "number" ? <AnimatedNumber value={value} /> : value}
          </div>
          {hint && <div className="text-muted-foreground mt-1 text-xs">{hint}</div>}
        </CardContent>
      </Card>
    </motion.div>
  );
}

export function ErrorAlert({ error, title = "Something went wrong" }: { error: unknown; title?: string }) {
  if (!error) return null;
  return (
    <Alert variant="destructive">
      <AlertCircle />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{errorMessage(error)}</AlertDescription>
    </Alert>
  );
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className="text-muted-foreground bg-card/60 flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-10 text-center"
    >
      <div className="bg-accent flex size-14 items-center justify-center rounded-full">
        {icon ?? <Inbox className="text-primary size-7" />}
      </div>
      <div className="text-foreground font-medium">{title}</div>
      {children && <div className="max-w-md text-sm leading-relaxed">{children}</div>}
    </motion.div>
  );
}
