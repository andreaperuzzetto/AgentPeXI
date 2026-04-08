import { redirect } from "next/navigation"
import { verifyPortalToken } from "@/lib/auth"
import { getProposal } from "@/lib/api"
import type { Proposal } from "@/lib/api"
import { ApproveButton } from "./ApproveButton"
import { RejectForm } from "./RejectForm"

interface PortalPageProps {
  params: Promise<{ token: string }>
}

export default async function PortalPage({ params }: PortalPageProps) {
  const { token } = await params
  const claims = await verifyPortalToken(token)
  if (!claims) redirect("/portal/expired")

  let proposal: Proposal
  try {
    proposal = await getProposal(claims.proposal_id)
  } catch {
    redirect("/portal/expired")
  }

  if (proposal.client_response) {
    return (
      <AlreadyResponded
        response={proposal.client_response}
        gate={claims.gate}
      />
    )
  }

  const isDelivery = claims.gate === "delivery"

  return (
    <div className="min-h-screen bg-white text-gray-900">
      {/* Header */}
      <div className="bg-gray-50 border-b border-gray-200 px-4 py-5">
        <div className="max-w-3xl mx-auto">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">
            {isDelivery ? "Approvazione consegna" : "Proposta commerciale"}
          </p>
          <h1 className="text-2xl font-semibold text-gray-900">
            {isDelivery ? "Conferma la consegna" : "La vostra proposta"}
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Andrea Peruzzetto — AgentPeXI
          </p>
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-4 py-8 space-y-8">

        {/* PDF inline */}
        {proposal.presigned_url && (
          <div className="rounded-lg overflow-hidden border border-gray-200 shadow-sm">
            <iframe
              src={proposal.presigned_url}
              className="w-full"
              style={{ height: "70vh", minHeight: 400 }}
              title="Proposta PDF"
            />
          </div>
        )}

        {/* Riepilogo proposta */}
        <div className="bg-gray-50 rounded-lg p-5 border border-gray-200">
          <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-3">
            {isDelivery ? "Riepilogo lavoro svolto" : "Riepilogo proposta"}
          </h2>
          <p className="text-sm text-gray-600">
            {isDelivery
              ? "Il lavoro concordato è stato completato. Rivedi i materiali consegnati e confermaci l'approvazione per procedere."
              : "Abbiamo preparato una proposta personalizzata per la vostra attività. Potete visualizzarla qui sopra e procedere con l'approvazione o inviarci un feedback."}
          </p>
        </div>

        {/* Azioni */}
        {isDelivery ? (
          <div className="space-y-4">
            <ApproveButton token={token} gate="delivery" label="Confermo la consegna" />
          </div>
        ) : (
          <div className="space-y-4">
            <ApproveButton token={token} gate="proposal" label="Approvo la proposta" />
            <RejectForm token={token} />
          </div>
        )}
      </div>
    </div>
  )
}

function AlreadyResponded({ response, gate }: { response: string; gate: string }) {
  const approved = response === "approved"
  return (
    <div className="min-h-screen bg-white flex items-center justify-center p-4">
      <div className="max-w-md text-center space-y-4">
        <div className={`w-16 h-16 rounded-full flex items-center justify-center mx-auto ${approved ? "bg-green-100" : "bg-gray-100"}`}>
          <span className="text-3xl">{approved ? "✓" : "✕"}</span>
        </div>
        <h1 className="text-xl font-semibold text-gray-900">
          {approved
            ? (gate === "delivery" ? "Consegna confermata" : "Proposta approvata")
            : "Risposta ricevuta"}
        </h1>
        <p className="text-gray-500 text-sm">
          {approved
            ? "Grazie! Verrete ricontattati entro 24 ore per i dettagli."
            : "Grazie per il feedback. Potrete ricontattarci in qualsiasi momento."}
        </p>
      </div>
    </div>
  )
}
