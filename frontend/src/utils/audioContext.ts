/**
 * Shared Web Audio context.
 *
 * Browsers cap the number of live AudioContexts (roughly 6 in Chrome);
 * the sample library used to create one per expanded card, and the drum
 * player + MIDI preview each created their own. That exhausted the limit
 * and made the browser warn (and drop) contexts.
 *
 * This module owns a single lazy singleton. Callers that used to close
 * their own context on unmount must NOT close this one --- another
 * player may still be using it. They should only stop their own
 * scheduled nodes.
 *
 * The context is resumed on demand: browsers start it "suspended" until
 * a user gesture, and every call site here is reachable from a click
 * handler, so the resume succeeds.
 */
let _sharedContext: AudioContext | null = null;

export function getSharedAudioContext(): AudioContext {
  if (typeof window === "undefined") {
    throw new Error("AudioContext is only available in the browser");
  }
  if (!_sharedContext) {
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    _sharedContext = new Ctor();
  }
  if (_sharedContext.state === "suspended") {
    void _sharedContext.resume().catch(() => {
      // Still suspended (e.g. called outside a user gesture) --- the
      // caller's own resume-on-play will pick it up.
    });
  }
  return _sharedContext;
}
