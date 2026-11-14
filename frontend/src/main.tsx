import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import './i18n';
import { removeServiceWorkers } from './serviceWorkers';
import { startSession } from './session';
import './styles/global.css';
import { installTooltipDismiss } from './lib/tooltips';
import { initTheme } from './theme';

const root = document.getElementById('root');
if (root === null) {
  throw new Error('Tabrion Studio: the #root element is missing from index.html');
}

initTheme();
installTooltipDismiss();
// A-01: Studio registers no service worker; one found on this origin was left by another program and is removed.
void removeServiceWorkers();

// Redeem the one-time launch token first (it is removed from the address bar at once), then render. With a session
// the App sends the heartbeat that keeps the local server alive while this tab is open (idle exit, 03 section 4.1).
void startSession().then((session) => {
  createRoot(root).render(
    <StrictMode>
      <App session={session} />
    </StrictMode>,
  );
});
