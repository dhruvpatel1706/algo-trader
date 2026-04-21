"use client";
import { api } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export function KillSwitch() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  async function execute() {
    if (confirm !== "FLATTEN") {
      setError('type "FLATTEN" exactly to confirm');
      return;
    }
    setPending(true);
    setError(null);
    try {
      await api.kill("manual kill from dashboard");
      await qc.invalidateQueries();
      setOpen(false);
      setConfirm("");
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded-md bg-danger px-3 py-1.5 text-sm font-semibold text-white shadow-md hover:bg-red-600 focus:outline-none focus:ring-2 focus:ring-danger"
        aria-label="kill switch — flatten and halt"
      >
        KILL
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-md rounded-lg border border-border bg-surface p-6 shadow-2xl">
            <h2 className="mb-2 text-lg font-bold text-danger">KILL — flatten & halt</h2>
            <p className="mb-4 text-sm text-muted">
              Cancels all open orders, liquidates all positions at market, halts every strategy,
              and writes an incident record under <code>live/incidents/</code>. This action
              cannot be undone.
            </p>
            <label className="block text-sm text-muted">
              Type <code className="rounded bg-bg px-1 py-0.5 text-zinc-200">FLATTEN</code> to
              confirm
            </label>
            <input
              autoFocus
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-bg px-3 py-2 text-zinc-100 focus:border-danger focus:outline-none"
            />
            {error && <p className="mt-2 text-sm text-danger">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  setOpen(false);
                  setConfirm("");
                  setError(null);
                }}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-border"
              >
                Cancel
              </button>
              <button
                onClick={execute}
                disabled={pending || confirm !== "FLATTEN"}
                className="rounded-md bg-danger px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
              >
                {pending ? "killing…" : "Confirm KILL"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
