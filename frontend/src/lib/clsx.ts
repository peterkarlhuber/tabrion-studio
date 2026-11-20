// Copied from axentra-studio src/lib/clsx.ts at caa52b5 (proprietary, © Levuvo); unchanged.
type ClassValue = string | false | null | undefined | 0;

/** Join truthy class names. Tiny local helper instead of a dependency. */
export default function clsx(...values: ClassValue[]): string {
  return values.filter(Boolean).join(' ');
}
