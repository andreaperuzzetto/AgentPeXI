/**
 * lib/auth.ts — Autenticazione operatore e verifica token portale.
 */

import { SignJWT, jwtVerify } from "jose"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

// ---------------------------------------------------------------------------
// Operatore
// ---------------------------------------------------------------------------

/**
 * Login operatore — POST /auth/token → imposta cookie access_token.
 * Usato dal Client Component nella pagina /login.
 */
export async function login(email: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    credentials: "include",
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err?.detail?.message ?? "Credenziali non valide")
  }
  // Il backend imposta il cookie httpOnly — nessuna azione lato client necessaria
}

export function logout(): void {
  // Cancella cookie lato client (il backend dovrebbe avere un endpoint /auth/logout)
  document.cookie = "access_token=; Max-Age=0; path=/"
}

/**
 * Legge il token JWT dal cookie lato client.
 */
export function getToken(): string | undefined {
  if (typeof document === "undefined") return undefined
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith("access_token="))
    ?.split("=")[1]
}

/**
 * Verifica se l'utente è autenticato (lato client — solo controlla presenza cookie).
 */
export function isAuthenticated(): boolean {
  return !!getToken()
}

// ---------------------------------------------------------------------------
// Portale cliente — verifica JWT lato server (Server Component)
// ---------------------------------------------------------------------------

export interface PortalClaims {
  proposal_id: string
  deal_id: string
  gate: "proposal" | "delivery"
  exp: number
  iat: number
  type: "portal_access"
}

/**
 * Verifica il JWT del portale cliente.
 * Da chiamare SOLO in Server Components — mai lato client.
 * Restituisce i claims se valido, null se scaduto/invalido.
 */
export async function verifyPortalToken(token: string): Promise<PortalClaims | null> {
  const secret = process.env.PORTAL_SECRET_KEY
  if (!secret) {
    console.error("auth: PORTAL_SECRET_KEY non configurata")
    return null
  }

  try {
    const key = new TextEncoder().encode(secret)
    const { payload } = await jwtVerify(token, key, { algorithms: ["HS256"] })
    return payload as unknown as PortalClaims
  } catch {
    return null
  }
}
