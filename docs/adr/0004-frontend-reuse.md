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
format setup; the other libraries arrive with the work packages that need them (STU-005 onward). The license check
is in place (`scripts/check_licenses.py`).
