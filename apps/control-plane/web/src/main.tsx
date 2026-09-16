import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import EmbedApp from './embed'
import OperatorApp from './operator'

const application = location.pathname === '/embed/v1' ? <EmbedApp /> : <OperatorApp />
createRoot(document.getElementById('root')!).render(<React.StrictMode>{application}</React.StrictMode>)
