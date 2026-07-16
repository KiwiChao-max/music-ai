import { type Page, expect } from "@playwright/test";

/**
 * Generate a minimal valid WAV file (PCM, 16-bit, mono, 44100 Hz, 1 second
 * of silence).  Returns a Buffer suitable for use with ``page.setInputFiles``
 * or ``fetch``.
 *
 * WAV header layout (44 bytes):
 *   RIFF chunk (12) + fmt  sub-chunk (24) + data sub-chunk header (8)
 */
export function generateSilentWav(
  durationSec = 1,
  sampleRate = 44100,
  channels = 1,
  bitsPerSample = 16,
): Buffer {
  const bytesPerSample = bitsPerSample / 8;
  const frameSize = channels * bytesPerSample;
  const numSamples = sampleRate * durationSec;
  const dataSize = numSamples * frameSize;
  const fileSize = 36 + dataSize; // total file size - 8

  const header = Buffer.alloc(44);
  let offset = 0;

  // RIFF header
  header.write("RIFF", offset); offset += 4;
  header.writeUInt32LE(fileSize, offset); offset += 4;
  header.write("WAVE", offset); offset += 4;

  // fmt  sub-chunk
  header.write("fmt ", offset); offset += 4;
  header.writeUInt32LE(16, offset); offset += 4; // chunk size
  header.writeUInt16LE(1, offset); offset += 2; // PCM = 1
  header.writeUInt16LE(channels, offset); offset += 2;
  header.writeUInt32LE(sampleRate, offset); offset += 4;
  header.writeUInt32LE(sampleRate * frameSize, offset); offset += 4; // byte rate
  header.writeUInt16LE(frameSize, offset); offset += 2; // block align
  header.writeUInt16LE(bitsPerSample, offset); offset += 2;

  // data sub-chunk
  header.write("data", offset); offset += 4;
  header.writeUInt32LE(dataSize, offset); offset += 4;

  // Silence: all zero samples
  const data = Buffer.alloc(dataSize);

  return Buffer.concat([header, data]);
}

/**
 * Navigate to a path and wait for the network to be idle so the SPA
 * has finished its initial data fetches.
 */
export async function goToPath(page: Page, path: string) {
  await page.goto(path);
  // Wait for the SPA to finish rendering (React Suspense + data fetching).
  await page.waitForLoadState("networkidle");
  // Give React a tick to finish any synchronous state updates.
  await page.waitForTimeout(300);
}

/**
 * Assert that the page shows the main layout shell (nav bar + content area).
 */
export async function expectLayout(page: Page) {
  await expect(page.locator("header nav")).toBeVisible();
  // MainLayout renders a <main> element.
  await expect(page.locator("main")).toBeVisible();
}