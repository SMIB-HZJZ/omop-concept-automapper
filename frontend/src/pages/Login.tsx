import { CheckCircle2, Loader2, LogIn, Map as MapIcon } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router";

import { api, unwrap } from "@/api/client";
import { useAuthOptions } from "@/api/queries";
import { useAuth } from "@/auth/useAuth";
import { ErrorAlert } from "@/components/common";
import { Logos } from "@/components/Logos";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Mode = "signin" | "register";

/** Why the organisation sign-in (OIDC) sent the person back here (/login?error=...). */
const OIDC_MESSAGES: Record<string, { text: string; ok?: boolean }> = {
  pending: {
    text: "Your account is created and waits for an administrator's approval. Sign in again once it is approved.",
    ok: true,
  },
  disabled: { text: "Your account is deactivated. Ask an administrator to activate it." },
  cancelled: { text: "Sign-in was cancelled." },
  expired: { text: "The sign-in took too long or was started in another window. Please try again." },
  conflict: {
    text: "An account with your email already exists but is not linked to your organisation account. Ask an administrator.",
  },
  provider: {
    text: "Sign-in with the organisation account failed. Try again; if it keeps failing, tell an administrator.",
  },
};

function OidcSignIn() {
  const [params] = useSearchParams();
  const message = OIDC_MESSAGES[params.get("error") ?? ""];
  return (
    <div className="space-y-4">
      {message && (
        <div
          role={message.ok ? "status" : "alert"}
          className={
            message.ok
              ? "border-success/40 bg-success/10 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm"
              : "border-destructive/40 bg-destructive/10 text-destructive rounded-lg border px-3 py-2 text-sm"
          }
        >
          {message.ok && <CheckCircle2 className="text-success mt-0.5 size-4 shrink-0" />}
          <span>{message.text}</span>
        </div>
      )}
      {/* A full page load: the backend redirects to the organisation's sign-in page */}
      <Button asChild variant="cta" className="w-full">
        <a href="/api/auth/oidc/login">
          <LogIn /> Sign in with your organisation account
        </a>
      </Button>
      <p className="text-muted-foreground text-center text-xs">
        New accounts can be used once an administrator approves them.
      </p>
    </div>
  );
}

export function LoginPage() {
  const { user, login } = useAuth();
  const options = useAuthOptions();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<Mode>("signin");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  // Shown after a registration: the account exists but waits for an admin
  const [registered, setRegistered] = useState<string | null>(null);
  const from = (location.state as { from?: string } | null)?.from ?? "/map";
  const canRegister = options.data?.self_registration ?? false;
  const oidc = options.data?.auth_mode === "oidc";

  if (user) return <Navigate to={from} replace />;

  const switchTo = (next: Mode) => {
    setMode(next);
    setError(null);
    setPassword("");
    setRepeat("");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (mode === "register" && password !== repeat) {
      setError(new Error("The two passwords differ"));
      return;
    }
    setBusy(true);
    try {
      if (mode === "signin") {
        await login(username, password);
        navigate(from, { replace: true });
      } else {
        const res = unwrap(await api.POST("/api/auth/register", { body: { email: username.trim(), password } }));
        setRegistered(res.message);
        switchTo("signin");
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const registering = mode === "register";

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-4">
      {/* Soft brand glow behind the card */}
      <div
        aria-hidden
        className="bg-brand pointer-events-none absolute top-1/2 left-1/2 size-[36rem] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-15 blur-3xl"
      />
      <motion.div
        className="relative w-full max-w-sm"
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
      >
        <Card className="w-full shadow-[var(--shadow-lift)]">
          <CardHeader className="text-center">
            <div className="bg-brand mx-auto mb-3 flex size-12 items-center justify-center rounded-2xl text-white shadow-[var(--shadow-glow)]">
              <MapIcon className="size-6" />
            </div>
            <CardTitle className="text-xl">{registering ? "Create an account" : "Auto OMOP Mapper"}</CardTitle>
            <CardDescription>
              {registering
                ? "An administrator approves new accounts before they can sign in"
                : "Sign in to map source terms to OMOP concepts"}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {options.isPending ? (
              <div className="flex justify-center py-6" aria-label="Loading">
                <Loader2 className="text-muted-foreground size-5 animate-spin" />
              </div>
            ) : oidc ? (
              <OidcSignIn />
            ) : (
              <>
                {registered && !registering && (
                  <div
                    role="status"
                    className="border-success/40 bg-success/10 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm"
                  >
                    <CheckCircle2 className="text-success mt-0.5 size-4 shrink-0" />
                    <span>{registered}</span>
                  </div>
                )}
                <form onSubmit={submit} className="space-y-4">
                  <div className="grid gap-2">
                    <Label htmlFor="username">
                      {registering ? "Email" : canRegister ? "Email or username" : "Username"}
                    </Label>
                    <Input
                      id="username"
                      type={registering ? "email" : "text"}
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      autoComplete={registering ? "email" : "username"}
                      maxLength={registering ? 64 : 150}
                      autoFocus
                      required
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="password">{registering ? "Password (min. 8 characters)" : "Password"}</Label>
                    <Input
                      id="password"
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      autoComplete={registering ? "new-password" : "current-password"}
                      minLength={registering ? 8 : undefined}
                      required
                    />
                  </div>
                  {registering && (
                    <div className="grid gap-2">
                      <Label htmlFor="password-repeat">Repeat the password</Label>
                      <Input
                        id="password-repeat"
                        type="password"
                        value={repeat}
                        onChange={(e) => setRepeat(e.target.value)}
                        autoComplete="new-password"
                        minLength={8}
                        required
                      />
                    </div>
                  )}
                  <ErrorAlert error={error} title={registering ? "Could not create the account" : "Sign-in failed"} />
                  <Button type="submit" variant="cta" className="w-full" disabled={busy}>
                    {busy ? (
                      <>
                        <Loader2 className="animate-spin" /> {registering ? "Creating…" : "Signing in…"}
                      </>
                    ) : registering ? (
                      "Create account"
                    ) : (
                      "Sign in"
                    )}
                  </Button>
                </form>
                {canRegister && (
                  <p className="text-muted-foreground text-center text-sm">
                    {registering ? "Already have an account? " : "No account yet? "}
                    <button
                      type="button"
                      className="text-primary cursor-pointer font-medium hover:underline"
                      onClick={() => switchTo(registering ? "signin" : "register")}
                    >
                      {registering ? "Sign in" : "Create one"}
                    </button>
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>
        <Logos large className="mt-4 shadow-[var(--shadow-soft)]" />
      </motion.div>
    </div>
  );
}
