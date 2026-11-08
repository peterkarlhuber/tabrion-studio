import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import { startHeartbeat } from './heartbeat';
import './styles.css';

const root = document.getElementById('root');
if (root === null) {
  throw new Error('Tabrion Studio: the #root element is missing from index.html');
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Keeps the local server alive while this tab is open (idle exit, 03 section 4.1).
startHeartbeat();
