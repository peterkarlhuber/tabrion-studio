# STU-ADR-0004: Reuse instead of new builds in the frontend

**Status:** needs E-S4 (dagre instead of elkjs, recommended) and E-S6 (copy components from axentra-studio instead
of a shared package, recommended).

## Decision

| Purpose | Choice (license) |
|---|---|
| Framework, primitives, data | React 19, TypeScript 5.9, Vite; Radix UI; TanStack Query 5; openapi-typescript (MIT) |
| SQL editor | CodeMirror 6 + `@codemirror/lang-sql` (MIT) |
| Grid | TanStack Table 8 + TanStack Virtual (MIT) |
| ER diagram (1.0) | @xyflow/react 12 + dagre (MIT); no elkjs (EPL-2.0) |
| i18n, icons, formatting | i18next/react-i18next (MIT), lucide-react (ISC), sql-formatter (MIT) |
| Tests | Vitest, Testing Library, Playwright, @axe-core/playwright (MPL-2.0, dev only) |
| Components from axentra-studio | copied with a provenance note and a yearly comparison; no shared UI package |
| License check | automatic (STU-031): permissive allowlist, MPL-2.0 dev only; Python runtime closure checked as well |

## State in this repository

The skeleton has React 19.3, TypeScript 5.9 and Vite 8 at the versions of axentra-studio, with the same lint and
format setup. STU-005 added i18next/react-i18next, lucide-react, Playwright and @axe-core/playwright (dev) at the
versions of axentra-studio, and copied Button, IconButton, EmptyState and Dialog from
`axentra-studio/src/components/ui/` at `caa52b5`, each with a provenance note. **Deviation:** the dialog uses the
native modal `<dialog>` instead of Radix Dialog, because Radix's scroll lock injects a `<style>` element that the
strict CSP of STU-003 (`style-src 'self'`) refuses (`docs/architecture/frontend.md`). STU-012 added CodeMirror 6
(`@codemirror/view`, `state`, `language`, `lang-sql`, `autocomplete`), `@uiw/react-codemirror` and
`@tanstack/react-virtual`, all approved in doc 05; CodeMirror is mounted in a shadow root for the same CSP reason.
The other libraries arrive with the work packages that need them. The license check is in place (`scripts/check_licenses.py`).
