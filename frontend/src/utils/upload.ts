/**
 * Frontend upload constraints. Mirror the backend's
 * `app.api.audio._ALLOWED_AUDIO_EXTENSIONS` and `MAX_UPLOAD_BYTES` --- keep
 * the two in sync when you change either side.
 *
 * `VITE_MAX_UPLOAD_BYTES` lets ops override the limit at deploy time without
 * rebuilding the bundle (handy when the backend `MAX_UPLOAD_BYTES` is
 * raised in production). Falls back to 200 MiB.
 */
export const MAX_UPLOAD_BYTES = (() => {
  const raw = import.meta.env.VITE_MAX_UPLOAD_BYTES;
  const parsed = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 200 * 1024 * 1024;
})();

export const ALLOWED_AUDIO_EXTENSIONS: readonly string[] = [
  ".aac",
  ".aiff",
  ".aif",
  ".flac",
  ".m4a",
  ".mp3",
  ".ogg",
  ".opus",
  ".wav",
  ".wave",
  ".webm",
  ".wma",
];

export function looksLikeAudio(file: File): boolean {
  if (file.type && file.type.toLowerCase().startsWith("audio/")) {
    return true;
  }
  const lowerName = file.name.toLowerCase();
  return ALLOWED_AUDIO_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
}

export function validateAudioFile(file: File | null): string | null {
  if (!file) return null;
  if (!looksLikeAudio(file)) {
    return "Please choose an audio file.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    const maxMB = Math.floor(MAX_UPLOAD_BYTES / (1024 * 1024));
    return `Audio files must be ${maxMB} MB or smaller.`;
  }
  return null;
}
