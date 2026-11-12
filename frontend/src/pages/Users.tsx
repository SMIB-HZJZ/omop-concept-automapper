import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { KeyRound, Trash2, UserPlus } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { api, errorMessage, unwrap } from "@/api/client";
import { keys, useUsers } from "@/api/queries";
import type { Role, User } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ErrorAlert, PageHeader } from "@/components/common";
import { formatDate } from "@/lib/format";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable } from "@/components/DataTable";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

type UserPatch = { role?: Role; is_active?: boolean; password?: string };

function useUserMutations() {
  const qc = useQueryClient();
  const onSuccess = () => void qc.invalidateQueries({ queryKey: keys.users });
  const onError = (e: unknown) => toast.error(errorMessage(e));
  const create = useMutation({
    mutationFn: async (body: { username: string; password: string; role: Role }) =>
      unwrap(await api.POST("/api/users", { body })),
    onSuccess,
    onError,
  });
  const patch = useMutation({
    mutationFn: async ({ id, ...body }: UserPatch & { id: number }) =>
      unwrap(await api.PATCH("/api/users/{user_id}", { params: { path: { user_id: id } }, body })),
    onSuccess,
    onError,
  });
  const remove = useMutation({
    mutationFn: async (id: number) =>
      unwrap(await api.DELETE("/api/users/{user_id}", { params: { path: { user_id: id } } })),
    onSuccess,
    onError,
  });
  return { create, patch, remove };
}

function AddUserDialog({ create }: { create: ReturnType<typeof useUserMutations>["create"] }) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("user");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    create.mutate(
      { username, password, role },
      {
        onSuccess: (user) => {
          toast.success(`Created ${user.role} ${user.username}`);
          setOpen(false);
          setUsername("");
          setPassword("");
          setRole("user");
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <UserPlus /> Add user
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Add user</DialogTitle>
            <DialogDescription>
              Users map concepts; admins also import data, manage embeddings, settings and users.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="new-username">Username</Label>
            <Input
              id="new-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              maxLength={64}
              pattern="^[A-Za-z0-9_.@\-]+$"
              title="Letters, digits and _ . @ -"
              autoComplete="off"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="new-user-password">Password (min. 8 characters)</Label>
            <Input
              id="new-user-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete="new-password"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="new-user-role">Role</Label>
            <Select value={role} onValueChange={(v) => setRole(v as Role)}>
              <SelectTrigger id="new-user-role" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="user">user</SelectItem>
                <SelectItem value="admin">admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={create.isPending}>
              Create user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({ user, patch }: { user: User; patch: ReturnType<typeof useUserMutations>["patch"] }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    patch.mutate(
      { id: user.id, password },
      {
        onSuccess: () => {
          toast.success(`Password reset for ${user.username}`);
          setOpen(false);
          setPassword("");
        },
      },
    );
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <KeyRound /> Reset password
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Reset password for {user.username}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor={`reset-${user.id}`}>New password (min. 8 characters)</Label>
            <Input
              id={`reset-${user.id}`}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete="new-password"
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={patch.isPending}>
              Set password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function UsersPage() {
  const { user: me } = useAuth();
  const users = useUsers();
  const { create, patch, remove } = useUserMutations();

  const columns = useMemo<ColumnDef<User, unknown>[]>(
    () => [
      {
        accessorKey: "username",
        header: "Username",
        cell: ({ row }) => (
          <span className="font-medium">
            {row.original.username}
            {row.original.id === me?.id && <span className="text-muted-foreground font-normal"> (you)</span>}
          </span>
        ),
      },
      {
        accessorKey: "role",
        header: "Role",
        cell: ({ row }) => (
          <Select
            value={row.original.role}
            disabled={row.original.id === me?.id || patch.isPending}
            onValueChange={(v) => patch.mutate({ id: row.original.id, role: v as Role })}
          >
            <SelectTrigger className="h-8 w-28" aria-label={`Role of ${row.original.username}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="user">user</SelectItem>
              <SelectItem value="admin">admin</SelectItem>
            </SelectContent>
          </Select>
        ),
      },
      {
        accessorKey: "is_active",
        header: "Active",
        cell: ({ row }) => (
          <Switch
            checked={row.original.is_active}
            disabled={row.original.id === me?.id || patch.isPending}
            onCheckedChange={(v) => patch.mutate({ id: row.original.id, is_active: v })}
            aria-label={`Active state of ${row.original.username}`}
          />
        ),
      },
      { accessorKey: "created_at", header: "Created", cell: ({ getValue }) => formatDate(getValue() as string | null) },
      {
        accessorKey: "last_login_at",
        header: "Last login",
        cell: ({ getValue }) => formatDate(getValue() as string | null),
      },
      {
        id: "actions",
        header: "",
        enableSorting: false,
        cell: ({ row }) => (
          <div className="flex justify-end gap-2">
            <ResetPasswordDialog user={row.original} patch={patch} />
            <ConfirmDialog
              trigger={
                <Button variant="ghost" size="sm" disabled={row.original.id === me?.id || remove.isPending}>
                  <Trash2 /> Delete
                </Button>
              }
              title={`Delete ${row.original.username}?`}
              description="The account is removed. Mappings made by this user are kept but no longer show who made them."
              confirmLabel="Delete"
              destructive
              onConfirm={() =>
                remove.mutate(row.original.id, { onSuccess: () => toast.success(`Deleted ${row.original.username}`) })
              }
            />
          </div>
        ),
      },
    ],
    [me?.id, patch, remove],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Users"
        description="Manage who can sign in and what they can do."
        actions={<AddUserDialog create={create} />}
      />
      <ErrorAlert error={users.error} title="Could not load users" />
      <DataTable
        columns={columns}
        data={users.data ?? []}
        getRowId={(u) => String(u.id)}
        isLoading={users.isLoading}
        emptyText="No users"
      />
    </div>
  );
}
