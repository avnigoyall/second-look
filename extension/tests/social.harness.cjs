// Local browser verification of the real self-contained Social Radar page.
// Run: PW_PATH=/path/to/playwright node extension/tests/social.harness.cjs
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PW_PATH || "playwright");
const html = fs.readFileSync(path.resolve(__dirname, "../../dashboard/trends.html"));
const shots = path.resolve(__dirname, "e2e/shots");
fs.mkdirSync(shots, { recursive: true });
const start = Date.UTC(2025, 10, 17);
const weeks = Array.from({ length: 52 }, (_, i) => ({
  week: new Date(start + i * 7 * 86400000).toISOString().slice(0, 10),
  count: i === 24 ? 30 : 5, corpus_count: 100, normalized: i === 24 ? 0.3 : 0.05,
  complete: true, query_coverage: i === 4 ? "unvisited" : i === 5 ? "partial" : "complete",
  corpus_query_coverage: i === 5 ? "incomplete" : "complete",
  saturated: i === 6, counts_are_lower_bound: i === 6,
}));
const trends = ["cargo pants", "barrel jeans"].map(term => ({ term, weekly_counts: weeks, peak_week: weeks[24].week }));
const graph = {
  nodes: Array.from({ length: 1500 }, (_, i) => ({ id: `hash${i}`, community_id: String(i % 3), community_name: `Discussion ${i % 3}`, degree: 2 })),
  edges: Array.from({ length: 1499 }, (_, i) => ({ source: `hash${i}`, target: `hash${i + 1}`, type: i % 2 ? "similarity" : "interaction", kind: i % 2 ? "similarity" : "reply", weight: i % 2 ? 0.2 : 1 })),
  communities: Array.from({ length: 3 }, (_, i) => ({ id: String(i), name: `Discussion ${i}`, size: 500 })),
  stats: { authors: 1500, exported_nodes: 1500, largest_component_share_interaction: 0.1, largest_component_share_with_similarity: 1, isolated_authors_interaction: 100, isolated_authors_with_similarity: 0 },
};
let mode = "ok", checks = 0;
const server = http.createServer((request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/trends-ui") {
    response.writeHead(200, { "Content-Type": "text/html" }); return response.end(html);
  }
  response.setHeader("Content-Type", "application/json");
  if (mode === "failed") { response.statusCode = 503; return response.end("{}"); }
  const value = url.pathname === "/social-graph" ? (mode === "empty" ? { nodes: [], edges: [], communities: [] } : graph)
    : { trends: mode === "empty" ? [] : trends };
  response.end(JSON.stringify(value));
});
const check = (value, name) => { assert.ok(value, name); checks++; console.log("PASS", name); };
(async () => {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1280]) for (const scheme of ["light", "dark"]) {
      mode = "ok";
      const page = await browser.newPage({ viewport: { width, height: 1000 }, colorScheme: scheme });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.goto(`${origin}/trends-ui?term=barrel%20jeans`);
      await page.locator("#graph-figure").waitFor({ state: "visible" });
      await page.locator("#curve-figure").waitFor({ state: "visible" });
      check(await page.locator("#term").inputValue() === "barrel jeans", `URL term selected ${width}/${scheme}`);
      check(await page.locator("button, iframe").count() === 0, `no additional controls ${width}/${scheme}`);
      check(await page.locator("#curve .peak-dot").count() === 1, `peak marked ${width}/${scheme}`);
      check(await page.locator("#community-legend li").count() === 3, `named communities ${width}/${scheme}`);
      check(await page.locator("#curve .partial-point").count() === 2, `saturated counts remain lower bounds ${width}/${scheme}`);
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `no horizontal overflow ${width}/${scheme}`);
      await page.locator("#term").selectOption("cargo pants");
      check(await page.locator("#term").inputValue() === "cargo pants", `dropdown works ${width}/${scheme}`);
      check(errors.length === 0, `no JS errors ${width}/${scheme}`);
      await page.screenshot({ path: path.join(shots, `social-radar-${width}-${scheme}.png`), fullPage: true });
      if (width === 390 && scheme === "light") {
        const edges = graph.edges;
        graph.edges = edges.filter(edge => edge.type === "interaction");
        graph.stats.similarity_enabled = false;
        await page.reload();
        await page.locator("#graph-figure").waitFor({ state: "visible" });
        check((await page.locator("#graph-caption").innerText()).includes("observed interactions only"), "missing similarity is disclosed");
        graph.edges = edges;
        graph.stats.similarity_enabled = true;
      }
      await page.close();
    }
    for (const state of ["empty", "failed"]) {
      mode = state;
      const page = await browser.newPage();
      await page.goto(`${origin}/trends-ui`);
      await page.waitForFunction(() => document.getElementById("graph-panel").getAttribute("aria-busy") === "false"
        && document.getElementById("curve-panel").getAttribute("aria-busy") === "false");
      check(await page.locator("#graph-message").isVisible(), `${state} graph message`);
      check(await page.locator("#curve-message").isVisible(), `${state} curve message`);
      check(await page.locator("#graph-figure").isHidden(), `${state} graph hidden`);
      await page.screenshot({ path: path.join(shots, `social-radar-${state}.png`), fullPage: true });
      await page.close();
    }
    console.log(`${checks}/${checks} passed`);
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error.message); server.close(); process.exitCode = 1; });
