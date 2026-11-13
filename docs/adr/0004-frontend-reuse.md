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
STU-023 added `@xyflow/react` 12.12.0 and `@dagrejs/dagre` 3.1.3 for the ER diagram (03 section 3.2, this ADR's E-S4:
dagre, not elkjs). `@dagrejs/dagre` is dagre as its own maintainers publish it now (the `dagrejs` organization; the
package `dagre` 0.8.5 of 2019 is no longer released and pulls in lodash); both and their dependencies (`zustand`,
`classcat`, `@xyflow/system`, `d3-*`, `@types/d3-*`, `@dagrejs/graphlib`) are MIT, ISC or BSD-3-Clause and pass
the license check. `@types/react-dom` (MIT) is now a runtime dependency in the lockfile, because `@xyflow/react`
declares it, so it appears in the production SBOM; it is types only and never part of the bundle. React, xyflow and
d3 set inline styles through `element.style` (the CSSOM), which the CSP does not refuse, and none of them adds a
`<style>` element or a `style` attribute in the parts Studio uses. The diagram is loaded without `React.lazy`, so
that its `<select>` is in the document within the task that made it, as WebKit's report of its own select style
needs (TD-047, TD-052; `docs/architecture/explorer.md`, "The page").
The other libraries arrive with the work packages that need them. The license check is in place (`scripts/check_licenses.py`).
