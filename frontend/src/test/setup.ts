import '@testing-library/jest-dom/vitest';

import '../i18n';

// jsdom has no HTMLDialogElement.showModal()/close(); src/components/ui/Dialog.tsx falls back to the `open`
// attribute, and the real modality is checked in a browser (e2e/dialogs.spec.ts).
