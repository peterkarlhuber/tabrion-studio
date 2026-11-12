/**
 * The SQL editor's actions for the command palette (08 section 1: "a command palette for everything"). The editor
 * page registers them while it is shown and removes them when it leaves; the palette lists them only then.
 */
export interface EditorActions {
  run: () => void;
  runScript: () => void;
  cancel: () => void;
  newTab: () => void;
  closeTab: () => void;
  save: () => void;
  /** Whether a query runs now (only then is "Cancel" offered). */
  running: () => boolean;
}

let current: EditorActions | null = null;

export function setEditorActions(actions: EditorActions | null): void {
  current = actions;
}

export function editorActions(): EditorActions | null {
  return current;
}
