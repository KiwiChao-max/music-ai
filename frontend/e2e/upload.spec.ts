import { test, expect } from "@playwright/test";
import { goToPath, expectLayout, generateSilentWav } from "./helpers";

test.describe("Upload page", () => {
  test("renders the upload form", async ({ page }) => {
    await goToPath(page, "/upload");

    await expectLayout(page);
    await expect(
      page.getByRole("heading", { level: 1 }),
    ).toBeVisible();
    await expect(page.getByLabel(/drop/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /submit|upload/i }),
    ).toBeVisible();
  });

  test("submit button is disabled when no file is selected", async ({ page }) => {
    await goToPath(page, "/upload");

    const submitBtn = page.getByRole("button", { name: /submit|upload/i });
    await expect(submitBtn).toBeDisabled();
  });

  test("can select a file via the file input", async ({ page }) => {
    await goToPath(page, "/upload");

    const wav = generateSilentWav();
    await page.locator('input[type="file"]').setInputFiles({
      name: "test.wav",
      mimeType: "audio/wav",
      buffer: wav,
    });

    // The file name should appear in the UI
    await expect(page.getByText("test.wav")).toBeVisible();
    // The submit button should be enabled
    const submitBtn = page.getByRole("button", { name: /submit|upload/i });
    await expect(submitBtn).toBeEnabled();
  });
});

test.describe("Upload flow", () => {
  test("uploading a file creates a task and redirects to detail page", async ({
    page,
  }) => {
    await goToPath(page, "/upload");

    // Select the file
    const wav = generateSilentWav();
    await page.locator('input[type="file"]').setInputFiles({
      name: "e2e-test.wav",
      mimeType: "audio/wav",
      buffer: wav,
    });

    // Submit the upload
    const submitBtn = page.getByRole("button", { name: /submit|upload/i });
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // Should redirect to /audio/{id} on success
    await page.waitForURL(/\/audio\/\d+/);
    await expect(page.locator("main")).toBeVisible();

    // The task should appear on the detail page
    await expect(page.getByText("e2e-test.wav")).toBeVisible();
  });

  test("uploaded task appears in the task list", async ({ page }) => {
    // First upload a file
    await goToPath(page, "/upload");
    const wav = generateSilentWav();
    await page.locator('input[type="file"]').setInputFiles({
      name: "list-test.wav",
      mimeType: "audio/wav",
      buffer: wav,
    });
    await page.getByRole("button", { name: /submit|upload/i }).click();
    await page.waitForURL(/\/audio\/\d+/);

    // Navigate to the task list
    await goToPath(page, "/audio");
    await expect(page.getByText("list-test.wav")).toBeVisible();
  });
});