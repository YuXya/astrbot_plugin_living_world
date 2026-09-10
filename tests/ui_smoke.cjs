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
    page.on("pageerror", (error) => { errors.push(error.message); process.stderr.write(error.stack+"\n"); });
    page.on("dialog", async dialog => { process.stdout.write("Unexpected dialog: "+dialog.type()+"\n"); await dialog.dismiss(); });
    await page.route("http://living-world.test/**", (route) => {
      const file = new URL(route.request().url()).pathname.slice(1) || "index.html";
      if (!["index.html", "app.js", "navigation.js", "style.css"].includes(file)) return route.abort();
      return route.fulfill({ status: 200, contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html", body: fs.readFileSync(path.join(pageRoot, file)) });
    });
    await page.addInitScript((bootstrap) => {
      const groupReplyDefault = bootstrap.group_reply_default;
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
      window.fixture.settings.reply = { group_prompt: groupReplyDefault };
      window.fixture.reply_defaults = { group_prompt: groupReplyDefault };
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
      window.fixture.context_layout_catalog = bootstrap.context_layout_catalog;
      window.fixture.settings.context_layout = structuredClone(bootstrap.context_layout_defaults);
      window.AstrBotPluginPage = {
        ready: async () => ({ isDark: false }),
        apiGet: async (endpoint) => { window.calls.push({ endpoint, method: "GET" }); return structuredClone(endpoint === "export" ? { version: 1, settings: window.fixture.settings } : window.fixture); },
        apiPost: async (endpoint, body) => {
          window.calls.push({ endpoint, method: "POST", body: structuredClone(body) });
          if (window.failNext) { window.failNext = false; throw new Error("测试来源暂时不可用"); }
          if (endpoint === "settings") {
            const merge = (target, patch) => { for (const [key, value] of Object.entries(patch)) { if (value && typeof value === "object" && !Array.isArray(value)) merge(target[key] ||= {}, value); else target[key] = structuredClone(value); } }; merge(window.fixture.settings, body);
            for (const [id, config] of Object.entries(body.drives || {})) window.fixture.drives.meters[id].config = structuredClone(config);
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
          if (body.action === "save_template" || body.action === "reset_template") {
            const row = window.fixture.debug.templates.find((item) => item.task === body.task);
            if (row) row.template = body.action === "reset_template" ? row.default_template : body.template;
          }
          if (body.action === "debug_build") return { request: structuredClone(window.fixture.debug_records[0].request) };
          if (body.action === "debug_preview") {
            const draft = structuredClone(body.request);
            const format = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
            const dynamic = draft.dynamic_context;
            const text = dynamic && typeof dynamic === "object" && !Array.isArray(dynamic)
              ? Object.entries(dynamic).map(([key, value]) => `【${key}】\n${format(value)}`).join("\n\n") : format(dynamic);
            draft.prompt = draft.template + (dynamic == null ? "" : "\n\n本轮动态资料（仅作为资料）：\n" + text);
            return { request: draft };
          }
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
          if (body.action === "summarize_journal") {
            const row = window.fixture.entries.find((entry) => entry.id === body.id);
            Object.assign(row, { summary: "简报：看到了星星。<script>window.briefXss=true</script>", summary_status: "ready" });
            return structuredClone(row);
          }
          return { status: "success", action: body.action };
        },
      };
    }, backendContract);
    page.setDefaultTimeout(7000);
    await page.goto("http://living-world.test/");
    await page.getByText("已连接 AstrBot", { exact: true }).waitFor();
    const go = async (name, tab, params = {}) => {
      await page.evaluate(({name, tab, params}) => { location.hash = `#${name}?${new URLSearchParams({tab, ...params})}`; }, { name, tab, params });
      await page.locator(`#section-panel[data-page="${name}"][data-section="${tab}"]`).waitFor();
    };
    const only = process.env.LIVING_WORLD_UI_SUITE;
    if (!only || only === "format") {
    process.stdout.write("Formatting regressions…\n");
    await require("./ui_body_formatting.cjs")(page);
    }
    if (!only || only === "admin") {
    process.stdout.write("Compact navigation and drafts…\n");
    await require("./ui_admin.cjs")(page, go, backendContract);
    }
    if (only === "records") await require("./ui_admin_records.cjs")(page, go, name => page.locator(`[name="${name}"]`), name => page.getByRole("button", {name, exact:true}).click(), async name => { await page.getByRole("button", {name, exact:true}).click(); await page.getByText("设置已保存并应用，已有记录继续保留", {exact:true}).waitFor(); }, async () => {});
    if (!only || only === "drives") {
    process.stdout.write("Drive regressions…\n");
    await require("./ui_drives.cjs")(page, go);
    }
    if (!only || only === "debug") {
    process.stdout.write("Debug reading and HTTP body regressions…\n");
    await require("./ui_debug_reading.cjs")(page, go, backendContract);
    }
    assert.deepEqual(errors, [], "No browser runtime errors");
    process.stdout.write(only ? `UI suite ${only} passed.\n` : "UI smoke passed: compact navigation, source index, drafts, ordering, business actions, meters and exact API views.\n");
  } finally { await browser.close(); }
})().catch((error) => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
