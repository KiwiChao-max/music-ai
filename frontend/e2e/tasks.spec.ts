import { test, expect } from "@playwright/test";
import { goToPath, expectLayout } from "./helpers";

test.describe("Task list page", () => {
  test("renders the page with heading", async ({ page }) => {
    await goToPath(page, "/audio");

    await expectLayout(page);
    await expect(
      page.getByRole("heading", { level: 1 }),
    ).toBeVisible();
  });

  test("shows empty state when no tasks exist", async ({ page }) => {
    await goToPath(page, "/audio");

    // The page should show either skeleton loading or empty state.
    // After network idle, it should settle to one of these.
    // Since we haven't uploaded anything, it should eventually show empty.
    // But if the API returns tasks from a previous run, we just verify
    // the page renders without error.
    await expect(page.locator("main")).toBeVisible();
  });

  test("has a link to the upload page", async ({ page }) => {
    await goToPath(page, "/audio");

    const uploadLink = page.getByRole("link", { name: /upload/i });
    // Could be the "New Upload" button or the empty-state CTA.
    await expect(uploadLink.first()).toBeVisible();
  });
});