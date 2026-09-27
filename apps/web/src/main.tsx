import React from 'react'
import ReactDOM from 'react-dom/client'
import G1App from './G1App'
import DepositApp from './DepositApp'
import VoiceTestApp from './VoiceTestApp'
import FakeWhatsAppApp from './FakeWhatsAppApp'
import './styles.css'
import './features/insurer-review/insurer-review.css'
import './g1-app.css'
import './features/insurer-review/refined.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {window.location.pathname === '/depot' ? <DepositApp /> :
      window.location.pathname === '/voice-test' ? <VoiceTestApp /> :
        window.location.pathname === '/fake-whatsapp' ? <FakeWhatsAppApp /> : <G1App />}
  </React.StrictMode>,
)
