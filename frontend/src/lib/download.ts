/**
 * Hand a file to the browser's download (TD-036, the diagnosis report): a hidden link to an object URL with the
 * `download` name, clicked once and removed. The object URL is released shortly after, once the browser has taken the
 * file. Nothing is opened or shown in the page.
 */
export type SaveFile = (blob: Blob, filename: string) => void;

/** How long the object URL stays valid after the click (the browser reads it when the download starts). */
const RELEASE_MS = 10_000;

export const saveFile: SaveFile = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), RELEASE_MS);
};
