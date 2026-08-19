// Pure text helpers shared by the console's renderers.
//
// Separate from console.js because console.js touches `document` at import
// time: these are the parts that can be exercised on their own (see
// tests/flux/agents/console/test_web_text.py), rather than pinned at the
// source with a regex like the DOM-bound code has to be.

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** Size of `text` in the units the wire and the size label both use. */
export function byteSize(text) {
  return encoder.encode(String(text)).length;
}

/** `text` cut to at most `limit` UTF-8 bytes, never mid-character.
 *
 * Byte-based rather than by string length: a limit meant as "2 KB" that
 * counts UTF-16 units cuts a Cyrillic or emoji payload at several times
 * the budget it advertises, and the size label beside it -- which does
 * count bytes -- then disagrees with the cut the reader is looking at.
 */
export function sliceBytes(text, limit) {
  const value = String(text);
  const bytes = encoder.encode(value);
  if (bytes.length <= limit) return value;
  // UTF-8 is self-synchronizing: while the byte just past the cut is a
  // continuation byte (10xxxxxx) the cut splits a character, so step back.
  // At most three steps. Deliberately not "decode and strip U+FFFD" -- that
  // also eats a replacement character the payload genuinely contained.
  let end = limit;
  while (end > 0 && (bytes[end] & 0b1100_0000) === 0b1000_0000) end--;
  return decoder.decode(bytes.subarray(0, end));
}

/** A byte count as an operator-facing size.
 *
 * Takes the count, not the text: its caller has already measured the
 * payload to decide whether to cut it, and re-encoding a multi-megabyte
 * argument on every render to print its size is the kind of work this
 * panel exists to avoid.
 */
export function formatBytes(bytes) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

/** `text` cut to at most `limit` characters, on a word boundary.
 *
 * Mirrors flux/agents/console/titles.py::truncate_title, including the
 * ellipsis counting against the budget: the server sends titles cut by
 * that rule, and a client that cuts by a different one renders the same
 * title in two shapes as a poll replaces a locally derived title with the
 * server's.
 */
export function truncate(text, limit) {
  const value = String(text);
  if (value.length <= limit) return value;
  const keep = limit - 1;
  if (keep <= 0) return "…".slice(0, Math.max(0, limit));
  let cut = value.slice(0, keep);
  // Only back up to the previous word when the cut actually split one.
  if (!/\s/.test(cut.slice(-1)) && !/\s/.test(value[keep])) {
    const boundary = cut.lastIndexOf(" ");
    if (boundary > 0) cut = cut.slice(0, boundary);
  }
  return `${cut.replace(/\s+$/, "")}…`;
}
