import { useQueries, useQueryClient } from "@tanstack/react-query";
import {
  CheckSquare,
  ClipboardCheck,
  KeyRound,
  ListChecks,
  LogOut,
  Map as MapIcon,
  Search,
  Settings,
  Upload,
  User as UserIcon,
  Users,
} from "lucide-react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useOutlet } from "react-router";
import { toast } from "sonner";

import { api, unwrap } from "@/api/client";
import { keys } from "@/api/queries";
import { taskResult } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";
import { PageTransition } from "@/components/motion";
import { TaskProgress } from "@/components/TaskProgress";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { untrackTask, useTrackedTaskIds } from "@/hooks/taskTracker";
import { invalidateForTask, taskLabel, useActiveTasks } from "@/hooks/useTasks";
import { cn } from "@/lib/utils";
import { ConceptModalProvider } from "@/components/ConceptModal";

type NavItem = {
  to: string;
  label: string;
  icon: typeof Upload;
  hint?: string;
  step?: number;
  adminOnly?: boolean;
};

// The mapping workflow in order, then tools and administration
const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Workflow",
    items: [
      { to: "/import", label: "Import data", icon: Upload, step: 1, hint: "Vocabularies, source terms, embeddings" },
      { to: "/map", label: "Map concepts", icon: MapIcon, step: 2, hint: "Auto-map with AI or map by hand" },
      { to: "/review", label: "Review", icon: ListChecks, step: 3, hint: "Accept or reject AI suggestions" },
      { to: "/commit", label: "Check & commit", icon: ClipboardCheck, step: 4, hint: "Verify and export mappings" },
    ],
  },
  { title: "Tools", items: [{ to: "/search", label: "Search concepts", icon: Search }] },
  {
    title: "Administration",
    items: [
      { to: "/settings", label: "Configuration", icon: Settings },
      { to: "/users", label: "Users", icon: Users, adminOnly: true },
    ],
  },
];

function pollTask(id: string) {
  return {
    queryKey: keys.task(id),
    queryFn: async () => unwrap(await api.GET("/api/tasks/{task_id}", { params: { path: { task_id: id } } })),
    refetchInterval: (query: { state: { data?: Task } }) => (query.state.data?.is_terminal ? false : 1000),
    retry: false,
  };
}

/** Watches background tasks; when one finishes, refreshes the data it changed and notifies once.
 *
 * Two sources: tasks started from this tab (polled until they finish, so even a task that ends
 * before the next refresh of the active list is noticed) and the shared active-task list (tasks
 * started elsewhere, or before a reload), whose finished members are fetched once. */
function useTaskWatcher() {
  const qc = useQueryClient();
  const active = useActiveTasks();
  const tracked = useTrackedTaskIds();
  const trackedTasks = useQueries({ queries: tracked.map(pollTask) });
  const announced = useRef(new Set<string>());
  const previous = useRef<Set<string> | null>(null);

  const finish = useCallback(
    (task: Task) => {
      untrackTask(task.id);
      if (announced.current.has(task.id)) return;
      announced.current.add(task.id);
      qc.setQueryData(keys.task(task.id), task);
      invalidateForTask(qc, task);
      const label = taskLabel(task.kind);
      const automap = taskResult(task, "automap.run");
      if (task.status === "FAILURE") toast.error(`${label} failed`, { description: task.error ?? undefined });
      else if (task.status === "CANCELLED") toast.message(`${label} cancelled`);
      else if (automap && (automap.errors ?? 0) > 0)
        toast.warning(`${label} finished with errors`, {
          description: `${automap.errors} of ${automap.processed} concepts could not be processed (see the worker log).`,
        });
      else toast.success(`${label} finished`);
    },
    [qc],
  );

  // Tasks started from this tab
  useEffect(() => {
    for (const result of trackedTasks) {
      if (result.data?.is_terminal) finish(result.data);
    }
  }, [trackedTasks, finish]);

  // Tasks that left the active list since the last poll
  useEffect(() => {
    if (!active.data) return;
    const current = new Set(active.data.map((t) => t.id));
    const before = previous.current;
    previous.current = current;
    if (!before) return;
    for (const id of before) {
      if (current.has(id) || announced.current.has(id)) continue;
      void qc
        .fetchQuery(pollTask(id))
        .then((task) => task.is_terminal && finish(task))
        .catch(() => {
          /* the task disappeared; nothing to refresh */
        });
    }
  }, [active.data, qc, finish]);

  return active.data ?? [];
}

