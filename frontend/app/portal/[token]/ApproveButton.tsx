"use client"

import { useState } from "react"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface ApproveButtonProps {
  token: string
  gate: "proposal" | "delivery"
  label: string
}

export function ApproveButton({ token, gate, label }: ApproveButtonProps) {
  const [state, setState] = useState<"idle" | "confirming" | "loading" | "done" | "error">("idle")
  const [error, setError] = useState<string | null>(null)

  async function handleConfirm() {
    setState("loading")
    setError(null)
    const endpoint =
      gate === "delivery"
        ? "/webhooks/portal/client-delivery-confirm"
        : "/webhooks/portal/client-approve"

    try {
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({}),
      })
      if (!res.ok) throw new Error(await res.text())
      setState("done")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Errore sconosciuto")
      setState("error")
    }
  }

  if (state === "done") {
    return (
      <div className="p-4 rounded-lg bg-green-50 border border-green-200 text-center">
        <p className="text-green-700 font-medium">
          {gate === "delivery"
            ? "Perfetto! La consegna è stata confermata."
            : "Perfetto! Verrete contattati entro 24 ore per definire i dettagli."}
        </p>
      </div>
    )
  }

  if (state === "confirming") {
    return (
      <div className="p-4 rounded-lg bg-blue-50 border border-blue-200 space-y-3">
        <p className="text-blue-800 text-sm font-medium">
          Confermi di voler procedere con l&#39;approvazione?
        </p>
        <div className="flex gap-3">
          <button
            onClick={handleConfirm}
            className="flex-1 py-2 px-4 rounded-md bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            Sì, confermo
          </button>
          <button
            onClick={() => setState("idle")}
            className="flex-1 py-2 px-4 rounded-md border border-gray-300 text-gray-700 text-sm font-medium hover:bg-gray-50 transition-colors"
          >
            Annulla
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <button
        onClick={() => setState("confirming")}
        className="w-full py-3 px-6 rounded-lg bg-green-600 text-white font-semibold text-base hover:bg-green-700 transition-colors shadow-sm"
      >
        {label}
      </button>
      {state === "error" && error && (
        <p className="text-sm text-red-600 text-center">{error}</p>
      )}
    </div>
  )
}
