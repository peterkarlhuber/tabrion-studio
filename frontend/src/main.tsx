import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import { startHeartbeat } from './heartbeat';
import { startSession } from './session';
import './styles.css';

const root = document.getElementById('root');
if (root === null) {
  throw new Error('Tabrion Studio: the #root element is missing from index.html');
}

// Redeem the one-time launch token first (it is removed from the address bar at once), then render.
void startSession().then((session) => {
  createRoot(root).render(
    <StrictMode>
      <App session={session} />
    </StrictMode>,
  );
  if (session === 'ok') {
    // Keeps the local server alive while this tab is open (idle exit, 03 section 4.1).
    startHeartbeat();
  }
});
