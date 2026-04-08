"use client"

import { useState } from "react"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface RejectFormProps {
  token: string
}

export function RejectForm({ token }: RejectFormProps) {
  const [open, setOpen] = useState(false)
  const [notes, setNotes] = useState("")
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle")
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setState("loading")
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/webhooks/portal/client-reject`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ rejection_notes: notes || null }),
      })
      if (!res.ok) throw new Error(await res.text())
      setState("done")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Errore")
      setState("error")
    }
  }

  if (state === "done") {
    return (
      <div className="p-4 rounded-lg bg-gray-50 border border-gray-200 text-center">
        <p className="text-gray-700 text-sm">
          Grazie per il feedback. Potrete ricontattarci in qualsiasi momento.
        </p>
      </div>
    )
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full py-2.5 px-6 rounded-lg border border-gray-300 text-gray-600 text-sm font-medium hover:bg-gray-50 transition-colors"
      >
        Non approvo
      </button>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 p-4 rounded-lg border border-gray-200 bg-gray-50">
      <div>
        <label htmlFor="notes" className="block text-sm font-medium text-gray-700 mb-1.5">
          Note opzionali
        </label>
        <textarea
          id="notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          placeholder="Motivo del rifiuto o richiesta di modifiche..."
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-900 resize-none focus:outline-none focus:ring-2 focus:ring-gray-400"
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={state === "loading"}
          className="flex-1 py-2 rounded-md bg-gray-800 text-white text-sm font-medium hover:bg-gray-700 transition-colors disabled:opacity-50"
        >
          {state === "loading" ? "Invio..." : "Invia risposta"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="py-2 px-4 rounded-md border border-gray-300 text-gray-600 text-sm hover:bg-gray-100 transition-colors"
        >
          Annulla
        </button>
      </div>
    </form>
  )
}
