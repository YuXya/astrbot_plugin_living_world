/* Run with Node and Playwright. Backend/model/network/QQ calls are fixtures. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const pageRoot = path.resolve(__dirname, "../pages/living-world");
const browserPath = process.env.LIVING_WORLD_BROWSER || ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Google/Chrome/Application/chrome.exe"].find(fs.existsSync);
(async () => {
  const browser = await chromium.launch({ headless: true, ...(browserPath ? { executablePath: browserPath } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1050 } });
    const page = await context.newPage(); const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("http://living-world.test/**", (route) => {
      const file = new URL(route.request().url()).pathname.slice(1) || "index.html";
      if (!["index.html", "app.js", "style.css"].includes(file)) return route.abort();
      return route.fulfill({ status: 200, contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html", body: fs.readFileSync(path.join(pageRoot, file)) });
    });
    await page.addInitScript(() => {
      const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
      const tomorrow = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date(Date.now() + 86400000));
      const scope = "qq:FriendMessage:42";
      window.calls = []; window.testTomorrow = tomorrow;
      const activities = Array.from({ length: 10 }, (_, i) => {
        const start = new Date(new Date(`${day}T00:00:00+08:00`).getTime() + i * 8640000);
        const end = new Date(start.getTime() + 8640000);
        const actions = {};
        for (const [key, marked] of [["news", [1, 5]], ["search", [3, 6]], ["social", [0, 4, 8]]]) actions[key] = { enabled: marked.includes(i), intent: `${key} 活动意图`, at: start.toISOString(), execution: i === 0 && key === "social" ? { status: "skipped", reason: "处于免打扰时段" } : {} };
        return { id: `a${i}`, date: day, start: start.toISOString(), end: end.toISOString(), title: `活动 ${i + 1}：上课、散步与阅读`, description: "看看窗外的云，记得把借来的笔还给同桌。", kind: "fiction", status: end.getTime() < Date.now() ? "completed" : "planned", location: "教室", sleep_state: "awake", scope: "global", actions };
      });
      activities.push({ id: "future", date: tomorrow, start: `${tomorrow}T09:00:00+08:00`, end: `${tomorrow}T10:00:00+08:00`, title: "明天的数学课", description: "复习函数", location: "教室", sleep_state: "awake", kind: "fiction", status: "planned", scope: "global", actions: { news: { enabled: false, intent: "", at: `${tomorrow}T09:00:00+08:00` }, search: { enabled: true, intent: "搜索函数学习方法", at: `${tomorrow}T09:10:00+08:00` }, social: { enabled: true, intent: "课间聊聊天", at: `${tomorrow}T09:20:00+08:00` } } });
      const template = "Return a validated daily activity JSON plan.";
      window.fixture = {
        version: "0.2.0-test", personas: [{ id: "student", name: "小夏" }], providers: [{ id: "chat-model", name: "默认聊天模型" }], platforms: [{ id: "qq", name: "测试 QQ 连接" }],
        settings: { persona_id: "student", retained_unknown: { keep: true }, modules: { life: true, state: true, memory: true, reply: true, journal: true, debug: true }, models: { default: "chat-model" }, character: { profile: "喜欢天文学和散步的高中生。", world: "在靠海的小城上学。", timezone: "Asia/Shanghai", energy: 75, mood: "平静" }, sessions: [{ umo: scope, enabled: true, weight: 1, retained: "keep" }, { umo: "qq:GroupMessage:42_100", enabled: true, weight: 2 }], news: { sources: ["BBC 中文", "Google 新闻中文", "Solidot", "Hacker News", "MIT Technology Review", "Ars Technica"].map((name, i) => ({ id: `rss-${i}`, name, url: `https://example.test/feed-${i}.xml`, enabled: true })), limit: 5 }, weather: { location: "北京", api_host: "test.re.qweatherapi.com", auth_mode: "api_key", credential: "fixture-credential" }, bilibili: { recent_limit: 5 }, daily_digest: { sources: [{ id: "heya", name: "黑鸦 Heya", uid: "3706929260006322", keywords: "早报 日报", time: "12:00", enabled: true }, { id: "juya", name: "橘鸦 Juya", uid: "285286947", keywords: "日报", time: "23:00", enabled: true }] }, social: { target_count: 1, cooldown_minutes: 60, daily_limit: 5, interjection_interval_minutes: 30, quiet_start: "23:00", quiet_end: "08:00" }, life: { tick_seconds: 60, detail_minutes: 10, daily_plan_time: "06:00", activity_count: 10, news_count: 2, search_count: 2, social_count: 3, retained_nested: true }, debug: { retain_per_category: 10 } },
        sessions: [{ umo: scope, title: "与朋友的私聊", persona_id: "student" }], state: { energy: 75, mood: "有点期待", routine: "上午上课，傍晚散步。" },
        modules: [{ id: "life", enabled: true, status: "ready" }, { id: "memory", enabled: true, status: "ready" }, { id: "bilibili", enabled: false, status: "disabled", error: "依赖插件尚未启用" }], bilibili_dependency: { available: false, text: "依赖插件尚未启用" },
        activities,
        life_days: [{ date: day, scope: "global", parameters: { activity_count: 10, news_count: 2, search_count: 2, social_count: 3 }, full_request: { prompt: "daily plan complete request" }, raw_json: JSON.stringify({ activities: activities.slice(0, 10) }), adopted_activities: activities.slice(0, 10) }],
        memories: [{ id: "m1", text: "约好了明天一起聊流星雨", kind: "event", scope, person_id: "42", important: true }, { id: "m2", text: "朋友最近喜欢看天文纪录片", kind: "knowledge", scope, person_id: "42" }, { id: "m3", text: "<img src=x onerror=window.xss=true>", kind: "event", scope: "global" }],
        observations: [{ id: "o1", module: "news", title: "今晚试着看看星空", selection_reason: "喜欢天文", factual_summary: "本周有流星雨观测窗口。", impression: "想在放学后看看天空。", reading_basis: "RSS 摘要", sources: [{ url: "https://example.test/story" }], scope: "global" }, { id: "o2", module: "search", title: "流星雨观测地点", query: "城市周边 观星", factual_summary: "搜索提供了两处公园的信息。", impression: "下次想和朋友讨论路线。", reading_basis: "搜索结果摘要", sources: ["https://example.test/search"], scope: "global" }, { id: "o3", module: "weather", text: "晴，26°C，适合散步。", scope: "global" }],
        entries: [{ id: "j1", day, text: "晚风很舒服，回家看到一颗很亮的星。", kind: "journal", scope: "global" }], events: [], deliveries: [{ id: "d1", umo: scope, text: "今晚还想一起聊星星吗？", status: "sent" }], usage: [], diagnostics: [],
        debug: { templates: [{ task: "life.plan_day", default_template: template, template }] },
        debug_records: [{ id: "debug-1", kind: "model", category: "life.plan_day", task: "life.plan_day", module: "life", scope: "global", status: "success", boundary: "本插件提交给 AstrBot 的请求", request: { module: "life", task: "life.plan_day", scope: "global", provider_id: "chat-model", system_prompt: "Full persona system", prompt_mode: "structured", prompt: "Private test context kept outside templates", contexts: [{ role: "user", content: "完整历史" }], parameters: { temperature: 0.6 }, template, dynamic_context: { memories: ["测试记忆"] } }, response: { completion_text: "{\"activities\":[]}" } }],
      };
      window.AstrBotPluginPage = {
        ready: async () => ({ isDark: false }),
        apiGet: async (endpoint) => { window.calls.push({ endpoint, method: "GET" }); return structuredClone(endpoint === "export" ? { version: 1, settings: window.fixture.settings } : window.fixture); },
        apiPost: async (endpoint, body) => {
          window.calls.push({ endpoint, method: "POST", body: structuredClone(body) });
          if (window.failNext) { window.failNext = false; throw new Error("测试来源暂时不可用"); }
          if (endpoint === "settings") window.fixture.settings = structuredClone(body);
          if (body.action === "debug_build") return { request: structuredClone(window.fixture.debug_records[0].request) };
          if (body.action === "debug_test") return { status: "success", text: "测试回复", test_only: true, notice: "No business side effects" };
          return { status: "success", action: body.action };
        },
      };
    });
    await page.goto("http://living-world.test/");
    await page.getByText("已连接 AstrBot", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.filter((call) => call.method === "POST").length), 0, "Opening must be read-only");
    for (const label of ["心情", "精力", "地点", "睡眠", "天气"]) await page.getByText(label, { exact: true }).first().waitFor();
    await page.getByRole("heading", { name: "今日时间线", exact: true }).waitFor();
    await page.getByRole("heading", { name: "下一次主动联系", exact: true }).waitFor();
    assert.ok(await page.locator('a[href="#whitelist"]').count() >= 2);
    if (process.env.LIVING_WORLD_UI_SCREENSHOT) await page.screenshot({ path: process.env.LIVING_WORLD_UI_SCREENSHOT, fullPage: true });
    await page.locator('a[data-view="whitelist"]').click();
    assert.equal(await page.locator('.whitelist-entry').count(), 2);
    await page.getByRole("button", { name: "添加聊天对象", exact: true }).click();
    const added = page.locator('.whitelist-entry').last();
    await added.locator('[name="connection"]').selectOption("qq");
    await added.locator('[name="type"]').selectOption("GroupMessage");
    await added.locator('[name="number"]').fill("998877");
    await added.locator('[name="weight"]').fill("0.5");
    await page.getByRole("button", { name: "保存聊天对象与限制", exact: true }).click();
    await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor();
    let settings = await page.evaluate(() => window.fixture.settings);
    assert.equal(settings.sessions[0].retained, "keep");
    assert.equal(settings.sessions[1].umo, "qq:GroupMessage:42_100", "Unchanged legacy group scope must survive");
    assert.equal(settings.sessions[2].umo, "qq:GroupMessage:998877");
    assert.equal(settings.sessions[2].weight, 0.5);
    assert.equal(await page.getByRole("button", { name: "抽选对象并发送", exact: true }).count(), 0);
    await page.locator('a[data-view="settings"]').click();
    await page.locator('[name="character.profile"]').fill("新的角色资料");
    await page.locator('[name="character.location"]').fill("海边小城");
    await page.locator('[name="character.sleep_state"]').selectOption("清醒");
    await page.locator('[name="modules.news"]').check();
    await page.getByRole("button", { name: "保存全部设置", exact: true }).click();
    await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor();
    settings = await page.evaluate(() => window.fixture.settings);
    assert.equal(settings.character.profile, "新的角色资料"); assert.equal(settings.modules.news, true);
    assert.equal(settings.character.location, "海边小城"); assert.equal(settings.character.sleep_state, "清醒");
    assert.equal(settings.retained_unknown.keep, true); assert.equal(settings.life.retained_nested, true);
    await page.locator('a[data-view="schedule"]').click();
    for (const [name, value] of [["daily_plan_time", "06:00"], ["activity_count", "10"], ["news_count", "2"], ["search_count", "2"], ["social_count", "3"]]) assert.equal(await page.locator(`[name="life.${name}"]`).inputValue(), value);
    assert.equal(await page.locator('[name="life.activity_count"]').getAttribute("max"), "48");
    await page.getByRole("button", { name: "保存日程生成参数", exact: true }).click();
    await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.some((call) => call.body?.action === "plan_day")), false, "Saving parameters must not regenerate");
    await page.getByLabel("日程日期").fill(await page.evaluate(() => window.testTomorrow));
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.locator('#editor [name="title"]').fill("数学课，今天复习函数");
    await page.locator("#editor-submit").click(); await page.locator("#editor").waitFor({ state: "hidden" });
    const edit = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "update_activity").body);
    assert.equal(edit.patch.title, "数学课，今天复习函数"); assert.equal(edit.patch.actions.social.enabled, true);
    assert.ok(!Object.hasOwn(edit.patch, "scope") && !Object.hasOwn(edit.patch, "status"));
    await page.getByRole("button", { name: "批量调整未来活动", exact: true }).click();
    const batch = JSON.parse(await page.locator('#editor [name="json"]').inputValue());
    assert.equal(batch.updates.length, 1); assert.ok(!Object.hasOwn(batch.updates[0].changes.actions.social, "execution"));
    await page.locator("#editor-submit").click(); await page.locator("#editor").waitFor({ state: "hidden" });
    await page.locator('a[data-view="memory"]').click();
    assert.equal(await page.evaluate(() => window.xss), undefined); assert.equal(await page.locator("#content img").count(), 0);
    await page.getByRole("searchbox", { name: "搜索记忆" }).fill("流星雨");
    assert.equal(await page.locator("#content .record").count(), 1);
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.locator('#editor [name="content"]').fill("约好了今晚一起聊流星雨");
    await page.locator("#editor-submit").click(); await page.locator("#editor").waitFor({ state: "hidden" });
    const memoryEdit = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "update_memory").body.patch);
    assert.equal(memoryEdit.text, "约好了今晚一起聊流星雨"); assert.ok(!Object.hasOwn(memoryEdit, "scope"));
    await page.getByRole("searchbox", { name: "搜索记忆" }).fill("");
    await page.getByRole("checkbox", { name: "选择记忆 约好了明天一起聊流星雨" }).check();
    await page.getByRole("checkbox", { name: "选择记忆 <img src=x onerror=window.xss=true>" }).check();
    await page.getByRole("button", { name: "合并所选", exact: true }).click();
    await page.getByText("只能合并同一场合的记忆，避免把私人内容带到其他场合。", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.some((call) => call.body?.action === "merge_memories")), false);
    await page.locator('a[data-view="sources"]').click();
    assert.equal(await page.locator('[name="weather.url"],[name="search.tool_name"],[name="search.query_argument"],[name="bilibili.plugin_name"]').count(), 0);
    assert.equal(await page.locator('.source-entry').count(), 8);
    await page.getByRole("button", { name: "测试天气连接", exact: true }).click();
    await page.getByText("操作已完成，请查看执行结果", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "test_weather").body.action), "test_weather");
    await page.getByRole("button", { name: "保存来源设置", exact: true }).click();
    await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor();
    settings = await page.evaluate(() => window.fixture.settings);
    assert.equal(settings.news.sources.length, 6); assert.equal(settings.daily_digest.sources[0].time, "12:00");
    assert.equal(settings.daily_digest.sources[0].keywords, "早报 日报");
    assert.ok(!Object.hasOwn(settings, "name"), "Source row fields must not leak into top-level settings");
    await page.locator('a[data-view="journal"]').click();
    await page.getByText("手动读取来源（真实调用并保存见闻）", { exact: true }).click();
    await page.getByLabel("视频 BV 号", { exact: true }).fill("BV1test");
    await page.evaluate(() => { window.failNext = true; });
    await page.getByRole("button", { name: "观看指定视频并保存", exact: true }).click();
    await page.getByText("测试来源暂时不可用", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "观看指定视频并保存", exact: true }).isEnabled(), true);
    await page.locator('a[data-view="debug"]').click();
    assert.equal(await page.locator('[name="debug.retain_per_category"]').inputValue(), "10");
    await page.getByRole("button", { name: "从当前配置建立测试请求", exact: true }).click();
    await page.getByText("已建立测试请求；尚未调用模型", { exact: true }).waitFor();
    assert.equal(await page.locator('[name="request_mode"]').inputValue(), "structured");
    await page.getByText("组合模式：", { exact: false }).waitFor();
    await page.locator('[name="request_mode"]').selectOption("raw");
    const request = JSON.parse(await page.locator('[name="request_json"]').inputValue());
    assert.equal(request.prompt_mode, "raw");
    assert.equal(request.contexts[0].content, "完整历史");
    assert.ok(request.prompt.includes("测试记忆"), "Switching to raw preserves structured context in compiled prompt");
    request.prompt = "Edited private test context";
    await page.locator('[name="request_json"]').fill(JSON.stringify(request));
    const businessBefore = await page.evaluate(() => JSON.stringify({ activities: window.fixture.activities, memories: window.fixture.memories, deliveries: window.fixture.deliveries }));
    await page.getByRole("button", { name: "仅调用 AI 试跑", exact: true }).click();
    await page.getByText("操作已完成，请查看执行结果", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "debug_test").body.request.prompt), "Edited private test context");
    assert.equal(await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "debug_test").body.request.prompt_mode), "raw");
    assert.equal(await page.evaluate(() => JSON.stringify({ activities: window.fixture.activities, memories: window.fixture.memories, deliveries: window.fixture.deliveries })), businessBefore);
    await page.locator('summary').filter({ hasText: /^life\.plan_day$/ }).click();
    await page.locator('[name="template"]').fill("Changed public instruction only");
    await page.getByRole("button", { name: "保存此任务模板", exact: true }).click();
    await page.getByText("操作已完成，请查看执行结果", { exact: true }).waitFor();
    const saved = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "save_template").body);
    assert.equal(saved.template, "Changed public instruction only"); assert.ok(!JSON.stringify(saved).includes("Edited private test context"));
    assert.ok(await page.getByRole("button", { name: "下载 JSON", exact: true }).count() >= 1);
    await page.locator('a[data-view="data"]').click();
    await page.getByRole("button", { name: "恢复所选备份", exact: true }).click();
    await page.getByText("请先选择有效的 JSON 备份文件。", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "整理旧记忆", exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: "维护记忆", exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const view of ["overview", "settings", "schedule", "whitelist", "sources", "debug", "memory", "journal", "data"]) {
      await page.evaluate((target) => { location.hash = target; }, view);
      await page.locator(`a[data-view="${view}"][aria-current="page"]`).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, `No mobile horizontal overflow: ${view}`);
    }
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; location.hash = "overview"; });
    await page.locator('a[data-view="overview"][aria-current="page"]').waitFor();
    assert.deepEqual(errors, [], "No browser runtime errors");
    process.stdout.write("UI smoke passed: state home, schedule quotas/archive/editor, whitelist UMO, fixed sources, independent digests, full debug JSON, test/template separation, XSS, memory scopes, error feedback, import guard, responsive layout.\n");
  } finally { await browser.close(); }
})().catch((error) => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
