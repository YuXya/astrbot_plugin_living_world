const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
(async () => {
  const source = fs.readFileSync(path.resolve(__dirname, "../pages/living-world/navigation.js"), "utf8");
  const { PAGES, resolveRoute, routeHash, reorderedLayout } = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
  assert.equal(Object.keys(PAGES).length, 8);
  for (const [page, config] of Object.entries(PAGES)) {
    assert.equal(resolveRoute("#" + page).tab, Object.keys(config.tabs)[0]);
    for (const tab of Object.keys(config.tabs)) assert.deepEqual(resolveRoute(routeHash(page, tab, { field: "中文 & /?" })), { page, tab, params: { field: "中文 & /?" } });
  }
  assert.equal(resolveRoute("#context", { context: "templates" }).tab, "templates");
  assert.deepEqual(resolveRoute("#debug?turn=legacy"), { page: "context", tab: "calls", params: { turn: "legacy" } });
  for (const hash of ["#constructor", "#toString", "#__proto__", "#missing"]) assert.equal(resolveRoute(hash).page, "overview");
  const layout = { system: ["anchor.system", "profile"], user: ["anchor.user", "time", "news", "memories"] };
  for (const args of [["time", "user", "time"], ["time", "user", "news"], ["memories", "user"], ["news", "assistant"], ["missing", "user"], ["anchor.user", "system"], ["news", "system", "invalid"]]) assert.equal(reorderedLayout(layout, ...args), layout, "No-op returns the original object, without marking a draft");
  const moved = reorderedLayout(layout, "news", "system", "anchor.system");
  assert.deepEqual(moved.system, ["news", "anchor.system", "profile"]);
  assert.deepEqual(moved.user, ["anchor.user", "time", "memories"]);
  assert.deepEqual(layout.user, ["anchor.user", "time", "news", "memories"]);
  assert.deepEqual(reorderedLayout(layout, "time", "user").user, ["anchor.user", "news", "memories", "time"]);
  assert.deepEqual(reorderedLayout(layout, "news", "user", "anchor.user").user, ["news", "anchor.user", "time", "memories"]);
  process.stdout.write("Navigation and immutable ordering checks passed.\n");
})().catch(error => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
