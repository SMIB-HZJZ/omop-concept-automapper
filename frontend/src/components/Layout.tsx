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
import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { toast } from "sonner";

import { api, unwrap } from "@/api/client";
import { keys } from "@/api/queries";
import { taskResult } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";
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

const NAV = [
  { to: "/import", label: "Import Data", icon: Upload },
  { to: "/search", label: "Search", icon: Search },
  { to: "/map", label: "Map Concepts", icon: MapIcon },
  { to: "/review", label: "Review", icon: ListChecks },
  { to: "/commit", label: "Check & Commit", icon: ClipboardCheck },
  { to: "/settings", label: "Configuration", icon: Settings },
  { to: "/users", label: "Users", icon: Users, adminOnly: true },
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
        <Button variant="outline" size="sm" className="gap-2">
          <CheckSquare className="size-4" />
          Tasks
          {tasks.length > 0 && <Badge className="h-5 min-w-5 px-1.5">{tasks.length}</Badge>}
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

export function Layout() {
  const { isAdmin } = useAuth();
  const items = NAV.filter((item) => !item.adminOnly || isAdmin);

  return (
    <div className="flex min-h-screen">
      <aside className="bg-sidebar border-sidebar-border sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r md:flex">
        <div className="flex h-14 items-center gap-2 border-b px-5 font-semibold">
          <MapIcon className="text-primary size-5" />
          Auto OMOP Mapper
        </div>
        <nav className="flex flex-1 flex-col gap-1 p-3">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "text-sidebar-foreground/80 hover:bg-sidebar-accent flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive && "bg-sidebar-accent text-sidebar-accent-foreground font-medium",
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="bg-background/95 sticky top-0 z-20 flex h-14 items-center gap-2 border-b px-4 backdrop-blur">
          <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto md:hidden">
            {items.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn("rounded-md px-2 py-1 text-sm whitespace-nowrap", isActive && "bg-accent font-medium")
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <TaskTray />
            <UserMenu />
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1600px] flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
