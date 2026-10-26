import { expect, test } from "@playwright/test";

test("a question draws a chart in the chat", async ({ page }) => {
  await page.goto("/");
  const input = page.getByPlaceholder(/Ask about TPC-H sales/);
  await input.fill("net revenue by region and year");
  await input.press("Enter");
  await expect(page.getByText("Source: tpch_sales")).toBeVisible();
  await expect(page.locator(".slayer-chart-error")).toHaveCount(0);
  await expect(page.getByText("Here it is.")).toBeVisible();
});
