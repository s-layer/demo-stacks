import { expect, test } from "@playwright/test";

type Row = { order_year: number; net_revenue: number; customer_region: string };

test("a question draws a chart in the chat", async ({ page }) => {
  await page.goto("/");
  // The chart fetches its rows from the app once the run has charted them; keep them.
  const rowsResponse = page.waitForResponse(
    (response) => /\/flint\/charts\/[^/]+\/rows$/.test(new URL(response.url()).pathname),
  );
  const input = page.getByPlaceholder(/Ask about TPC-H sales/);
  await input.fill("net revenue by region and year");
  await input.press("Enter");

  const response = await rowsResponse;
  expect(response.status()).toBe(200);
  const rows = (await response.json()) as Row[];
  // SF0.01 TPC-H: five regions, orders in 1992-1998.
  const regions = [...new Set(rows.map((row) => row.customer_region))].sort();
  expect(regions).toEqual(["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]);
  expect(rows.every((row) => row.net_revenue > 0)).toBe(true);

  await expect(page.getByText("Source: tpch_sales")).toBeVisible();
  await expect(page.locator(".slayer-chart-error")).toHaveCount(0);
  const figure = page.getByRole("figure", { name: "Net revenue by region and year" });
  const canvas = figure.locator("canvas.marks");
  await expect(canvas).toBeVisible();
  // The chart draws on a canvas, so read its pixels: a grouped bar chart has one solid colour
  // per region, and a line just above the x axis crosses every bar once.
  await expect.poll(() => canvas.evaluate(countBars)).toEqual({
    colours: regions.length,
    bars: rows.length,
  });
  await expect(page.getByText("Here it is.")).toBeVisible();
});

/**
 * The bars on a chart canvas: its solid, saturated colours (one per series, legend swatches
 * included), and the most bars any one pixel row crosses.
 */
function countBars(canvas: HTMLCanvasElement): { colours: number; bars: number } {
  const context = canvas.getContext("2d");
  if (!context) return { colours: 0, bars: 0 };
  const { width, height, data } = context.getImageData(0, 0, canvas.width, canvas.height);
  const at = (x: number, y: number) => {
    const i = (y * width + x) * 4;
    return data[i + 3] === 255 ? `${data[i]},${data[i + 1]},${data[i + 2]}` : "";
  };
  const saturated = (key: string) => {
    const [r, g, b] = key.split(",").map(Number) as [number, number, number];
    return Math.max(r, g, b) - Math.min(r, g, b) > 60;
  };
  const pixels = new Map<string, number>();
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const key = at(x, y);
      if (key && saturated(key)) pixels.set(key, (pixels.get(key) ?? 0) + 1);
    }
  }
  // Antialiased edges blend colours into a long tail of near-misses; a series colour fills
  // thousands of pixels, within a few times of the other series.
  const most = Math.max(0, ...pixels.values());
  const colours = new Set([...pixels].filter(([, n]) => n > most / 4).map(([key]) => key));
  let bars = 0;
  for (let y = 0; y < height; y++) {
    // A run ends where its colour changes, so two bars that touch still count as two.
    let runs = 0;
    let prev = "";
    for (let x = 0; x < width; x++) {
      const key = at(x, y);
      const colour = colours.has(key) ? key : "";
      if (colour && colour !== prev) runs++;
      prev = colour;
    }
    bars = Math.max(bars, runs);
  }
  return { colours: colours.size, bars };
}
