import { createRoot } from 'react-dom/client'
import './styles/globals.css'
import { Shell } from './layout/Shell'

// StrictMode rimosso: in development montava il componente due volte,
// creando due WebSocket simultanee verso il backend vocale.
createRoot(document.getElementById('root')!).render(
  <Shell />,
)