function TaskTray() {
  const tasks = useTaskWatcher();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="relative gap-2">
          <CheckSquare className="size-4" />
          Tasks
          {tasks.length > 0 && (
            <>
              <Badge className="h-5 min-w-5 px-1.5">{tasks.length}</Badge>
              {/* Something is running: a soft pulse on the button */}
              <span className="absolute -top-1 -right-1 flex size-3" aria-hidden>
                <span className="bg-primary absolute inline-flex size-full animate-ping rounded-full opacity-60 motion-reduce:animate-none" />
                <span className="bg-primary relative inline-flex size-3 rounded-full" />
              </span>
            </>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96">
        <div className="mb-2 text-sm font-medium">Running tasks</div>
        {tasks.length === 0 ? (
          <div className="text-muted-foreground text-sm">Nothing is running.</div>
        ) : (
          <div className="space-y-4">
            {tasks.map((task) => (
              <div key={task.id} className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium">{taskLabel(task.kind)}</span>
                  {task.username && <span className="text-muted-foreground">by {task.username}</span>}
                </div>
                <TaskProgress task={task} compact />
              </div>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const [passwordOpen, setPasswordOpen] = useState(false);
  if (!user) return null;
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="gap-2">
            <UserIcon className="size-4" />
            <span className="hidden sm:inline">{user.username}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuLabel className="flex items-center justify-between">
            {user.username}
            <Badge variant={user.role === "admin" ? "default" : "secondary"}>{user.role}</Badge>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setPasswordOpen(true)}>
            <KeyRound /> Change password
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void logout()}>
            <LogOut /> Log out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChangePasswordDialog open={passwordOpen} onOpenChange={setPasswordOpen} />
    </>
  );
}

function SidebarLink({ item }: { item: NavItem }) {
  const { to, label, icon: Icon, hint, step } = item;
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
          isActive ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60",
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <motion.div
              layoutId="nav-active"
              className="bg-sidebar-accent absolute inset-0 rounded-lg"
              transition={{ type: "spring", stiffness: 500, damping: 38 }}
            />
          )}
          <span
            className={cn(
              "relative flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold transition-colors",
              isActive
                ? "bg-brand text-white shadow-[var(--shadow-glow)]"
                : "bg-muted text-muted-foreground group-hover:text-foreground",
            )}
          >
            {step ?? <Icon className="size-4" />}
          </span>
          <span className="relative min-w-0">
            <span className={cn("block truncate", isActive && "font-semibold")}>{label}</span>
            {hint && <span className="text-muted-foreground block truncate text-[11px] leading-tight">{hint}</span>}
          </span>
        </>
      )}
    </NavLink>
  );
}

/** The routed page, animated in when the path changes (query changes such as tabs do not animate). */
function AnimatedOutlet() {
  const location = useLocation();
  const outlet = useOutlet();
  return (
    <AnimatePresence mode="wait" initial={false}>
      <PageTransition key={location.pathname}>{outlet}</PageTransition>
    </AnimatePresence>
  );
}

export function Layout() {
  const { isAdmin } = useAuth();
  const groups = NAV_GROUPS.map((g) => ({ ...g, items: g.items.filter((i) => !i.adminOnly || isAdmin) })).filter(
    (g) => g.items.length > 0,
  );
  const items = groups.flatMap((g) => g.items);

  return (
    <ConceptModalProvider>
      <div className="flex min-h-screen">
        <aside className="bg-sidebar border-sidebar-border sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r md:flex">
          <div className="flex h-16 items-center gap-2.5 border-b px-5">
            <div className="bg-brand flex size-8 items-center justify-center rounded-lg text-white shadow-[var(--shadow-glow)]">
              <MapIcon className="size-4" />
            </div>
            <div className="leading-tight">
              <div className="font-semibold">Auto OMOP Mapper</div>
              <div className="text-muted-foreground text-[11px]">Source terms → OMOP concepts</div>
            </div>
          </div>
          <LayoutGroup>
            <nav className="flex flex-1 flex-col gap-5 overflow-y-auto p-3">
              {groups.map((group) => (
                <div key={group.title} className="space-y-1">
                  <div className="text-muted-foreground px-3 pb-1 text-[11px] font-semibold tracking-wider uppercase">
                    {group.title}
                  </div>
                  {group.items.map((item) => (
                    <SidebarLink key={item.to} item={item} />
                  ))}
                </div>
              ))}
            </nav>
          </LayoutGroup>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="bg-background/80 sticky top-0 z-20 flex h-16 items-center gap-2 border-b px-4 backdrop-blur-md">
            <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto md:hidden">
              {items.map(({ to, label, step }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    cn(
                      "rounded-md px-2 py-1 text-sm whitespace-nowrap transition-colors",
                      isActive && "bg-accent text-accent-foreground font-medium",
                    )
                  }
                >
                  {step ? `${step}. ` : ""}
                  {label}
                </NavLink>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-2">
              <TaskTray />
              <UserMenu />
            </div>
          </header>
          <main className="mx-auto w-full max-w-[1600px] flex-1 p-4 md:p-8">
            <AnimatedOutlet />
          </main>
        </div>
      </div>
    </ConceptModalProvider>
  );
}
