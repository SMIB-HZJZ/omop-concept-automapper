import { useState } from "react";
import { toast } from "sonner";

import { api, unwrap } from "@/api/client";
import { ErrorAlert } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ChangePasswordDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next !== confirm) return setError("The new passwords do not match");
    setBusy(true);
    try {
      unwrap(await api.POST("/api/auth/change-password", { body: { current_password: current, new_password: next } }));
      toast.success("Password changed");
      onOpenChange(false);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Change password</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="current-password">Current password</Label>
            <Input
              id="current-password"
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="new-password">New password (min. 8 characters)</Label>
            <Input
              id="new-password"
              type="password"
              minLength={8}
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
              autoComplete="new-password"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="confirm-password">Repeat new password</Label>
            <Input
              id="confirm-password"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              autoComplete="new-password"
            />
          </div>
          <ErrorAlert error={error} title="Could not change the password" />
          <DialogFooter>
            <Button type="submit" disabled={busy}>
              Change password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
