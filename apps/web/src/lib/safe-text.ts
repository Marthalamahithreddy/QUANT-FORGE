/**
 * Defence in depth for user-visible error text.
 *
 * The error primitives never print an internal address: any `http(s)://` URL in a message is
 * replaced before it reaches the page. The real fix is at the source (`failureCopy` in api/messages.ts
 * is built from constants); this guarantees a future message cannot reintroduce the leak.
 */

// A URL, or a scheme-less `host:port[/path]` (localhost, an IPv4 address or a dotted hostname).
const URL_PATTERN =
  /\bhttps?:\/\/[^\s)"'<>]+|\b(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|[a-z0-9-]+(?:\.[a-z0-9-]+)+):\d{2,5}[^\s)"'<>]*|\blocalhost\b[^\s)"'<>]*/gi;

export const REDACTED_ADDRESS = "[service address]";

export function redactUrls(text: string): string {
  return text.replace(URL_PATTERN, REDACTED_ADDRESS);
}
