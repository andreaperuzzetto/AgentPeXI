/**
 * lib/api.ts — Utility per chiamare il backend FastAPI.
 *
 * Legge il token JWT dal cookie `access_token` (server-side tramite cookies())
 * oppure dal localStorage (client-side) per le chiamate fetch lato browser.
 * Tutte le funzioni sono safe lato server (Next.js Server Components) e lato client.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

// ---------------------------------------------------------------------------
// Helper fetch con auth
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  let token: string | undefined

  if (typeof window === "undefined") {
    // Server Component — legge il cookie tramite next/headers
    const { cookies } = await import("next/headers")
    const jar = await cookies()
    token = jar.get("access_token")?.value
  } else {
    // Client Component — legge da cookie document
    token = document.cookie
      .split("; ")
      .find((c) => c.startsWith("access_token="))
      ?.split("=")[1]
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  }
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText }))
    throw new Error(err?.detail?.message ?? err?.message ?? `HTTP ${res.status}`)
  }

  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function apiLogin(email: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    credentials: "include",
  })
  if (!res.ok) throw new Error("Credenziali non valide")
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PipelineStats {
  leads_total: number
  leads_qualified: number
  deals_active: number
  deals_awaiting_gate: number
  deals_in_delivery: number
  deals_delivered: number
  deals_by_service: Record<string, number>
  revenue_delivered_eur: number
  revenue_pipeline_eur: number
}

export interface RunSummary {
  run_id: string
  deal_id: string | null
  status: string
  gate_type: string | null
  awaiting_gate_since: string | null
  current_phase: string | null
  current_agent: string | null
  started_at: string | null
}

export interface RunDetail extends RunSummary {
  task_history: TaskSummary[]
}

export interface TaskSummary {
  id: string
  type: string
  agent: string
  status: string
  created_at: string
  updated_at: string
  error_code: string | null
}

export interface Deal {
  id: string
  lead_id: string
  client_id: string | null
  service_type: string
  sector: string
  status: string
  estimated_value_eur: number | null
  proposal_human_approved: boolean
  kickoff_confirmed: boolean
  delivery_approved: boolean
  consulting_approved: boolean
  created_at: string
}

export interface Lead {
  id: string
  business_name: string
  address: string | null
  city: string | null
  website_url: string | null
  google_rating: number | null
  qualified: boolean
  score: number | null
  service_type: string | null
  status: string
}

export interface Proposal {
  id: string
  deal_id: string
  version: number
  storage_key: string
  presigned_url: string | null
  client_response: string | null
  portal_link_token: string | null
  portal_link_expires: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export const getStats = () => apiFetch<PipelineStats>("/stats/pipeline")

export const listRuns = (params?: { status?: string; deal_id?: string }) => {
  const qs = new URLSearchParams()
  if (params?.status) qs.set("status", params.status)
  if (params?.deal_id) qs.set("deal_id", params.deal_id)
  return apiFetch<{ items: RunSummary[]; total: number }>(`/runs?${qs}`)
}

export const getRun = (runId: string) => apiFetch<RunDetail>(`/runs/${runId}`)

export const listDeals = (params?: { status?: string }) => {
  const qs = new URLSearchParams()
  if (params?.status) qs.set("status", params.status)
  return apiFetch<{ items: Deal[] }>(`/deals?${qs}`)
}

export const getDeal = (id: string) => apiFetch<Deal>(`/deals/${id}`)

export const listLeads = () => apiFetch<{ items: Lead[]; total: number }>("/leads")

export const listTasks = (params?: { deal_id?: string; run_id?: string }) => {
  const qs = new URLSearchParams()
  if (params?.deal_id) qs.set("deal_id", params.deal_id)
  if (params?.run_id) qs.set("run_id", params.run_id)
  return apiFetch<{ tasks: TaskSummary[]; total: number }>(`/tasks?${qs}`)
}

export const getProposal = (id: string) => apiFetch<Proposal>(`/proposals/${id}`)

export const listProposals = (dealId: string) =>
  apiFetch<{ proposals: Proposal[] }>(`/proposals?deal_id=${dealId}`)

export const startRun = (payload: { zone?: string; deal_id?: string; [k: string]: unknown }) =>
  apiFetch<{ run_id: string }>("/runs", { method: "POST", body: JSON.stringify(payload) })

// Gate approval (operatore)
export const approveGate = (runId: string, gateType: string) =>
  apiFetch(`/runs/${runId}/gates/${gateType}/approve`, { method: "POST", body: "{}" })
