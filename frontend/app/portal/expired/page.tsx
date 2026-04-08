export default function PortalExpiredPage() {
  const operatorEmail = process.env.OPERATOR_EMAIL ?? "info@agentpexi.it"
  const operatorPhone = process.env.OPERATOR_PHONE ?? ""

  return (
    <div className="min-h-screen bg-white flex items-center justify-center p-4">
      <div className="max-w-md text-center space-y-5">
        <div className="w-16 h-16 rounded-full bg-amber-100 flex items-center justify-center mx-auto">
          <span className="text-3xl">⏱</span>
        </div>
        <h1 className="text-2xl font-semibold text-gray-900">Link scaduto</h1>
        <p className="text-gray-500 text-sm leading-relaxed">
          Questo link è scaduto o è già stato utilizzato.
          <br />
          Contattaci per ricevere una nuova proposta.
        </p>
        <div className="space-y-2">
          {operatorEmail && (
            <a
              href={`mailto:${operatorEmail}`}
              className="block text-sm text-blue-600 hover:text-blue-800 underline"
            >
              {operatorEmail}
            </a>
          )}
          {operatorPhone && (
            <p className="text-sm text-gray-600">{operatorPhone}</p>
          )}
        </div>
      </div>
    </div>
  )
}
