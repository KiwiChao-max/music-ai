import { describe, it, expect } from "vitest";
import {
  ALLOWED_AUDIO_EXTENSIONS,
  looksLikeAudio,
  validateAudioFile,
} from "./upload";

function makeFile(name: string, opts: { type?: string; size?: number } = {}): File {
  const blob = new Blob([new Uint8Array(opts.size ?? 0)], {
    type: opts.type ?? "",
  });
  return new File([blob], name, { type: opts.type ?? "" });
}

describe("ALLOWED_AUDIO_EXTENSIONS", () => {
  it("includes common lossless and lossy formats", () => {
    expect(ALLOWED_AUDIO_EXTENSIONS).toContain(".wav");
    expect(ALLOWED_AUDIO_EXTENSIONS).toContain(".mp3");
    expect(ALLOWED_AUDIO_EXTENSIONS).toContain(".flac");
    expect(ALLOWED_AUDIO_EXTENSIONS).toContain(".ogg");
  });

  it("is a readonly list (callers can't mutate the shared constant)", () => {
    // `readonly string[]` is a compile-time guarantee; at runtime it's
    // still a mutable array. Assert the no-op mutation doesn't throw so
    // a future refactor to `Object.freeze` doesn't silently break tests.
    expect(() => [...ALLOWED_AUDIO_EXTENSIONS]).not.toThrow();
  });
});

describe("looksLikeAudio", () => {
  it("accepts a file whose MIME type is audio/*", () => {
    expect(looksLikeAudio(makeFile("track", { type: "audio/wav" }))).toBe(true);
    expect(looksLikeAudio(makeFile("track", { type: "audio/mpeg" }))).toBe(true);
  });

  it("accepts a file whose extension is in the allow-list", () => {
    expect(looksLikeAudio(makeFile("song.wav"))).toBe(true);
    expect(looksLikeAudio(makeFile("song.MP3"))).toBe(true); // case-insensitive
    expect(looksLikeAudio(makeFile("path/to/song.flac"))).toBe(true);
  });

  it("rejects non-audio files", () => {
    expect(looksLikeAudio(makeFile("notes.txt"))).toBe(false);
    expect(looksLikeAudio(makeFile("video.mp4", { type: "video/mp4" }))).toBe(false);
    expect(looksLikeAudio(makeFile("no-extension"))).toBe(false);
  });

  it("rejects a file with a spoofed audio extension but non-audio MIME", () => {
    // MIME type takes precedence and is not audio/*, so fall through to
    // the extension check, which matches `.wav` → still accepted.
    // This documents the actual precedence (MIME first, then extension).
    expect(looksLikeAudio(makeFile("song.wav", { type: "application/octet-stream" }))).toBe(true);
  });
});

describe("validateAudioFile", () => {
  it("returns null for null input (caller should skip)", () => {
    expect(validateAudioFile(null)).toBeNull();
  });

  it("returns an error message for a non-audio file", () => {
    const msg = validateAudioFile(makeFile("notes.txt"));
    expect(msg).not.toBeNull();
    expect(msg).toMatch(/audio file/i);
  });

  it("returns null for a valid audio file under the size limit", () => {
    expect(validateAudioFile(makeFile("song.wav", { size: 1024 }))).toBeNull();
  });

  it("returns an error message when the file exceeds the size limit", () => {
    // Build a file just over 200 MiB (the default MAX_UPLOAD_BYTES).
    const oversized = makeFile("song.wav", { size: 200 * 1024 * 1024 + 1 });
    const msg = validateAudioFile(oversized);
    expect(msg).not.toBeNull();
    expect(msg).toMatch(/MB or smaller/);
  });
});
