/**
 * app/api/events/route.ts
 * SSE proxy: riceve eventi dal FastAPI backend e li inoltra al browser.
 * Usato da AgentActivityFeed per aggiornamenti real-time.
 */

import { cookies } from "next/headers"
import { NextRequest } from "next/server"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export async function GET(request: NextRequest) {
  const runId = request.nextUrl.searchParams.get("run_id")
  if (!runId) {
    return new Response("Missing run_id", { status: 400 })
  }

  const jar = await cookies()
  const token = jar.get("access_token")?.value
  if (!token) {
    return new Response("Not authenticated", { status: 401 })
  }

  const upstream = await fetch(`${API_BASE}/runs/${runId}/events`, {
    headers: { Cookie: `access_token=${token}` },
  })

  if (!upstream.ok || !upstream.body) {
    return new Response("Upstream error", { status: 502 })
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
      Connection: "keep-alive",
    },
  })
}
