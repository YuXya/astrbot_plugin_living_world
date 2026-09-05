/* Run with Node and Playwright; every backend call is replaced by an in-page fixture. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const pageRoot = path.resolve(__dirname, "../pages/living-world");
const browserPath = process.env.LIVING_WORLD_BROWSER || [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
].find((candidate) => fs.existsSync(candidate));

(async () => {
  const browser = await chromium.launch({ headless: true, ...(browserPath ? { executablePath: browserPath } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("http://living-world.test/**", (route) => {
      const file = new URL(route.request().url()).pathname.slice(1) || "index.html";
      if (!["index.html", "app.js", "style.css"].includes(file)) return route.abort();
      return route.fulfill({ status: 200, contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html", body: fs.readFileSync(path.join(pageRoot, file)) });
    });
    await page.addInitScript(() => {
      const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
      const scope = "qq:FriendMessage:42";
      window.calls = [];
      window.fixture = {
        version: "0.1.0-test", personas: [{ id: "student", name: "小夏" }], providers: [{ id: "chat-model", name: "默认聊天模型" }],
        settings: { persona_id: "student", retained_unknown: { keep: true }, modules: { life: true, state: true, memory: true, reply: true, journal: true }, models: { default: "chat-model" }, character: { profile: "喜欢天文学和散步的高中生。", world: "在一座靠海的小城上学。", timezone: "Asia/Shanghai", energy: 75, mood: "平静" }, sessions: [{ umo: scope, enabled: true, weight: 1, retained: "keep" }], news: { feeds: ["https://example.test/feed.xml"], limit: 5 }, social: { target_count: 1, cooldown_minutes: 60, daily_limit: 5, interjection_interval_minutes: 30, quiet_start: "23:00", quiet_end: "08:00" }, life: { tick_seconds: 60, detail_minutes: 10, max_activities: 12 } },
        sessions: [{ umo: scope, title: "与朋友的私聊", persona_id: "student" }], state: { energy: 75, mood: "有点期待", routine: "上午上课，傍晚散步。" },
        modules: [{ id: "life", enabled: true, status: "ready" }, { id: "memory", enabled: true, status: "ready" }, { id: "bilibili", enabled: false, status: "disabled", error: "依赖插件尚未启用" }],
        activities: [{ id: "a1", date: day, start: `${day}T09:00:00+08:00`, end: `${day}T10:00:00+08:00`, title: "数学课，窗外有一朵很像鲸鱼的云", kind: "fiction", status: "planned", scope: "global", description: "记得把昨天借来的笔还给同桌。" }],
        memories: [{ id: "m1", text: "约好了明天一起聊流星雨", kind: "event", scope, person_id: "42", important: true }, { id: "m2", text: "朋友最近喜欢看天文纪录片", kind: "knowledge", scope, person_id: "42" }, { id: "m3", text: "<img src=x onerror=window.xss=true>", kind: "event", scope: "global" }, { id: "m4", text: "喜欢天文学", kind: "knowledge", scope: "global", person_id: "42", profile: true }],
        observations: [{ id: "o1", source: "news", title: "今晚试着看看星空", text: "一篇关于秋季观星的阅读记录。", url: "https://example.test/story", scope: "global" }],
        entries: [{ id: "j1", day, text: "晚风很舒服，回家路上看到了一颗很亮的星。", kind: "journal", scope: "global" }],
        events: [{ id: "e1", text: "读完了一篇关于流星雨的文章。", kind: "news", status: "success", scope: "global" }],
        deliveries: [{ id: "d1", umo: scope, text: "刚看到今晚天气不错，你还想一起聊星星吗？", status: "sent" }],
        usage: [{ id: "u1", module: "life", provider_id: "chat-model", status: "ok", total_tokens: 760, duration_ms: 1500 }], diagnostics: [],
      };
      window.AstrBotPluginPage = {
        ready: async () => ({ isDark: false }),
        apiGet: async (endpoint) => { window.calls.push({ endpoint, method: "GET" }); return structuredClone(endpoint === "export" ? { version: 1, settings: window.fixture.settings } : window.fixture); },
        apiPost: async (endpoint, body) => {
          window.calls.push({ endpoint, method: "POST", body: structuredClone(body) });
          if (window.failNext) { window.failNext = false; throw new Error("测试来源暂时不可用"); }
          if (endpoint === "settings") window.fixture.settings = structuredClone(body);
          return { status: "success", action: body.action };
        },
      };
    });
    await page.goto("http://living-world.test/");
    await page.getByText("已连接 AstrBot", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.filter((call) => call.method === "POST").length), 0, "Opening the page must never mutate or send");
    if (process.env.LIVING_WORLD_UI_SCREENSHOT) await page.screenshot({ path: process.env.LIVING_WORLD_UI_SCREENSHOT, fullPage: true });

    await page.locator('a[data-view="settings"]').click();
    await page.locator('[name="character.profile"]').fill("新的角色资料");
    await page.locator('[name="modules.news"]').check();
    await page.getByRole("button", { name: "保存全部设置", exact: true }).first().click();
    await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor();
    let settings = await page.evaluate(() => window.fixture.settings);
    assert.equal(settings.character.profile, "新的角色资料");
    assert.equal(settings.modules.news, true);
    assert.equal(settings.retained_unknown.keep, true, "Unknown top-level settings must be retained");
    assert.equal(settings.life.max_activities, 12, "Unknown nested settings must be retained");
    assert.equal(settings.sessions[0].retained, "keep", "Unknown session settings must be retained");
    assert.deepEqual(settings.news.feeds, ["https://example.test/feed.xml"]);

    await page.locator('a[data-view="schedule"]').click();
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.locator('#editor [name="title"]').fill("数学课，今天复习函数");
    await page.locator("#editor-submit").click();
    await page.locator("#editor").waitFor({ state: "hidden" });
    const edit = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "update_activity").body);
    assert.equal(edit.patch.title, "数学课，今天复习函数");
    assert.ok(edit.patch.start && edit.patch.end);
    assert.ok(!Object.hasOwn(edit.patch, "scope") && !Object.hasOwn(edit.patch, "status"), "Activity edits must not forge execution or scope");

    await page.locator('a[data-view="memory"]').click();
    assert.equal(await page.evaluate(() => window.xss), undefined, "Untrusted memory text must stay inert");
    assert.equal(await page.locator("#content img").count(), 0);
    await page.getByRole("searchbox", { name: "搜索记忆" }).fill("流星雨");
    assert.equal(await page.locator("#content .record").count(), 1);
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.locator('#editor [name="content"]').fill("约好了今晚一起聊流星雨");
    await page.locator("#editor-submit").click();
    await page.locator("#editor").waitFor({ state: "hidden" });
    const memoryEdit = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "update_memory").body.patch);
    assert.equal(memoryEdit.text, "约好了今晚一起聊流星雨");
    assert.ok(!Object.hasOwn(memoryEdit, "scope") && !Object.hasOwn(memoryEdit, "person_id"), "Memory edits retain trusted scope and person identity");
    await page.getByRole("searchbox", { name: "搜索记忆" }).fill("");
    await page.getByRole("checkbox", { name: "选择记忆 约好了明天一起聊流星雨" }).check();
    await page.getByRole("checkbox", { name: "选择记忆 <img src=x onerror=window.xss=true>" }).check();
    await page.getByRole("button", { name: "合并所选", exact: true }).click();
    await page.getByText("只能合并同一场合的记忆，避免把私人内容带到其他场合。", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.some((call) => call.body?.action === "merge_memories")), false);
    await page.getByRole("checkbox", { name: "选择记忆 <img src=x onerror=window.xss=true>" }).uncheck();
    await page.getByRole("checkbox", { name: "选择记忆 朋友最近喜欢看天文纪录片" }).check();
    await page.getByRole("button", { name: "合并所选", exact: true }).click();
    await page.locator("#editor-submit").click();
    await page.locator("#editor").waitFor({ state: "hidden" });
    assert.deepEqual(await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "merge_memories").body.ids), ["m1", "m2"]);

    await page.locator('a[data-view="journal"]').click();
    await page.locator('[name="source"]').selectOption("bilibili_watch");
    await page.locator('[name="query"]').fill("BV1test");
    await page.evaluate(() => { window.failNext = true; });
    await page.getByRole("button", { name: "执行一次探索", exact: true }).click();
    await page.getByText("测试来源暂时不可用", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "执行一次探索", exact: true }).isEnabled(), true);

    await page.locator('a[data-view="social"]').click();
    await page.locator('[name="reason"]').fill("数学课无聊，想找群聊天");
    await page.getByRole("button", { name: "抽选对象并发送", exact: true }).click();
    assert.equal(await page.evaluate(() => window.calls.some((call) => call.body?.action === "social")), false, "Sending requires the explicit confirm button");
    await page.locator("#editor-submit").click();
    await page.locator("#editor").waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "social").body.reason), "数学课无聊，想找群聊天");

    await page.locator('a[data-view="data"]').click();
    await page.getByRole("button", { name: "恢复所选备份", exact: true }).click();
    await page.getByText("请先选择有效的 JSON 备份文件。", { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    for (const view of ["overview", "settings", "schedule", "memory", "journal", "social", "data"]) {
      await page.evaluate((target) => { location.hash = target; }, view);
      await page.locator(`a[data-view="${view}"][aria-current="page"]`).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, `No horizontal overflow on mobile: ${view}`);
    }
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; location.hash = "overview"; });
    await page.locator('a[data-view="overview"][aria-current="page"]').waitFor();
    assert.deepEqual(errors, [], "The page must render without JavaScript errors");
    process.stdout.write("UI smoke passed: navigation, settings preservation, editing, memory boundaries, XSS, error feedback, explicit sending, import guard, responsive layout.\n");
  } finally { await browser.close(); }
})().catch((error) => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
