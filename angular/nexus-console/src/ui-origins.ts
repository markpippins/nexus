/**
 * Origins for the UIs the console embeds in iframes.
 *
 * Those apps are published on the same machine as the console, each on its own
 * port. Hardcoding `http://localhost:<port>` only works when the browser is
 * running on that machine: for any remote browser `localhost` resolves to the
 * viewer's own computer, so the frame loads nothing (or something unrelated).
 * Derive the host from the page doing the embedding instead.
 */

export interface OriginLoc {
  hostname?: string;
  protocol?: string;
}

export function uiOriginFor(port: number, loc?: OriginLoc): string {
  const location = loc ?? (typeof window !== 'undefined' ? window.location : undefined);
  const hostname = location?.hostname || 'localhost';
  const protocol = location?.protocol === 'https:' ? 'https:' : 'http:';
  return `${protocol}//${hostname}:${port}`;
}
