import { test, expect } from "@playwright/test";
import { goToPath, expectLayout } from "./helpers";

test.describe("Home page", () => {
  test("renders the layout and navigation", async ({ page }) => {
    await goToPath(page, "/");

    await expectLayout(page);

    // Home page specific content
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByText(/upload/i).first()).toBeVisible();
    await expect(page.getByText(/tasks/i).first()).toBeVisible();
  });

  test("has navigation links to upload and tasks pages", async ({ page }) => {
    await goToPath(page, "/");

    // Click the upload card link
    const uploadLink = page.getByRole("link", { name: /upload/i });
    await expect(uploadLink).toBeVisible();
    await uploadLink.click();
    await expect(page).toHaveURL(/\/upload/);

    // Go back and click the tasks link
    await goToPath(page, "/");
    const tasksLink = page.getByRole("link", { name: /tasks/i });
    await expect(tasksLink).toBeVisible();
    await tasksLink.click();
    await expect(page).toHaveURL(/\/audio/);
  });
});