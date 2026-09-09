/* Run with Node and Playwright. Backend/model/network/QQ calls are fixtures. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { chromium } = require("playwright");
const pageRoot = path.resolve(__dirname, "../pages/living-world");
const browserPath = process.env.LIVING_WORLD_BROWSER || ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Google/Chrome/Application/chrome.exe"].find(fs.existsSync);
const python = process.env.LIVING_WORLD_PYTHON || path.resolve(__dirname, "../.venv/Scripts/python.exe");
const backendContract = JSON.parse(execFileSync(python, ["-X", "utf8", path.join(__dirname, "ui_debug_fixture.py")], { encoding: "utf8" }));
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
      const originalInterval = window.setInterval;
      window.setInterval = (handler, duration, ...args) => { if (duration === 60000) window.driveRefreshTick = handler; return originalInterval(handler, duration, ...args); };
      const activities = Array.from({ length: 10 }, (_, i) => {
        const start = new Date(new Date(`${day}T00:00:00+08:00`).getTime() + i * 8640000);
        const end = new Date(start.getTime() + 8640000);
        const actions = {};
        for (const [key, marked] of [["news", [1, 5]], ["search", [3, 6]], ["social", [0, 4, 8]]]) actions[key] = { enabled: marked.includes(i), reason: marked.includes(i) ? "活动适合这项行动" : "当前没有具体需要，不凑数", intent: `${key} 活动意图`, at: marked.includes(i) ? start.toISOString() : null, execution: i === 0 && key === "social" ? { status: "skipped", reason: "处于免打扰时段" } : { status: marked.includes(i) ? "pending" : "disabled" } };
        return { id: `a${i}`, schema_version: 3, detailed: true, detail_version: `a${i}-detail`, date: day, start: start.toISOString(), end: end.toISOString(), title: `活动 ${i + 1}：上课、散步与阅读`, description: "看看窗外的云，记得把借来的笔还给同桌。", kind: "fiction", status: end.getTime() < Date.now() ? "completed" : "planned", location: "教室", sleep_state: "awake", scope: "global", actions };
      });
      activities.push({ id: "future", schema_version: 3, detailed: false, detail_error: "模型返回不完整", detail_attempts: 2, date: tomorrow, start: `${tomorrow}T09:00:00+08:00`, end: `${tomorrow}T10:00:00+08:00`, title: "明天的数学课", content: "复习函数", location: "教室", sleep_state: "awake", kind: "fiction", status: "planned", scope: "global" });
      const template = "Return a validated daily activity JSON plan.";
      window.fixture = {
        version: "0.2.0-test", personas: [{ id: "student", name: "小夏" }], providers: [{ id: "chat-model", name: "默认聊天模型" }], platforms: [{ id: "qq", name: "测试 QQ 连接" }],
        settings: { persona_id: "student", retained_unknown: { keep: true }, modules: { life: true, state: true, memory: true, reply: true, journal: true, debug: true }, models: { default: "chat-model" }, character: { profile: "喜欢天文学和散步的高中生。", world: "在靠海的小城上学。", timezone: "Asia/Shanghai", energy: 75, mood: "平静" }, sessions: [{ umo: scope, enabled: true, weight: 1, retained: "keep" }, { umo: "qq:GroupMessage:42_100", enabled: true, weight: 2 }], news: { sources: ["BBC 中文", "Google 新闻中文", "Solidot", "Hacker News", "MIT Technology Review", "Ars Technica"].map((name, i) => ({ id: `rss-${i}`, name, url: `https://example.test/feed-${i}.xml`, enabled: true })), limit: 5 }, weather: { location: "北京", api_host: "test.re.qweatherapi.com", auth_mode: "api_key", credential: "fixture-credential" }, bilibili: { recent_limit: 5 }, daily_digest: { sources: [{ id: "heya", name: "黑鸦 Heya", uid: "3706929260006322", keywords: "早报 日报", time: "12:00", enabled: true }, { id: "juya", name: "橘鸦 Juya", uid: "285286947", keywords: "日报", time: "23:00", enabled: true }] }, social: { target_count: 1, cooldown_minutes: 60, daily_limit: 5, interjection_interval_minutes: 30, quiet_start: "23:00", quiet_end: "08:00" }, life: { tick_seconds: 60, detail_minutes: 10, daily_plan_time: "06:00", activity_count: 10, news_count: 2, search_count: 2, social_count: 3, retained_nested: true }, debug: { retain_per_category: 10 } },
        sessions: [{ umo: scope, title: "与朋友的私聊", persona_id: "student" }], state: { energy: 75, mood: "有点期待", routine: "上午上课，傍晚散步。" },
        modules: [{ id: "life", enabled: true, status: "ready" }, { id: "memory", enabled: true, status: "ready" }, { id: "bilibili", enabled: false, status: "disabled", error: "依赖插件尚未启用" }], bilibili_dependency: { available: false, text: "依赖插件尚未启用" },
        activities, action_usage: [{ id: "news-used", kind: "news", date: day }, { id: "social-used", kind: "social", date: day }, { id: "future-used", kind: "search", date: tomorrow }], detail_history: [],
        day_summary: { date: day, counts: { news: { started: 1, arranged: 1, success: 0, failed: 1, skipped: 2 }, search: { started: 0, arranged: 2, success: 0, failed: 0, skipped: 0 }, social: { started: 1, arranged: 0, success: 1, failed: 0, skipped: 1 } } },
        prompt_template_history: [{ id: "life.detail", task: "life.detail", template: "旧版指令：不能新增行动。", archived_at: new Date().toISOString() }],
        life_days: [{ date: day, scope: "global", parameters: { activity_count: 10, news_count: 2, search_count: 2, social_count: 3 }, full_request: { prompt: "daily plan complete request" }, raw_json: JSON.stringify({ activities: activities.slice(0, 10) }), adopted_activities: activities.slice(0, 10) }],
        memories: [{ id: "m1", text: "约好了明天一起聊流星雨", kind: "event", scope, person_id: "42", important: true }, { id: "m2", text: "朋友最近喜欢看天文纪录片", kind: "knowledge", scope, person_id: "42" }, { id: "m3", text: "<img src=x onerror=window.xss=true>", kind: "event", scope: "global" }],
        observations: [{ id: "o1", module: "news", title: "今晚试着看看星空", selection_reason: "喜欢天文", factual_summary: "本周有流星雨观测窗口。", impression: "想在放学后看看天空。", reading_basis: "RSS 摘要", sources: [{ url: "https://example.test/story" }], scope: "global" }, { id: "o2", module: "search", title: "流星雨观测地点", query: "城市周边 观星", factual_summary: "搜索提供了两处公园的信息。", impression: "下次想和朋友讨论路线。", reading_basis: "搜索结果摘要", sources: ["https://example.test/search"], scope: "global" }, { id: "o3", module: "weather", text: "晴，26°C，适合散步。", scope: "global" }],
        entries: [{ id: "j1", day, text: "晚风很舒服，回家看到一颗很亮的星。", kind: "journal", scope: "global" }], events: [], deliveries: [{ id: "d1", umo: scope, text: "今晚还想一起聊星星吗？", status: "sent" }], usage: [], diagnostics: [],
        debug: { templates: [{ task: "life.plan_day", default_template: template, template }] },
        debug_records: [{ id: "debug-1", kind: "model", category: "life.plan_day", task: "life.plan_day", module: "life", scope: "global", status: "success", boundary: "本插件提交给 AstrBot 的请求", request: { module: "life", task: "life.plan_day", scope: "global", provider_id: "chat-model", system_prompt: "Full persona system", prompt_mode: "structured", prompt: "Private test context kept outside templates", contexts: [{ role: "user", content: "完整历史" }], parameters: { temperature: 0.6 }, template, dynamic_context: { memories: ["测试记忆"] } }, response: { completion_text: "{\"activities\":[]}" } }],
      };
      window.fixture.version = "0.2.3-test";
      delete window.fixture.settings.character.energy; delete window.fixture.state.energy;
      for (const kind of ["news", "search", "social"]) delete window.fixture.settings.life[`${kind}_count`];
      window.fixture.settings.modules.drives = true;
      window.fixture.drives = { enabled: true, meters: {
        loneliness: { value: 0, config: { growth_per_hour: 10, costs: { social: 10 }, stages: [{ max: 40, text: "不是很想聊天。" }, { max: 80, text: "想偷偷看一眼 QQ 聊天。" }, { max: 100, text: "必须聊天。" }] } },
        energy: { value: 75.25, config: { growth_per_hour: 10, costs: { news: 10, search: 10 }, stages: [{ max: 40, text: "暂时不太想阅读新闻或主动搜索。" }, { max: 80, text: "想了解新鲜事，或查查感兴趣的问题。" }, { max: 100, text: "很想阅读新闻或主动搜索，了解些新东西。" }] } },
      } };
      window.fixture.settings.drives = Object.fromEntries(Object.entries(window.fixture.drives.meters).map(([key, meter]) => [key, structuredClone(meter.config)]));
      window.refreshDriveFixture = () => {
        window.fixture.drives.enabled = window.fixture.settings.modules.drives;
        for (const meter of Object.values(window.fixture.drives.meters)) {
          meter.display_value = Math.floor(meter.value);
          const index = meter.config.stages.findIndex((stage) => stage.max >= meter.display_value);
          meter.stage = { min: index ? meter.config.stages[index - 1].max + 1 : 0, ...meter.config.stages[index] };
          meter.thought = window.fixture.drives.enabled ? meter.stage.text : "";
        }
      };
      window.refreshDriveFixture();
      window.fixture.provider_capture_available = true;
      window.fixture.session_status = [
        { umo: scope, actual_scope: scope, platform_id: "qq", persona_id: "student", persona_match: true, allowed: true, history_status: "found", history_count: 2, history_source: "AstrBot 当前对话", conversation_id: "current-private", reason: "可接入；已找到历史", checked_at: Date.now() / 1000 },
        { umo: "qq:GroupMessage:42_100", actual_scope: "qq:GroupMessage:42_100", persona_id: "student", allowed: true, history_status: "empty", history_count: 0, reason: "可接入；首次对话／暂无历史" },
      ];
      const steps = [
        ["chat.turn", "turn", { text: "帮我查一下数学资料" }, { sent: 1 }, "sent"],
        ["chat.route", "event", { allowed: true }, { reason: "白名单与人格匹配" }, "success"],
        ["chat.context", "event", { history: { count: 2 }, dynamic_context: { memories: ["本场合记忆"] } }, {}, "success"],
        ["reply.model", "model", { provider_id: "chat-model", arguments: { prompt: "数学资料", contexts: [{ role: "user", content: "实际采用的历史" }] } }, { completion_text: "", raw_completion: { choices: [{ message: { tool_calls: [{ id: "lookup-1" }] } }] } }, "success"],
        ["reply.tool", "tool", { name: "lookup", arguments: { query: "数学资料" } }, { content: [{ type: "text", text: "数学资料搜索结果" }] }, "success"],
        ["reply.model", "model", { provider_id: "chat-model", arguments: { contexts: [{ role: "tool", content: "数学资料搜索结果" }] } }, { completion_text: "找到一份数学笔记", raw_completion: { choices: [{ message: { content: "找到一份数学笔记" } }] } }, "success"],
        ["reply.send", "message", { message: { chain: [{ type: "Plain", text: "找到一份数学笔记" }] } }, { accepted: true }, "sent"],
      ];
      steps.forEach(([task, kind, request, response, status], index) => window.fixture.debug_records.push({ id: `chat-step-${index}`, turn_id: "round-1", parent_id: index ? "chat-step-0" : "", task, category: task, module: "reply", scope, kind, request, response, status, created_at: 1800000000 + index }));
      window.fixture.debug_records.find((record) => record.task === "reply.model").request.arguments.temperature = 0.7;
      const firstRequest = JSON.stringify({ model: "deepseek-chat", messages: [{ role: "system", content: "小夏喜欢天文和散步。\n今天在教室复习函数。\r\n课后去公园散步。" }, { role: "user", content: "帮我查一下数学资料" }], tools: [{ type: "function", function: { name: "lookup", parameters: { type: "object" } } }], temperature: 0.7, extension_field: { path: "C:\\notes\\new.json", literal: "\\n", long_text: "连续的长文本".repeat(120), text: "<img src=x onerror=window.debugXss=true>" } });
      const firstResponse = JSON.stringify({ id: "chatcmpl-first", choices: [{ message: { role: "assistant", content: null, tool_calls: [{ id: "lookup-1", type: "function", function: { name: "lookup", arguments: '{"query":"数学资料"}' } }] } }], usage: { prompt_tokens: 120, completion_tokens: 18 }, extension_field: "不得丢弃这个未知字段" }, null, 2);
      const secondRequest = JSON.stringify({ model: "deepseek-chat", messages: [{ role: "tool", tool_call_id: "lookup-1", content: "数学资料搜索结果" }] });
      const secondResponse = JSON.stringify({ id: "chatcmpl-second", choices: [{ message: { role: "assistant", content: "找到一份数学笔记", reasoning_content: "这份笔记与本次学习活动有关。" } }], usage: { prompt_tokens: 160, completion_tokens: 22 } });
      window.rawFixture = { firstRequest, firstResponse, secondRequest, secondResponse, sse: 'data: {"choices":[{"delta":{"content":"晚风"}}]}\n\ndata: {"choices":[{"delta":{"content":"很舒服"}}]}\n\ndata: [DONE]\n\n' };
      window.fixture.debug_views = [
        { id: "round-1", task: "chat.turn", scope, created_at: 1800000000, status: "sent", legacy: false, categories: ["chat.turn", "reply.model", "reply.tool"], sources: [{ title: "当前日程", source: "Living World 今日日程 · 本次私聊", placement: "本轮末尾", content: "14:00—15:00 数学课，复习函数。" }, { title: "相关记忆", source: "长期记忆 · 当前场合", placement: "本轮末尾", content: "答应朋友找一些数学资料。" }, { title: "聊天历史", source: "AstrBot 当前对话", placement: "历史消息", content: [{ role: "user", content: "<img src=x onerror=window.debugXss=true>" }] }], injected_text: "【当前时间】2026-09-06 14:30\n【当前活动】数学课，正在复习函数。\n【相关记忆】答应朋友找一些数学资料。", calls: [
          { id: "http-1", record_id: "chat-step-3", provider_id: "chat-model", model: "deepseek-chat", method: "POST", url: "https://model.test/v1/chat/completions", status: "success", capture_status: "captured", http_status: 200, request_body: firstRequest, response_body: firstResponse, response_type: "json", reading: { text: "", tool_calls: [{ name: "lookup", arguments: { query: "数学资料" } }], usage: { prompt_tokens: 120, completion_tokens: 18 } } },
          { id: "http-2", record_id: "chat-step-5", provider_id: "chat-model", model: "deepseek-chat", method: "POST", url: "https://model.test/v1/chat/completions", status: "success", capture_status: "captured", http_status: 200, request_body: secondRequest, response_body: secondResponse, response_type: "json", reading: { text: "找到一份数学笔记", reasoning: "这份笔记与本次学习活动有关。", usage: { prompt_tokens: 160, completion_tokens: 22 } } },
        ], adopted: ["找到一份数学笔记"], sends: [{ status: "sent", content: "找到一份数学笔记" }, { status: "partial", content: [{ type: "Plain", text: "还有一个练习题" }], error: "第二段发送未确认" }] },
        { id: "stream-1", task: "journal.write", scope: "global", created_at: 1800000001, status: "success", sources: [], injected_text: "", calls: [{ id: "http-stream", status: "success", request_body: '{"model":"deepseek-chat","stream":true,"messages":[]}', response_body: window.rawFixture.sse, response_type: "sse", reading: { text: "晚风很舒服" } }], adopted: [], sends: [] },
        { id: "unavailable-1", task: "chat.turn", scope: "qq:FriendMessage:99", status: "skipped", error: "绑定人格不匹配，未进入模型阶段", sources: [], injected_text: "", calls: [], adopted: [], sends: [] },
        { id: "debug-1", task: "life.plan_day", scope: "global", status: "success", legacy: true, sources: [{ title: "旧版日程请求", source: "宿主请求快照，非 API 原文", content: "旧版保存的提示词资料" }], injected_text: "", calls: [], adopted: [{ activities: [activities[0]] }], sends: [] },
      ];
      Object.assign(window.fixture.session_status[0], { platform_name: "aiocqhttp", bound_persona: "student", persona_source: "host_default", reason_code: "allowed" });
      window.AstrBotPluginPage = {
        ready: async () => ({ isDark: false }),
        apiGet: async (endpoint) => { window.calls.push({ endpoint, method: "GET" }); return structuredClone(endpoint === "export" ? { version: 1, settings: window.fixture.settings } : window.fixture); },
        apiPost: async (endpoint, body) => {
          window.calls.push({ endpoint, method: "POST", body: structuredClone(body) });
          if (window.failNext) { window.failNext = false; throw new Error("测试来源暂时不可用"); }
          if (endpoint === "settings") {
            window.fixture.settings = structuredClone(body);
            for (const [id, config] of Object.entries(body.drives)) window.fixture.drives.meters[id].config = structuredClone(config);
            window.refreshDriveFixture();
          }
          if (["save_drive_settings", "set_drive_value"].includes(body.action)) {
            const meter = window.fixture.drives.meters[body.id];
            if (body.action === "set_drive_value") meter.value = body.value;
            else { meter.config = structuredClone(body.config); window.fixture.settings.drives[body.id] = structuredClone(body.config); }
            window.refreshDriveFixture();
            return structuredClone(window.fixture.drives);
          }
          if (body.action === "inspect_session") return window.inspectError
            ? { umo: body.scope, actual_scope: body.scope, history_status: "error", reason: "测试历史服务不可用", allowed: false }
            : structuredClone(window.fixture.session_status.find((row) => row.umo === body.scope));
          if (body.action === "debug_build") return { request: structuredClone(window.fixture.debug_records[0].request) };
          if (body.action === "debug_test") return { status: "success", text: "测试回复", test_only: true, notice: "No business side effects" };
          if (body.action === "regenerate_day") {
            const old = window.fixture.life_days[0];
            const previous = window.fixture.activities.filter((row) => row.date === body.date);
            window.fixture.life_day_history = [{ ...structuredClone(old), id: "previous-day", archived_at: new Date().toISOString(), activities: structuredClone(previous) }];
            const replacement = previous.map((row) => ({ ...row, id: `regenerated-${row.id}`, title: `重生成：${row.title}`, actions: {}, detailed: false, description: "", detail_version: "" }));
            window.fixture.activities = [...window.fixture.activities.filter((row) => row.date !== body.date), ...replacement];
            window.fixture.life_days[0] = { ...old, adopted_activities: replacement, raw_json: JSON.stringify({ activities: replacement }) };
            return replacement;
          }
          if (body.action === "detail_activity") {
            const row = window.fixture.activities.find((item) => item.id === body.id);
            if (row.detailed) window.fixture.detail_history.push({ id: `history-${row.detail_version}`, activity_id: row.id, archived_at: new Date().toISOString(), activity: structuredClone(row), reason: "重新细化" });
            row.detail_version = `detail-${window.calls.length}`; row.detailed = true; row.description = body.instruction || "围绕函数学习生成的活动细节"; delete row.detail_error;
            row.actions = { news: { enabled: false, reason: "课堂不需要新闻", intent: "", at: null, execution: { status: "disabled" } }, search: { enabled: true, reason: "遇到具体学习疑问，需要核对函数概念", intent: "搜索函数学习方法", at: `${tomorrow}T09:10:00+08:00`, execution: { status: "pending" } }, social: { enabled: false, reason: "本次要求不安排主动聊天", intent: "", at: null, execution: { status: "disabled" } } };
            return structuredClone(row);
          }
          if (body.action === "update_activity") {
            const row = window.fixture.activities.find((item) => item.id === body.id);
            if (row.detailed) window.fixture.detail_history.push({ id: `history-${row.detail_version}`, activity_id: row.id, archived_at: new Date().toISOString(), activity: structuredClone(row), reason: "大纲已修改" });
            Object.assign(row, body.patch, { detailed: false, actions: {}, description: "", detail_version: "" });
            return structuredClone(row);
          }
          return { status: "success", action: body.action };
        },
      };
    });
    await page.goto("http://living-world.test/");
    await page.getByText("已连接 AstrBot", { exact: true }).waitFor();
    const bodyCases = [
      { folds: 1, raw: '{"2":2,"1":1,"same":900719925474099312345,"same":1.2300e+04,"negative":-0}', expected: '{\n  "2": 2,\n  "1": 1,\n  "same": 900719925474099312345,\n  "same": 1.2300e+04,\n  "negative": -0\n}' },
      { folds: 2, raw: '[{},[],[1,2],true,null,""]', expected: '[\n  {},\n  [],\n  [\n    1,\n    2\n  ],\n  true,\n  null,\n  ""\n]' },
      { raw: String.raw`"第一行\n第二行\r\n第三行\r第四行\u000a第五行\u000D\u000A第六行"`, expected: '"第一行\n第二行\n第三行\n第四行\n第五行\n第六行"' },
      { raw: String.raw`"C:\\notes\\new.json"`, expected: String.raw`"C:\\notes\\new.json"` },
      { raw: String.raw`"字面量：\\n，Unicode 字面量：\\u000a，引号：\"，括号：{} []，制表符：\t"`, expected: String.raw`"字面量：\\n，Unicode 字面量：\\u000a，引号：\"，括号：{} []，制表符：\t"` },
      { raw: String.raw`"\\\n尾行"`, expected: '"\\\\\n尾行"' },
      { raw: '{"unfinished":"line\\n', expected: '{"unfinished":"line\\n' },
      { raw: 'HTTP 502\nUpstream unavailable <html>\\n', expected: 'HTTP 502\nUpstream unavailable <html>\\n' },
      { raw: '', expected: '' },
      { folds: 1, type: "sse", raw: ': heartbeat\r\nid: 7\r\nevent: delta\r\ndata: {"text":"第一行\\n第二行","n":900719925474099312345}\r\n\r\ndata: [DONE]\r\n\r\n', expected: ': heartbeat\r\nid: 7\r\nevent: delta\r\ndata: {\r\ndata:   "text": "第一行\r\ndata:   第二行",\r\ndata:   "n": 900719925474099312345\r\ndata: }\r\n\r\ndata: [DONE]\r\n\r\n' },
      { folds: 1, type: "sse", raw: 'event: delta\ndata: {"text":\ndata: "多行事件"}\n\n', expected: 'event: delta\ndata: {\ndata:   "text": "多行事件"\ndata: }\n\n' },
      { type: "sse", raw: 'data: {"text":"未结束事件"}\n', expected: 'data: {"text":"未结束事件"}\n' },
      { type: "sse", raw: 'data: {"text":"broken\n\ndata: plain error\n\n', expected: 'data: {"text":"broken\n\ndata: plain error\n\n' },
      { folds: 1, type: "sse", raw: 'retry: 1000\rdata:{"ok":true}\r\rdata: [DONE]\r\r', expected: 'retry: 1000\rdata: {\rdata:   "ok": true\rdata: }\r\rdata: [DONE]\r\r' },
    ];
    await page.evaluate((cases) => {
      window.savedFormattingViews = window.fixture.debug_views;
      window.fixture.debug_views = cases.map(({ raw, type = "json" }, index) => ({ id: `format-${index}`, task: "debug.test", scope: "global", status: "success", sources: [], calls: [{ id: `format-call-${index}`, request_body: "{}", response_body: raw, response_type: type, status: "success" }] }));
      location.hash = "debug";
    }, bodyCases);
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    const rootFolds = (entry) => entry.locator(".debug-json-document > .debug-json-node");
    const foldToggle = (node) => node.locator(":scope > .debug-json-toggle");
    for (const [index, { raw, type = "json", expected, folds = 0 }] of bodyCases.entries()) {
      const entry = page.locator(`.debug-round[data-turn="format-${index}"]`);
      if (await entry.getAttribute("open") === null) await entry.locator(":scope > summary").click();
      await entry.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
      assert.equal(await entry.locator(".debug-raw").textContent(), expected, `Readable ${type} preserves content and safely handles escapes`);
      assert.equal(await entry.locator(".debug-json-toggle").count(), folds, "Only nonempty JSON containers fold; strings and incomplete events do not");
      assert.equal(await entry.locator(".debug-fold-controls").isVisible(), folds > 0);
      if (folds) {
        assert.equal(await entry.locator('.debug-json-toggle[aria-expanded="true"]').count(), folds, "JSON containers start expanded");
        await entry.getByRole("button", { name: "全部收起", exact: true }).click();
        assert.equal(await entry.locator('.debug-json-toggle[aria-expanded="false"]').count(), folds);
        if (type === "json") assert.equal(await entry.locator(".debug-raw").innerText(), raw.startsWith("[") ? "[…]" : "{…}");
        if (index === 9) assert.equal(await entry.locator(".debug-raw").innerText(), ': heartbeat\r\nid: 7\r\nevent: delta\r\ndata: {…}\r\n\r\ndata: [DONE]\r\n\r\n', "SSE metadata and event boundaries survive folding");
        await entry.getByRole("button", { name: "全部展开", exact: true }).click();
        assert.equal(await entry.locator(".debug-raw").textContent(), expected, "Expanding all restores the complete formatted content");
        await foldToggle(rootFolds(entry).first()).click();
      }
      await entry.getByRole("button", { name: "原文", exact: true }).click();
      assert.equal(await entry.locator(".debug-raw").textContent(), raw, "Original view remains byte-for-byte text");
      assert.equal(await entry.locator(".debug-fold-controls").isVisible(), false);
    }
    const changedBody = page.locator('.debug-round[data-turn="format-0"]');
    await changedBody.getByRole("button", { name: "格式化显示", exact: true }).click();
    assert.equal(await foldToggle(rootFolds(changedBody).first()).getAttribute("aria-expanded"), "false", "Raw mode preserves folding state");
    await page.evaluate(() => { window.fixture.debug_views[0].calls[0].response_body = '{"updated":{"value":2}}'; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.equal(await changedBody.locator('.debug-json-toggle[aria-expanded="true"]').count(), 2, "Changed body resets folding for the same call");
    await page.evaluate(() => { window.fixture.debug_views = window.savedFormattingViews; delete window.savedFormattingViews; location.hash = "overview"; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.equal(await page.evaluate(() => window.calls.filter((call) => call.method === "POST").length), 0, "Opening must be read-only");
    for (const label of ["心情", "精力", "地点", "睡眠", "天气"]) await page.getByText(label, { exact: true }).first().waitFor();
    await page.getByRole("heading", { name: "今日时间线", exact: true }).waitFor();
    await page.getByRole("heading", { name: "下一次主动联系", exact: true }).waitFor();
    assert.ok(await page.locator('a[href="#whitelist"]').count() >= 2);
    if (process.env.LIVING_WORLD_UI_SCREENSHOT) await page.screenshot({ path: process.env.LIVING_WORLD_UI_SCREENSHOT, fullPage: true });
    await page.locator('a[data-view="whitelist"]').click();
    await page.locator('.whitelist-entry').first().waitFor();
    assert.equal(await page.locator('.whitelist-entry').count(), 2);
    await page.getByText("已找到历史：2 条", { exact: true }).waitFor();
    await page.getByText("首次对话／暂无历史", { exact: true }).waitFor();
    await page.getByText("配置允许接入", { exact: true }).first().waitFor();
    await page.getByText("尚未观察到实际聊天接入；配置检查通过不代表已经注入上下文。", { exact: true }).first().waitFor();
    assert.ok((await page.locator('.whitelist-entry').first().innerText()).includes("人格来源：AstrBot 默认设置"));
    const firstTarget = page.locator('.whitelist-entry').first();
    await firstTarget.getByRole("button", { name: "检查会话与历史", exact: true }).click();
    await page.getByText("会话检查完成；没有调用模型或发送消息", { exact: true }).waitFor();
    assert.deepEqual(await page.evaluate(() => window.calls.filter((call) => call.method === "POST").map((call) => call.body.action)), ["inspect_session"]);
    await page.evaluate(() => { window.inspectError = true; });
    await firstTarget.getByRole("button", { name: "检查会话与历史", exact: true }).click();
    await firstTarget.getByText("历史读取失败", { exact: true }).waitFor();
    await page.evaluate(() => { window.inspectError = false; });
    await firstTarget.getByRole("button", { name: "检查会话与历史", exact: true }).click();
    await firstTarget.getByText("已找到历史：2 条", { exact: true }).waitFor();
    await page.evaluate(() => {
      const actual = { turn_id: "round-1", status: "injected", at: Date.now() / 1000, reason: "Living World 上下文已交给宿主 Agent" };
      window.fixture.session_status[0].context_status = { last_attempt: actual, last_injected: actual };
    });
    await firstTarget.getByRole("button", { name: "检查会话与历史", exact: true }).click();
    await firstTarget.getByText("最近实际注入：", { exact: false }).waitFor();
    await firstTarget.getByRole("link", { name: "查看这轮接入记录", exact: true }).click();
    await page.locator('a[data-view="debug"][aria-current="page"]').waitFor();
    assert.equal(await page.locator('.debug-round[data-turn="round-1"]').getAttribute("open"), "");
    await page.locator('a[data-view="whitelist"]').click();
    await firstTarget.getByText("最近实际注入：", { exact: false }).waitFor();
    if (process.env.LIVING_WORLD_WHITELIST_SCREENSHOT) await page.screenshot({ path: process.env.LIVING_WORLD_WHITELIST_SCREENSHOT, fullPage: true });
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
    assert.equal(await page.locator('[name="character.energy"]').count(), 0, "Energy is configured only in the new module");
    await page.locator('a[data-view="drives"]').click();
    await page.getByRole("tab", { name: "寂寞值", exact: true }).waitFor();
    assert.equal(await page.locator(".drive-status-cards > .card").count(), 2);
    assert.equal(await page.getByRole("tab", { selected: true }).count(), 1);
    assert.equal(await page.locator(".drive-tabs").evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").length), 2, "Two tabs span the full editor width");
    assert.equal(await page.locator(".drive-stage").count(), 3);
    assert.equal(await page.locator('.drive-status-cards [data-drive="energy"] .drive-value').innerText(), "75 / 100");
    assert.equal(await page.locator('[name="drive.value"]').inputValue(), "0");
    await page.locator('[name="drive.growth_per_hour"]').fill("12.5");
    await page.locator('[name="drive.costs.social"]').fill("0");
    await page.locator('[name="drive.value"]').fill("81.75");
    await page.getByRole("tab", { name: "精力", exact: true }).click();
    assert.equal(await page.locator('[name="drive.costs.news"]').inputValue(), "10");
    assert.equal(await page.locator('[name="drive.costs.search"]').inputValue(), "10");
    await page.getByRole("tab", { name: "精力", exact: true }).press("ArrowLeft");
    assert.equal(await page.getByRole("tab", { name: "寂寞值", exact: true }).getAttribute("aria-selected"), "true");
    assert.equal(await page.locator('[name="drive.growth_per_hour"]').inputValue(), "12.5", "Switching tabs retains drafts");
    await page.getByRole("button", { name: "应用当前值", exact: true }).click();
    await page.getByText("当前值已应用，参数与阶段草稿继续保留", { exact: true }).waitFor();
    assert.equal(await page.locator('.drive-status-cards [data-drive="loneliness"] .drive-thought').innerText(), "必须聊天。");
    assert.deepEqual(await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "set_drive_value").body), { action: "set_drive_value", id: "loneliness", value: 81.75 });
    assert.equal(await page.evaluate(() => window.fixture.drives.meters.loneliness.config.growth_per_hour), 10, "Applying a value must not save rate drafts");
    await page.evaluate(() => { window.fixture.drives.meters.loneliness.value = 83.8; window.refreshDriveFixture(); });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.getByText("数值已刷新，未保存的修改继续保留", { exact: true }).waitFor();
    assert.equal(await page.locator("#editor").isVisible(), false);
    assert.equal(await page.locator('[name="drive.growth_per_hour"]').inputValue(), "12.5");
    assert.equal(await page.locator('.drive-status-cards [data-drive="loneliness"] .drive-value').innerText(), "83 / 100");
    await page.locator('[name="drive.value"]').fill("12");
    await page.locator('[name="drive.growth_per_hour"]').focus();
    await page.evaluate(async () => { window.fixture.drives.meters.loneliness.value = 84.9; window.refreshDriveFixture(); await window.driveRefreshTick(); });
    assert.equal(await page.locator('[name="drive.value"]').inputValue(), "12", "Automatic refresh must preserve a manual-value draft");
    assert.equal(await page.locator('[name="drive.growth_per_hour"]').evaluate((node) => node === document.activeElement), true, "Status-only refresh preserves editor focus");
    assert.equal(await page.locator('.drive-status-cards [data-drive="loneliness"] .drive-value').innerText(), "84 / 100");
    await page.getByRole("button", { name: "拆分第 1 阶段", exact: true }).click();
    assert.equal(await page.locator(".drive-stage").count(), 4);
    assert.equal(await page.locator('[name="drive.stage.0.max"]').inputValue(), "20");
    await page.locator('[name="drive.stage.0.max"]').fill("15"); await page.locator('[name="drive.stage.0.max"]').press("Tab");
    assert.match(await page.locator(".drive-stage").nth(1).innerText(), /阶段 2 · 16—40/);
    await page.getByRole("button", { name: "删除第 1 阶段", exact: true }).click();
    assert.match(await page.locator(".drive-stage").first().innerText(), /阶段 1 · 0—40/);
    await page.getByRole("button", { name: "删除第 2 阶段", exact: true }).click();
    assert.match(await page.locator(".drive-stage").first().innerText(), /阶段 1 · 0—80/);
    await page.getByRole("button", { name: "拆分第 1 阶段", exact: true }).click();
    await page.locator('[name="drive.stage.0.max"]').fill("0"); await page.locator('[name="drive.stage.0.max"]').press("Tab");
    assert.equal(await page.getByRole("button", { name: "拆分第 1 阶段", exact: true }).isDisabled(), true, "A one-value stage cannot split");
    await page.locator('[name="drive.stage.0.max"]').fill("40"); await page.locator('[name="drive.stage.0.max"]').press("Tab");
    await page.locator('[name="drive.stage.0.max"]').fill("81"); await page.locator('[name="drive.stage.0.max"]').dispatchEvent("change");
    assert.equal(await page.locator('[name="drive.stage.0.max"]').inputValue(), "40", "Overlapping stage boundaries are rejected");
    await page.getByRole("button", { name: "删除第 1 阶段", exact: true }).click();
    await page.getByRole("button", { name: "删除第 1 阶段", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "删除第 1 阶段", exact: true }).isDisabled(), true);
    assert.equal(await page.locator('[name="drive.stage.0.max"]').getAttribute("readonly"), "");
    await page.getByRole("button", { name: "拆分第 1 阶段", exact: true }).click();
    await page.getByRole("button", { name: "拆分第 1 阶段", exact: true }).click();
    await page.locator('[name="drive.stage.0.max"]').fill("40"); await page.locator('[name="drive.stage.0.max"]').press("Tab");
    await page.locator('[name="drive.stage.1.max"]').fill("80"); await page.locator('[name="drive.stage.1.max"]').press("Tab");
    for (const [index, text] of ["不是很想聊天。", "想偷偷看一眼 QQ 聊天。", "必须聊天。<img src=x onerror=window.driveXss=true>"].entries()) await page.locator(`[name="drive.stage.${index}.text"]`).fill(text);
    await page.evaluate(() => { window.failNext = true; });
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await page.getByText("测试来源暂时不可用", { exact: true }).waitFor();
    assert.equal(await page.locator('[name="drive.growth_per_hour"]').inputValue(), "12.5", "Failed save retains the configuration draft");
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await page.getByText("参数与阶段已保存，当前值继续按运行规则变化", { exact: true }).waitFor();
    const driveSaved = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "save_drive_settings").body);
    assert.equal(Object.hasOwn(driveSaved, "value"), false); assert.equal(driveSaved.config.growth_per_hour, 12.5); assert.equal(driveSaved.config.costs.social, 0);
    assert.deepEqual(driveSaved.config.stages.map((stage) => stage.max), [40, 80, 100]);
    assert.equal(await page.evaluate(() => window.fixture.drives.meters.loneliness.value), 84.9, "Saving configuration does not apply the manual-value draft");
    assert.equal(await page.locator('[name="drive.value"]').inputValue(), "12");
    assert.equal(await page.locator(".drive-thought img").count(), 0); assert.equal(await page.evaluate(() => window.driveXss), undefined);
    await page.locator('[name="drive.stage.2.text"]').fill("必须聊天。");
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await page.getByRole("button", { name: "应用当前值", exact: true }).click();
    await page.getByRole("tab", { name: "精力", exact: true }).click();
    await page.locator('[name="drive.costs.news"]').fill("7.5"); await page.locator('[name="drive.costs.search"]').fill("15");
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    assert.deepEqual(await page.evaluate(() => window.fixture.drives.meters.energy.config.costs), { news: 7.5, search: 15 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, "No desktop meter overflow");
    if (process.env.LIVING_WORLD_DRIVES_SCREENSHOT) await page.locator("#content").screenshot({ path: process.env.LIVING_WORLD_DRIVES_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("tab", { name: "寂寞值", exact: true }).click();
    await page.getByRole("button", { name: "拆分第 1 阶段", exact: true }).click();
    await page.getByRole("button", { name: "删除第 2 阶段", exact: true }).click();
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, "No mobile meter overflow");
    if (process.env.LIVING_WORLD_DRIVES_MOBILE_SCREENSHOT) await page.locator("#content").screenshot({ path: process.env.LIVING_WORLD_DRIVES_MOBILE_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    await page.setViewportSize({ width: 1600, height: 1050 });
    await page.evaluate(() => { window.fixture.settings.modules.drives = false; window.refreshDriveFixture(); });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.equal(await page.locator(".drive-status-cards").getByText("已暂停", { exact: true }).count(), 2);
    assert.equal(await page.locator(".drive-status-cards").getByText("模块已暂停，下次细化不注入这项想法。", { exact: true }).count(), 2);
    await page.locator('[name="drive.value"]').fill("40.99"); await page.getByRole("button", { name: "应用当前值", exact: true }).click();
    assert.match(await page.locator('.drive-status-cards [data-drive="loneliness"]').innerText(), /当前阶段 0—40/);
    await page.evaluate(() => { window.fixture.settings.modules.drives = true; window.refreshDriveFixture(); });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.locator('a[data-view="schedule"]').click();
    await page.getByText("生成输入快照（非 API 原文）", { exact: true }).waitFor();
    await page.getByText("模型生成的日程文本", { exact: true }).waitFor();
    assert.equal(await page.getByText("完整生成请求", { exact: true }).count(), 0);
    for (const [name, value] of [["daily_plan_time", "06:00"], ["activity_count", "10"]]) assert.equal(await page.locator(`[name="life.${name}"]`).inputValue(), value);
    for (const kind of ["news", "search", "social"]) assert.equal(await page.locator(`[name="life.${kind}_count"]`).count(), 0);
    assert.equal(await page.locator('[name="life.activity_count"]').getAttribute("max"), "48");
    assert.equal(await page.getByRole("heading", { name: "大纲生成参数", exact: true }).count(), 1);
    assert.equal(await page.getByRole("heading", { name: "行动每日上限", exact: true }).count(), 0);
    assert.match(await page.locator(".schedule-counts").innerText(), /新闻 · 次[\s\S]*已开始 1 次[\s\S]*已安排 1 次[\s\S]*成功 0 · 失败 1 · 跳过 2/);
    assert.doesNotMatch(await page.locator(".schedule-counts").innerText(), /预留|额度|每日上限/);
    await page.getByRole("button", { name: "保存日程设置", exact: true }).click();
    await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.fixture.settings.drives.loneliness.growth_per_hour), 12.5, "Saving another page must preserve newly saved meter settings");
    assert.equal(await page.evaluate(() => window.calls.some((call) => ["plan_day", "regenerate_day"].includes(call.body?.action))), false, "Saving parameters must not regenerate");
    assert.equal(await page.getByRole("button", { name: "补生成缺失日程", exact: true }).count(), 0);
    await page.locator('[name="life.activity_count"]').fill("11");
    await page.getByRole("button", { name: "重新生成日程", exact: true }).click();
    await page.getByText("请先保存日程设置，再重新生成日程。", { exact: true }).waitFor();
    await page.locator('[name="life.activity_count"]').fill("10");
    await page.getByRole("button", { name: "保存日程设置", exact: true }).click();
    await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor();
    await page.getByRole("button", { name: "重新生成日程", exact: true }).click();
    await page.getByText("重新生成今天的日程", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.some((call) => call.body?.action === "regenerate_day")), false, "Opening confirmation does not call the model");
    await page.locator("#editor-cancel").click();
    await page.getByRole("button", { name: "重新生成日程", exact: true }).click();
    await page.getByRole("button", { name: "调用模型并重新生成", exact: true }).click();
    await page.locator("#editor").waitFor({ state: "hidden" });
    const regeneration = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "regenerate_day").body);
    assert.equal(regeneration.date, await page.getByLabel("日程日期").inputValue());
    assert.equal(await page.locator(".schedule-history").count(), 1);
    assert.equal(await page.locator(".schedule-history").getAttribute("open"), null);
    await page.locator(".schedule-history > summary").click();
    await page.getByText("替换前的活动与实际执行记录", { exact: true }).waitFor();
    if (process.env.LIVING_WORLD_SCHEDULE_SCREENSHOT) await page.locator("#content").screenshot({ path: process.env.LIVING_WORLD_SCHEDULE_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    await page.evaluate(() => { window.fixture.day_regenerating = true; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "重新生成日程", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "批量调整未来活动", exact: true }).isDisabled(), true);
    await page.evaluate(() => { window.fixture.day_regenerating = false; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.getByLabel("日程日期").fill(await page.evaluate(() => window.testTomorrow));
    assert.equal(await page.getByRole("button", { name: "重新生成日程", exact: true }).isDisabled(), true);
    assert.match(await page.locator(".schedule-counts").innerText(), /搜索 · 次[\s\S]*已开始 1 次[\s\S]*已安排 0 次/);
    await page.evaluate(() => {
      const day = window.fixture.life_days[0].date;
      const at = new Date(`${window.testTomorrow}T00:10:00+08:00`).toISOString();
      window.fixture.activities.push({ id: "cross-day-finished", date: day, start: `${day}T23:30:00+08:00`, end: `${window.testTomorrow}T01:00:00+08:00`, scope: "qq:FriendMessage:42", title: "跨日阅读", status: "completed", actions: { news: { id: "cross-day-news", enabled: true, at, execution: { status: "success", finished_at: at } } } });
      window.fixture.action_usage.push({ id: "cross-day-news", kind: "news", date: window.testTomorrow });
    });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.match(await page.locator(".schedule-counts").innerText(), /新闻 · 次[\s\S]*已开始 1 次[\s\S]*已安排 1 次[\s\S]*成功 1/, "Arranged includes completed adopted decisions on the action's local date, including private activities from the prior day");
    await page.evaluate(() => {
      window.fixture.activities = window.fixture.activities.filter((item) => item.id !== "cross-day-finished");
      window.fixture.action_usage = window.fixture.action_usage.filter((item) => item.id !== "cross-day-news");
    });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.match(await page.locator(".activity-detail").innerText(), /自动重试已结束/);
    await page.getByRole("button", { name: "细化活动", exact: true }).click();
    assert.equal(await page.evaluate(() => window.calls.some((call) => call.body?.action === "detail_activity")), false, "Opening the detail dialog must not call a model");
    await page.locator('#editor [name="instruction"]').fill("这次专心复习函数，不安排主动聊天。<img src=x onerror=window.detailXss=true>");
    await page.locator("#editor-submit").click(); await page.locator("#editor").waitFor({ state: "hidden" });
    const firstDetail = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "detail_activity").body);
    assert.equal(firstDetail.id, "future"); assert.equal(firstDetail.regenerate, false); assert.ok(firstDetail.instruction.startsWith("这次专心复习函数"));
    assert.equal(await page.evaluate(() => window.detailXss), undefined); assert.equal(await page.locator(".activity-detail img").count(), 0);
    await page.locator(".activity-decisions > summary").click();
    assert.match(await page.locator(".activity-decisions").innerText(), /遇到具体学习疑问，需要核对函数概念/);
    assert.match(await page.locator(".activity-decisions").innerText(), /主动聊天 · 不安排/);
    await page.getByRole("button", { name: "重新细化", exact: true }).click();
    assert.equal(await page.locator('#editor [name="instruction"]').inputValue(), "", "A one-time instruction is not reused automatically");
    await page.locator('#editor [name="instruction"]').fill("保留搜索，稍后读一本数学书。");
    await page.evaluate(() => { window.failNext = true; });
    await page.locator("#editor-submit").click();
    await page.getByText("测试来源暂时不可用", { exact: true }).waitFor();
    assert.equal(await page.locator("#editor").isVisible(), true, "A rejected refinement retains the draft for retry");
    assert.ok((await page.evaluate(() => window.fixture.activities.find((item) => item.id === "future").description)).startsWith("这次专心复习函数"));
    await page.locator("#editor-submit").click(); await page.locator("#editor").waitFor({ state: "hidden" });
    const secondDetail = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "detail_activity").body);
    assert.equal(secondDetail.regenerate, true); assert.equal(secondDetail.instruction, "保留搜索，稍后读一本数学书。");
    assert.equal(await page.locator(".activity-detail-history").getAttribute("open"), null);
    await page.locator(".activity-detail-history > summary").click();
    assert.equal(await page.locator(".activity-detail-history details").count(), 1);
    await page.locator(".activity-decisions > summary").click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, "No desktop schedule overflow");
    if (process.env.LIVING_WORLD_DETAIL_SCREENSHOT) await page.locator("#content").screenshot({ path: process.env.LIVING_WORLD_DETAIL_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, "No mobile detail overflow");
    if (process.env.LIVING_WORLD_DETAIL_MOBILE_SCREENSHOT) await page.locator("#content").screenshot({ path: process.env.LIVING_WORLD_DETAIL_MOBILE_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    await page.setViewportSize({ width: 1600, height: 1050 });
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.locator('#editor [name="title"]').fill("数学课，今天复习函数");
    await page.locator("#editor-submit").click(); await page.locator("#editor").waitFor({ state: "hidden" });
    const edit = await page.evaluate(() => window.calls.findLast((call) => call.body?.action === "update_activity").body);
    assert.equal(edit.patch.title, "数学课，今天复习函数"); assert.equal(Object.hasOwn(edit.patch, "actions"), false);
    assert.ok(!Object.hasOwn(edit.patch, "scope") && !Object.hasOwn(edit.patch, "status"));
    assert.equal(await page.getByRole("button", { name: "细化活动", exact: true }).count(), 1, "An outline edit invalidates the old detail");
    await page.getByRole("button", { name: "批量调整未来活动", exact: true }).click();
    const batch = JSON.parse(await page.locator('#editor [name="json"]').inputValue());
    assert.equal(batch.updates.length, 1); assert.ok(!Object.hasOwn(batch.updates[0].changes, "actions"), "Batch edits only submit the outline");
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
    await page.locator('a[data-view="sources"][aria-current="page"]').waitFor();
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
    await page.locator('a[data-view="debug"][aria-current="page"]').waitFor();
    const round = page.locator('.debug-round[data-turn="round-1"]');
    assert.equal(await page.locator('.debug-round').count(), 4);
    assert.equal(await round.getAttribute("open"), "");
    assert.equal(await round.getByRole("tab").count(), 4);
    assert.ok((await round.innerText()).includes("Living World 今日日程 · 本次私聊"));
    assert.ok((await round.innerText()).includes("插件实际加入的完整文本"));
    const sourceCards = round.locator(".debug-sources > details.debug-source");
    assert.equal(await sourceCards.count(), 3);
    assert.equal(await sourceCards.evaluateAll((cards) => cards.every((card) => card.open)), true, "Sources start expanded");
    const assertSingleColumnSources = async () => {
      const boxes = await sourceCards.evaluateAll((cards) => cards.map((card) => { const box = card.getBoundingClientRect(); return { x: box.x, y: box.y, width: box.width, bottom: box.bottom }; }));
      for (let index = 1; index < boxes.length; index++) {
        assert.ok(Math.abs(boxes[index].x - boxes[0].x) < 1 && Math.abs(boxes[index].width - boxes[0].width) < 1, "Every source occupies the same full-width column");
        assert.ok(boxes[index].y >= boxes[index - 1].bottom, "Sources stack vertically");
      }
    };
    await assertSingleColumnSources();
    await sourceCards.first().locator("summary").click();
    assert.equal(await sourceCards.first().getAttribute("open"), null);
    await sourceCards.first().getByText("当前日程", { exact: true }).waitFor();
    await sourceCards.first().getByText("来源：Living World 今日日程 · 本次私聊 · 放入：本轮末尾", { exact: true }).waitFor();
    assert.equal(await sourceCards.first().locator(".debug-source-content").isVisible(), false);
    assert.equal(await sourceCards.nth(1).getAttribute("open"), "", "Collapsing one source leaves the next expanded");
    await sourceCards.first().locator("summary").focus();
    await page.keyboard.press("Enter");
    assert.equal(await sourceCards.first().getAttribute("open"), "", "Native source disclosures support keyboard operation");
    await sourceCards.first().locator("summary").click();
    assert.equal(await page.evaluate(() => window.debugXss), undefined);
    assert.equal(await round.locator("img").count(), 0);
    assert.ok(!(await round.innerText()).includes("个步骤"));
    assert.equal(await page.locator('#navigation a').nth(1).getAttribute("data-view"), "settings");
    assert.ok((await page.locator('#navigation a').nth(1).innerText()).startsWith("02"));
    const downloadedText = async (trigger) => {
      const downloadPromise = page.waitForEvent("download");
      await trigger(); const download = await downloadPromise;
      const chunks = []; for await (const chunk of await download.createReadStream()) chunks.push(chunk);
      return { text: Buffer.concat(chunks).toString("utf8"), filename: download.suggestedFilename() };
    };
    await round.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    assert.equal(await round.getByRole("button", { name: "格式化显示", exact: true }).getAttribute("aria-pressed"), "true");
    const formattedRequest = await round.locator(".debug-raw").textContent();
    assert.ok(formattedRequest.startsWith('{\n  "model": "deepseek-chat",'));
    assert.match(formattedRequest, /小夏喜欢天文和散步。\n\s+今天在教室复习函数。\n\s+课后去公园散步。/);
    assert.ok(formattedRequest.includes('"path": "C:\\\\notes\\\\new.json"'));
    assert.ok(formattedRequest.includes('"literal": "\\\\n"'));
    assert.equal(await round.locator("img").count(), 0, "Formatted bodies render untrusted markup as text");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, "Long formatted JSON wraps on desktop");
    if (process.env.LIVING_WORLD_RAW_DEBUG_SCREENSHOT) await round.screenshot({ path: process.env.LIVING_WORLD_RAW_DEBUG_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    const requestRoot = rootFolds(round).first();
    const messageArray = requestRoot.locator(":scope > .debug-json-children > .debug-json-node").first();
    const messageObjects = messageArray.locator(":scope > .debug-json-children > .debug-json-node");
    const firstMessage = messageObjects.first();
    await foldToggle(firstMessage).click();
    assert.equal(await firstMessage.locator(":scope > .debug-json-children").isVisible(), false);
    assert.equal(await messageObjects.nth(1).locator(":scope > .debug-json-children").isVisible(), true, "Collapsing a message leaves its sibling visible");
    await foldToggle(messageArray).click();
    assert.ok((await round.locator(".debug-raw").innerText()).includes('"messages": […]'), "Collapsed arrays retain their field name");
    await foldToggle(messageArray).focus(); await page.keyboard.press("Enter");
    assert.equal(await foldToggle(messageArray).getAttribute("aria-expanded"), "true", "Keyboard expands the parent");
    assert.equal(await foldToggle(firstMessage).getAttribute("aria-expanded"), "false", "Expanding the parent preserves its child's collapsed state");
    await round.getByRole("tab", { name: "① 上下文与信息来源", exact: true }).click();
    await round.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    assert.equal(await foldToggle(firstMessage).getAttribute("aria-expanded"), "false", "Tab changes retain folding");
    await round.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
    assert.equal(await round.locator('.debug-json-toggle[aria-expanded="false"]').count(), 0, "Response folds are independent from request folds");
    await foldToggle(rootFolds(round).first()).click();
    await round.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    await round.locator('[name="debug_call"]').selectOption("1");
    assert.equal(await round.locator('.debug-json-toggle[aria-expanded="false"]').count(), 0, "Another HTTP call starts expanded");
    await foldToggle(rootFolds(round).first()).click();
    await round.locator('[name="debug_call"]').selectOption("0");
    assert.equal(await foldToggle(requestRoot).getAttribute("aria-expanded"), "true");
    assert.equal(await foldToggle(firstMessage).getAttribute("aria-expanded"), "false", "Returning to an HTTP call restores its own folds");
    await round.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
    assert.equal(await foldToggle(rootFolds(round).first()).getAttribute("aria-expanded"), "false", "Request navigation does not alter the response's folds");
    await round.getByRole("button", { name: "全部展开", exact: true }).click();
    await round.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    if (process.env.LIVING_WORLD_JSON_FOLD_SCREENSHOT) await round.screenshot({ path: process.env.LIVING_WORLD_JSON_FOLD_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    // Formatting is display-only, including while downloading or copying.
    const formattedDownload = await downloadedText(() => round.getByRole("button", { name: "下载请求原文", exact: true }).click());
    assert.equal(formattedDownload.text, await page.evaluate(() => window.rawFixture.firstRequest));
    await round.getByRole("button", { name: "原文", exact: true }).click();
    assert.equal(await round.locator(".debug-raw").textContent(), await page.evaluate(() => window.rawFixture.firstRequest));
    const firstDownload = await downloadedText(() => round.getByRole("button", { name: "下载请求原文", exact: true }).click());
    assert.equal(firstDownload.text, await page.evaluate(() => window.rawFixture.firstRequest));
    assert.ok(firstDownload.filename.endsWith("-request.json"));
    await round.getByRole("button", { name: "格式化显示", exact: true }).click();
    assert.equal(await foldToggle(firstMessage).getAttribute("aria-expanded"), "false", "Switching through original mode retains nested folds");
    await round.getByRole("button", { name: "全部收起", exact: true }).click();
    assert.equal(await round.locator(".debug-raw").innerText(), "{…}");
    await round.getByRole("button", { name: "复制请求原文", exact: true }).click();
    await page.locator("#editor[open]").waitFor();
    assert.equal(await page.locator('#editor [name="copy"]').inputValue(), firstDownload.text);
    await page.locator("#editor-cancel").click();
    await round.getByRole("button", { name: "全部展开", exact: true }).click();
    assert.equal(await round.locator(".debug-raw").textContent(), formattedRequest);
    await round.getByRole("button", { name: "原文", exact: true }).click();
    await round.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
    assert.equal(await round.getByRole("button", { name: "格式化显示", exact: true }).getAttribute("aria-pressed"), "true", "Request and response display preferences are independent");
    await round.getByRole("button", { name: "原文", exact: true }).click();
    assert.equal(await round.locator(".debug-raw").textContent(), await page.evaluate(() => window.rawFixture.firstResponse));
    const responseDownload = await downloadedText(() => round.getByRole("button", { name: "下载返回原文", exact: true }).click());
    assert.equal(responseDownload.text, await page.evaluate(() => window.rawFixture.firstResponse));
    assert.ok(responseDownload.text.includes("不得丢弃这个未知字段"));
    await round.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    await round.getByRole("heading", { name: "模型请求调用的工具", exact: true }).waitFor();
    await round.locator('[name="debug_call"]').selectOption("1");
    await round.getByRole("heading", { name: "回复正文", exact: true }).waitFor();
    await round.getByRole("heading", { name: "接口返回的推理内容", exact: true }).waitFor();
    await round.getByRole("heading", { name: "实际聊天发送", exact: true }).waitFor();
    assert.ok((await round.innerText()).includes("第二段发送未确认"));
    await round.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    assert.equal(await round.locator(".debug-raw").textContent(), await page.evaluate(() => window.rawFixture.secondRequest));
    await round.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
    assert.equal(await round.locator(".debug-raw").textContent(), await page.evaluate(() => window.rawFixture.secondResponse));
    await round.getByRole("tab", { name: "① 上下文与信息来源", exact: true }).click();
    assert.equal(await sourceCards.first().getAttribute("open"), null, "Source collapse state survives tab and request changes");
    await round.locator('[name="debug_call"]').selectOption("0");
    assert.equal(await sourceCards.first().getAttribute("open"), null, "Changing requests within the source view retains disclosure state");
    if (process.env.LIVING_WORLD_DEBUG_SCREENSHOT) await round.screenshot({ path: process.env.LIVING_WORLD_DEBUG_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
    const stream = page.locator('.debug-round[data-turn="stream-1"]');
    await stream.locator(':scope > summary').click();
    await stream.getByRole("heading", { name: "没有可用的信息来源清单", exact: true }).waitFor();
    await stream.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
    assert.ok((await stream.locator(".debug-raw").textContent()).includes('data: {\ndata:   "choices": ['));
    assert.ok((await stream.locator(".debug-raw").textContent()).endsWith("data: [DONE]\n\n"));
    assert.equal(await rootFolds(stream).count(), 2, "SSE event JSON roots remain separate");
    await foldToggle(rootFolds(stream).first()).click();
    assert.equal(await foldToggle(rootFolds(stream).nth(1)).getAttribute("aria-expanded"), "true", "Collapsing one event leaves the next expanded");
    await stream.getByRole("button", { name: "全部收起", exact: true }).click();
    assert.equal(await stream.locator(".debug-raw").innerText(), "data: {…}\n\ndata: {…}\n\ndata: [DONE]\n\n");
    const streamDownload = await downloadedText(() => stream.getByRole("button", { name: "下载返回原文", exact: true }).click());
    assert.equal(streamDownload.text, await page.evaluate(() => window.rawFixture.sse));
    assert.ok(streamDownload.filename.endsWith(".sse"));
    await stream.getByRole("button", { name: "原文", exact: true }).click();
    assert.equal(await stream.locator(".debug-raw").textContent(), streamDownload.text);
    await stream.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    await stream.getByText("晚风很舒服", { exact: true }).waitFor();
    assert.ok((await stream.innerText()).includes("合并后的阅读内容"));
    const missing = page.locator('.debug-round[data-turn="unavailable-1"]');
    await missing.locator(':scope > summary').click();
    await missing.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    await missing.getByRole("heading", { name: "没有捕获 API 原始请求", exact: true }).waitFor();
    assert.equal(await missing.getByRole("button", { name: "下载请求原文", exact: true }).count(), 0);
    const legacy = page.locator('.debug-round[data-turn="debug-1"]');
    await legacy.locator(':scope > summary').click();
    assert.ok((await legacy.innerText()).includes("旧版快照，非 API 原文"));
    await legacy.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
    assert.equal(await legacy.locator(".debug-raw").count(), 0);
    await legacy.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    assert.equal(await legacy.locator(".debug-activity").count(), 1, "Structured activities have a readable timeline");
    await page.locator('[name="category"]').selectOption("reply.tool");
    assert.equal(await page.locator('.debug-round').count(), 1, "Filtering a tool keeps its whole conversation round");
    assert.equal(await page.locator('.debug-round [name="debug_call"] option').count(), 2);
    await page.locator('[name="category"]').selectOption("");
    await round.locator('[name="debug_call"]').selectOption("0");
    await round.getByRole("button", { name: "复制到试跑编辑器", exact: true }).click();
    assert.equal(JSON.parse(await page.locator('[name="request_json"]').inputValue()).parameters.temperature, 0.7, "Provider parameters survive copying to the test editor");
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
    await page.locator(".template-history > summary").click();
    assert.match(await page.locator(".template-history").textContent(), /旧版指令：不能新增行动/);
    assert.equal(await page.locator(".template-history button").count(), 0, "Archived templates are read-only");
    assert.equal(await page.getByRole("tablist", { name: "调用记录四项视图", exact: true, includeHidden: true }).count(), 4);
    await page.evaluate(() => { window.savedViews = window.fixture.debug_views; delete window.fixture.debug_views; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.getByText("数据已刷新", { exact: true }).waitFor();
    const legacyChat = page.locator('.debug-round[data-turn="round-1"]');
    if (await legacyChat.getAttribute("open") === null) await legacyChat.locator(':scope > summary').click();
    assert.ok((await legacyChat.innerText()).includes("旧版快照，非 API 原文"));
    await legacyChat.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    assert.equal(await legacyChat.getByRole("button", { name: "下载请求原文", exact: true }).count(), 0, "Legacy Provider arguments never become a fabricated HTTP body");
    await legacyChat.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    assert.ok((await legacyChat.innerText()).includes("找到一份数学笔记"));
    await page.evaluate(() => { window.fixture.debug_views = window.savedViews; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.getByText("数据已刷新", { exact: true }).waitFor();
    for (const testCase of backendContract.format_cases) {
      const { expected, ...draft } = testCase;
      await page.locator('[name="request_json"]').fill(JSON.stringify(draft));
      await page.locator('[name="request_mode"]').selectOption("raw");
      assert.equal(JSON.parse(await page.locator('[name="request_json"]').inputValue()).prompt, expected, "Structured-to-raw text exactly matches Runtime.format_task_context");
    }
    await page.evaluate((contract) => {
      window.regularDebugFixture = { views: window.fixture.debug_views, records: window.fixture.debug_records };
      window.fixture.debug_views = contract.views; window.fixture.debug_records = contract.records;
    }, backendContract);
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.getByText("数据已刷新", { exact: true }).waitFor();
    const contractChat = page.locator('.debug-round[data-turn="contract-chat"]');
    assert.equal(await page.locator('.debug-round').count(), 4);
    await contractChat.getByText("契约来源清单", { exact: true }).waitFor();
    await contractChat.getByText("角色补充资料：喜欢观察星空。", { exact: true }).waitFor();
    await contractChat.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    await contractChat.getByText("契约测试的真实阅读正文", { exact: true }).waitFor();
    await contractChat.getByRole("heading", { name: "工具实际返回", exact: true }).waitFor();
    await contractChat.getByText("工具真实契约返回资料", { exact: true }).waitFor();
    await contractChat.getByRole("heading", { name: "AstrBot 采用的回复", exact: true }).waitFor();
    await contractChat.getByText("宿主最终采用的契约回复", { exact: true }).waitFor();
    await contractChat.getByText("实际发送的契约正文", { exact: true }).waitFor();
    await contractChat.getByText("fixture-image.png", { exact: true }).waitFor();
    await contractChat.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    await contractChat.getByRole("button", { name: "原文", exact: true }).click();
    assert.equal(await contractChat.locator(".debug-raw").textContent(), backendContract.views[0].calls[0].request_body);
    await contractChat.getByRole("tab", { name: "③ API 原始返回", exact: true }).click();
    await contractChat.getByRole("button", { name: "原文", exact: true }).click();
    assert.equal(await contractChat.locator(".debug-raw").textContent(), backendContract.views[0].calls[0].response_body);
    await contractChat.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    if (process.env.LIVING_WORLD_BACKEND_DEBUG_SCREENSHOT) await page.screenshot({ path: process.env.LIVING_WORLD_BACKEND_DEBUG_SCREENSHOT, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    for (const label of ["① 上下文与信息来源", "② API 原始请求", "③ API 原始返回", "④ 回复阅读版"]) {
      await contractChat.getByRole("tab", { name: label, exact: true }).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, `Backend contract stays within mobile width: ${label}`);
    }
    if (process.env.LIVING_WORLD_BACKEND_DEBUG_MOBILE_SCREENSHOT) await page.screenshot({ path: process.env.LIVING_WORLD_BACKEND_DEBUG_MOBILE_SCREENSHOT, fullPage: true });
    await page.setViewportSize({ width: 1600, height: 1050 });
    const contractPlan = page.locator('.debug-round[data-turn="contract-plan"]');
    await contractPlan.locator(':scope > summary').click();
    await contractPlan.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    await contractPlan.getByRole("heading", { name: "正式日程采用结果", exact: true }).waitFor();
    await contractPlan.getByRole("heading", { name: "正式采用的数学课", exact: true }).waitFor();
    const contractUnsupported = page.locator('.debug-round[data-turn="contract-unsupported"]');
    await contractUnsupported.locator(':scope > summary').click();
    await contractUnsupported.getByRole("tab", { name: "② API 原始请求", exact: true }).click();
    await contractUnsupported.getByText("当前提供商的 HTTP 原文捕获尚未适配", { exact: true }).waitFor();
    assert.equal(await contractUnsupported.getByRole("button", { name: "下载请求原文", exact: true }).count(), 0);
    const contractLegacy = page.locator('.debug-round[data-turn="contract-legacy"]');
    await contractLegacy.locator(':scope > summary').click();
    await contractLegacy.getByText("旧版输入快照（非 API 原文）", { exact: true }).waitFor();
    await contractLegacy.getByRole("tab", { name: "④ 回复阅读版", exact: true }).click();
    await contractLegacy.getByText("旧版 reply 字段的返回正文", { exact: true }).waitFor();
    await page.evaluate(() => {
      window.fixture.life_days[0].full_request._debug_record_id = "contract-plan";
    });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.getByText("数据已刷新", { exact: true }).waitFor();
    await page.locator('a[data-view="schedule"]').click();
    await page.getByLabel("日程日期").fill(await page.evaluate(() => window.fixture.life_days[0].date));
    await page.getByRole("link", { name: "查看这次调用的 API 原文", exact: true }).click();
    assert.equal(await contractPlan.getAttribute("open"), "", "Formal day links directly to its generating model record");
    await page.evaluate(() => {
      window.fixture.debug_views = window.regularDebugFixture.views; window.fixture.debug_records = window.regularDebugFixture.records;
      delete window.fixture.life_days[0].full_request._debug_record_id;
      location.hash = "debug";
    });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await page.getByText("数据已刷新", { exact: true }).waitFor();
    await page.locator('a[data-view="data"]').click();
    await page.getByRole("button", { name: "恢复所选备份", exact: true }).click();
    await page.getByText("请先选择有效的 JSON 备份文件。", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "整理旧记忆", exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: "维护记忆", exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const view of ["overview", "settings", "drives", "schedule", "whitelist", "sources", "debug", "memory", "journal", "data"]) {
      await page.evaluate((target) => { location.hash = target; }, view);
      await page.locator(`a[data-view="${view}"][aria-current="page"]`).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, `No mobile horizontal overflow: ${view}`);
      if (view === "schedule" && process.env.LIVING_WORLD_SCHEDULE_MOBILE_SCREENSHOT) await page.locator("#content").screenshot({ path: process.env.LIVING_WORLD_SCHEDULE_MOBILE_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
      if (view === "debug") {
        const mobileRound = page.locator('.debug-round[data-turn="round-1"]');
        for (const label of ["② API 原始请求", "③ API 原始返回", "④ 回复阅读版", "① 上下文与信息来源"]) {
          await mobileRound.getByRole("tab", { name: label, exact: true }).click();
          if (label.startsWith("②") || label.startsWith("③")) {
            await mobileRound.getByRole("button", { name: "格式化显示", exact: true }).click();
            await mobileRound.getByRole("button", { name: "全部展开", exact: true }).click();
            assert.equal(await mobileRound.locator(".debug-raw").evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true, "Formatted bodies wrap inside the mobile reader");
            if (label.startsWith("②") && process.env.LIVING_WORLD_RAW_DEBUG_MOBILE_SCREENSHOT) await mobileRound.screenshot({ path: process.env.LIVING_WORLD_RAW_DEBUG_MOBILE_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
            if (label.startsWith("②")) {
              await foldToggle(firstMessage).click();
              assert.equal(await firstMessage.locator(":scope > .debug-json-children").isVisible(), false, "Mobile fold controls are clickable");
              if (process.env.LIVING_WORLD_JSON_FOLD_MOBILE_SCREENSHOT) await mobileRound.screenshot({ path: process.env.LIVING_WORLD_JSON_FOLD_MOBILE_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
              await mobileRound.getByRole("button", { name: "全部展开", exact: true }).click();
            }
          }
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, `No mobile tab overflow: ${label}`);
        }
        await assertSingleColumnSources();
        if (process.env.LIVING_WORLD_DEBUG_MOBILE_SCREENSHOT) await mobileRound.screenshot({ path: process.env.LIVING_WORLD_DEBUG_MOBILE_SCREENSHOT, style: "#notice { visibility: hidden !important; }" });
      }
    }
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; location.hash = "overview"; });
    await page.locator('a[data-view="overview"][aria-current="page"]').waitFor();
    assert.deepEqual(errors, [], "No browser runtime errors");
    process.stdout.write("UI smoke passed: program-owned meters, independent value/config saves, fractional values and costs, stage split/delete/boundaries and full coverage, draft-preserving refresh and tabs, pause controls, action statistics without daily caps, state home, detail/re-detail instruction and failure flow, decisions and detail history, outline-only edits, regeneration archives, old template viewer, whitelist UMO, fixed sources, independent digests, four debug views, single-column source disclosures, 14 JSON/SSE formatting cases, nested JSON folding, per-call and direction fold state, changed-body reset, keyboard/mobile controls, original-view toggle, exact JSON/SSE body downloads, paired calls, old snapshot labels, test/template separation, XSS, memory scopes, error feedback, import guard, responsive layout.\n");
  } finally { await browser.close(); }
})().catch((error) => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
