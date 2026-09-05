const bridge = window.AstrBotPluginPage;
const $ = (selector) => document.querySelector(selector);
const content = $("#content");
const titles = { overview: "生活概览", settings: "设定与模块", schedule: "日程与状态", memory: "记忆与人物", journal: "见闻与日记", social: "社交与调用", data: "数据管理" };
const moduleNames = { life: "日程生活", state: "角色状态", memory: "长期记忆", reply: "被动回复", interjection: "群聊插话", proactive: "主动社交", news: "新闻阅读", search: "主动搜索", weather: "天气", bilibili: "B 站见闻", journal: "生活日记", notes: "见闻笔记" };
const kindNames = { knowledge: "知识", event: "事件", skill: "技能", emotional: "情感与体会", profile: "人物画像", journal: "日记", note: "笔记", notes: "笔记", news: "新闻", search: "搜索", weather: "天气", bilibili: "B 站搜索", bilibili_watch: "观看 B 站视频", bilibili_recent: "读取 B 站历史见闻", fiction: "角色日常", life: "生活", social: "社交", read: "已读取", searched: "已搜索", watched: "已观看" };
const statusNames = { enabled: "已开启", disabled: "已关闭", ready: "就绪", running: "运行中", paused: "已暂停", unavailable: "不可用", error: "异常", failed: "失败", planned: "已计划", pending: "待执行", detailed: "已细化", completed: "已完成", done: "已完成", sent: "已发送", skipped: "已跳过", cancelled: "已取消", expired: "已过期", success: "成功", ok: "正常" };
let snapshot = null;
let busy = false;
let dirty = false;
let lastResult = null;
let dialogHandler = null;
let memorySearch = "";
let memoryKind = "";
let memoryScope = "";
let scheduleDate = "";
let recordSearch = "";
let selectedMemories = new Set();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function append(parent, ...children) {
  for (const child of children.flat()) if (child) parent.append(child);
  return parent;
}
function button(text, callback, style = "secondary", small = false) {
  const node = el("button", `button ${style}${small ? " small" : ""}`, text);
  node.type = "button";
  node.addEventListener("click", callback);
  return node;
}
function badge(value, forceStyle) {
  const good = ["enabled", "ready", "running", "completed", "done", "sent", "success", "ok"];
  const bad = ["failed", "error"];
  return el("span", `badge ${forceStyle || (good.includes(value) ? "good" : bad.includes(value) ? "bad" : "")}`, statusNames[value] || kindNames[value] || value || "未标记");
}
function heading(title, description, actions) {
  return append(el("div", "card-heading"), append(el("div"), el("h2", "", title), description ? el("p", "", description) : null), actions);
}
function card(title, description, body, actions) {
  return append(el("section", "card"), heading(title, description, actions), body);
}
function empty(title = "暂时没有记录", description = "生活发生之后，对应的记录会出现在这里。") {
  return append(el("div", "empty-state compact"), el("h2", "", title), el("p", "", description));
}
function details(value, title = "查看完整记录") {
  return append(el("details"), el("summary", "", title), el("pre", "", stringify(value)));
}
function stringify(value) {
  return typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2);
}
function rows(key) {
  const value = snapshot?.[key];
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.items)) return value.items;
  if (value && typeof value === "object") return Object.entries(value).map(([id, entry]) => typeof entry === "object" && entry !== null ? { id, ...entry } : { id, value: entry });
  return [];
}
function clone(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}
function valueAt(object, path, fallback = "") {
  return path.split(".").reduce((result, key) => result?.[key], object) ?? fallback;
}
function setAt(object, path, value) {
  const keys = path.split(".");
  if (keys.some((key) => ["__proto__", "constructor", "prototype"].includes(key))) return;
  let cursor = object;
  for (const key of keys.slice(0, -1)) {
    if (!cursor[key] || typeof cursor[key] !== "object" || Array.isArray(cursor[key])) cursor[key] = {};
    cursor = cursor[key];
  }
  cursor[keys.at(-1)] = value;
}
function stamp(value) {
  if (!value) return "";
  const parsed = new Date(typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
function dayNow() {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: valueAt(snapshot?.settings, "character.timezone", "Asia/Shanghai"), year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
    const part = (type) => parts.find((item) => item.type === type)?.value;
    return `${part("year")}-${part("month")}-${part("day")}`;
  } catch { return new Date().toISOString().slice(0, 10); }
}
function notice(text, isError = false) {
  const node = $("#notice");
  node.textContent = text;
  node.className = isError ? "error" : "";
  node.hidden = !text;
  node.setAttribute("role", isError ? "alert" : "status");
}
function setBusy(value) {
  busy = value;
  document.querySelectorAll("button").forEach((node) => { node.disabled = value; });
  content.setAttribute("aria-busy", String(value));
}
async function request(work, successText = "操作已完成") {
  if (busy) return false;
  setBusy(true);
  notice("正在处理，请稍候…");
  try {
    const result = await work();
    notice(typeof successText === "function" ? successText(result) : successText);
    return result ?? true;
  } catch (error) {
    notice(error?.message || String(error), true);
    return false;
  } finally { setBusy(false); }
}
async function readState() {
  const next = await bridge.apiGet("state");
  if (!next || typeof next !== "object") throw new Error("插件返回了无效状态，请查看 AstrBot 日志。");
  snapshot = next;
  $("#connection").textContent = "已连接 AstrBot";
  $("#version").textContent = `Living World ${snapshot.version || ""}`;
  $("#updated").textContent = `${new Date().toLocaleTimeString("zh-CN", { hour12: false })} 更新`;
}
async function refresh() {
  return request(async () => { await readState(); dirty = false; render(); }, "数据已刷新");
}
async function action(name, payload = {}) {
  return request(async () => {
    const result = await bridge.apiPost("action", { action: name, ...payload });
    lastResult = { action: name, result, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }) };
    await readState();
    render();
    return result ?? true;
  }, (result) => result?.status === "skipped" ? `本次未执行：${result.text || result.reason || "请查看操作结果"}` : result?.status === "failed" ? `本次执行失败：${result.text || result.error || result.reason || "请查看操作结果"}` : "操作已完成，请查看执行结果");
}
async function saveSettings(settings) {
  return request(async () => {
    await bridge.apiPost("settings", settings);
    dirty = false;
    await readState();
    render();
  }, "设置已保存并应用，已有记录继续保留");
}
function field(label, name, value, options = {}) {
  const wrapper = el("label", `field${options.wide ? " wide" : ""}`);
  append(wrapper, el("span", "field-label", label));
  const control = el(options.options ? "select" : options.type === "textarea" ? "textarea" : "input");
  control.name = name;
  if (options.options) {
    const choices = options.options.map((choice) => typeof choice === "string" ? { value: choice, label: choice } : choice);
    if (value && !choices.some((choice) => String(choice.value) === String(value))) choices.push({ value, label: `${value}（当前值）` });
    choices.forEach((choice) => {
      const option = el("option", "", choice.label);
      option.value = choice.value;
      control.append(option);
    });
  } else if (options.type !== "textarea") control.type = options.type || "text";
  control.value = value ?? "";
  for (const key of ["min", "max", "step", "placeholder", "required", "rows", "readOnly"]) if (options[key] !== undefined) control[key] = options[key];
  if (options.type === "number") control.dataset.number = "true";
  if (options.lines) control.dataset.lines = "true";
  control.addEventListener("input", () => { if (control.closest("#settings-form")) dirty = true; });
  append(wrapper, control, options.hint ? el("span", "field-hint", options.hint) : null);
  return wrapper;
}
function readFields(container) {
  const values = {};
  container.querySelectorAll("input[name],select[name],textarea[name]").forEach((input) => {
    if (input.disabled || input.dataset.skip) return;
    let value = input.type === "checkbox" ? input.checked : input.value;
    if (input.dataset.number) value = Number(value);
    if (input.dataset.lines) value = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    setAt(values, input.name, value);
  });
  return values;
}
function applyFields(target, container) {
  const values = readFields(container);
  function merge(destination, source) {
    for (const [key, value] of Object.entries(source)) {
      if (["__proto__", "constructor", "prototype"].includes(key)) continue;
      if (value && typeof value === "object" && !Array.isArray(value)) {
        if (!destination[key] || typeof destination[key] !== "object" || Array.isArray(destination[key])) destination[key] = {};
        merge(destination[key], value);
      } else destination[key] = value;
    }
  }
  merge(target, values);
  return target;
}
function openEditor(title, body, handler, submitLabel = "保存", danger = false) {
  $("#editor-title").textContent = title;
  $("#editor-body").replaceChildren(...(Array.isArray(body) ? body : [body]));
  $("#editor-submit").textContent = submitLabel;
  $("#editor-submit").className = `button ${danger ? "danger" : "primary"}`;
  dialogHandler = handler;
  $("#editor").showModal();
}
function confirmAction(title, text, handler, danger = false, submitLabel = "确认") {
  openEditor(title, el("p", danger ? "danger-copy" : "muted", text), handler, submitLabel, danger);
}
$("#editor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !dialogHandler) return;
  const result = await dialogHandler(readFields($("#editor-body")));
  if (result !== false) $("#editor").close();
});
$("#editor-close").addEventListener("click", () => $("#editor").close());
$("#editor-cancel").addEventListener("click", () => $("#editor").close());
$("#editor").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });

function scopeLabel(scope) {
  if (!scope || ["global", "public", "shared"].includes(scope)) return "公开 / 角色生活";
  const session = rows("sessions").find((row) => row.umo === scope);
  return session?.title || scope;
}
function scopeOptions() {
  const options = [{ value: "global", label: "公开 / 角色生活" }];
  const seen = new Set(["global"]);
  [...rows("sessions"), ...(snapshot?.settings?.sessions || []).filter((item) => typeof item === "object")].forEach((row) => {
    if (row.umo && !seen.has(row.umo)) { seen.add(row.umo); options.push({ value: row.umo, label: row.title || row.umo }); }
  });
  return options;
}
function recordTitle(record) {
  return record.title || record.name || record.reason || record.query || record.content?.slice?.(0, 100) || record.text?.slice?.(0, 100) || record.id || "记录";
}
function recordList(records, options = {}) {
  if (!records.length) return empty(options.emptyTitle, options.emptyDescription);
  const list = el("div", `list${options.timeline ? " timeline" : ""}`);
  records.forEach((record) => {
    const item = el("article", "record");
    const title = el("div", "record-title", recordTitle(record));
    const meta = el("div", "record-meta");
    if (record.status) meta.append(badge(record.status));
    if (record.kind || record.source || record.type) meta.append(badge(record.kind || record.source || record.type));
    if (record.scope) meta.append(el("span", "", scopeLabel(record.scope)));
    if (record.person_id) meta.append(el("span", "", `人物 ${record.person_id}`));
    const time = record.start_at || record.start || record.created_at || record.timestamp || record.date || record.day;
    if (time) meta.append(el("span", options.timeline ? "timeline-time" : "", stamp(time)));
    if (record.important || record.pinned) meta.append(badge("重要保留", "good"));
    if (record.profile) meta.append(badge("人物画像", "good"));
    append(item, append(el("div", "record-head"), title, options.head?.(record)), meta);
    const text = record.content || record.text || record.summary || record.description || record.error;
    if (text && text !== recordTitle(record)) item.append(el("p", "record-body", stringify(text)));
    const urls = [...new Set([record.url, record.source_url, ...(Array.isArray(record.sources) ? record.sources : [])])];
    urls.filter((url) => typeof url === "string" && /^https?:\/\//i.test(url)).forEach((url, index) => {
      const link = el("a", "muted", `来源 ${index + 1} ↗`);
      link.href = url; link.title = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.style.marginRight = "12px";
      item.append(link);
    });
    const actions = options.actions?.(record);
    if (actions) item.append(actions);
    item.append(details(record));
    list.append(item);
  });
  return list;
}
function modulePanel(editable = false) {
  const grid = el("div", "module-grid");
  const actual = rows("modules");
  const ids = [...new Set([...Object.keys(moduleNames), ...actual.map((module) => module.id)])];
  ids.forEach((id) => {
    const module = actual.find((row) => row.id === id) || {};
    const enabled = snapshot.settings?.modules?.[id] ?? module.enabled ?? false;
    const wrapper = el("div", "module");
    const label = el("label");
    append(label, el("span", "", moduleNames[id] || id));
    if (editable) {
      const control = el("input");
      control.type = "checkbox";
      control.name = `modules.${id}`;
      control.checked = enabled;
      control.addEventListener("change", () => { dirty = true; });
      label.append(control);
    } else label.append(badge(enabled ? module.status || "enabled" : "disabled"));
    append(wrapper, label, el("div", "muted", editable ? (statusNames[module.status] || module.status || (enabled ? "开启后参与生活" : "关闭后保留已有记录")) : id));
    if (module.error) wrapper.append(el("p", "", module.error));
    grid.append(wrapper);
  });
  return grid;
}

function renderOverview() {
  const root = el("div", "stack");
  const state = snapshot.state || {};
  const activities = rows("activities");
  const current = activities.find((row) => row.status === "running") || activities.find((row) => ["planned", "pending", "detailed"].includes(row.status));
  const persona = rows("personas").find((row) => row.id === snapshot.settings?.persona_id);
  append(root, append(el("section", "hero"), append(el("div"), el("p", "eyebrow", `${dayNow()} · ${persona?.name || snapshot.settings?.persona_id || "等待绑定人格"}`), el("h2", "", current ? recordTitle(current) : "从一个平常的日子开始"), el("p", "", state.summary || state.activity || "日程、见闻和交谈在这里相遇。先绑定人格、设置会话，再开启你希望角色拥有的能力。")), el("span", "hero-decoration", "✳")));
  const metrics = el("div", "metrics");
  [["日程活动", activities.length, "计划与执行分别记录"], ["长期记忆", rows("memories").length, "保留每段经历的场合"], ["外部见闻", rows("observations").length, "新闻 · 搜索 · 天气 · B 站"], ["主动发送", rows("deliveries").filter((row) => ["sent", "success", "completed"].includes(row.status)).length, "当前记录中的成功发送"]].forEach(([label, value, hint]) => append(metrics, append(el("div", "metric"), el("div", "metric-label", label), el("div", "metric-value", value), el("div", "metric-foot", hint))));
  root.append(metrics);
  root.append(card("能力运行状态", "可分别开关；关闭能力后，已有生活与记忆仍然保留。", modulePanel(), button("管理能力", () => { location.hash = "settings"; }, "secondary", true)));
  const diagnostics = rows("diagnostics");
  const diagnosticBody = diagnostics.length ? recordList(diagnostics.map((row) => typeof row === "string" ? { title: row } : row)) : empty("没有待处理诊断", "缺失依赖、会话配置和运行错误会在这里显示。");
  append(root, append(el("div", "grid"), card("最近发生", "实际执行情况与生活事件", recordList(rows("events").slice(0, 5))), card("连接与诊断", "优先查看不可用能力的原因", diagnosticBody)));
  return root;
}

function renderSettings() {
  const form = el("form", "stack");
  form.id = "settings-form";
  const settings = snapshot.settings || {};
  const f = (label, path, options = {}, fallback = "") => field(label, path, valueAt(settings, path, fallback), options);
  const personas = [{ value: "", label: "请选择绑定人格" }, ...rows("personas").map((row) => ({ value: row.id, label: row.name || row.id }))];
  const providers = [{ value: "", label: "沿用默认模型" }, ...rows("providers").map((row) => ({ value: row.id, label: row.name || row.id }))];
  const saveButton = button("保存全部设置", () => form.requestSubmit(), "primary");
  append(form, append(el("div", "section-toolbar"), el("p", "muted", "修改后点击保存，即刻应用能力开关与配置。核心人格由 AstrBot 管理。"), saveButton));
  form.append(card("角色与世界", "使用指定 AstrBot 人格，补充角色资料与生活背景。", append(el("div", "form-grid"), f("绑定人格", "persona_id", { options: personas, required: true }), f("时区", "character.timezone", { placeholder: "Asia/Shanghai" }, "Asia/Shanghai"), f("角色补充资料", "character.profile", { type: "textarea", hint: "身份、兴趣、作息偏好。不会替换 AstrBot 核心人格。" }), f("世界设定", "character.world", { type: "textarea", hint: "例如学校、课程、居住环境与日常活动。" }), f("初始精力", "character.energy", { type: "number", min: 0, max: 100 }, 80), f("初始情绪", "character.mood", {}, "平静"))));
  form.append(card("业务能力", "每项能力独立开关；停用保留数据。", modulePanel(true)));
  const models = el("div", "form-grid");
  [["默认模型", "default"], ["生活与日程", "life"], ["记忆提炼", "memory"], ["主动社交", "social"], ["外部见闻", "exploration"], ["日记与笔记", "journal"]].forEach(([label, key]) => models.append(f(label, `models.${key}`, { options: providers })));
  form.append(card("模型分配", "空白模块继承默认模型；默认模型空白时沿用宿主配置。", models));
  const sessionContainer = el("div", "session-table");
  const sessionRows = [];
  const addSession = (original = { umo: "", enabled: true, weight: 1 }) => {
    const row = el("div", "session-row");
    const check = el("input"); check.type = "checkbox"; check.checked = original.enabled !== false; check.setAttribute("aria-label", "启用此会话");
    const umo = el("input"); umo.value = original.umo || ""; umo.placeholder = "平台:GroupMessage:群号 或 FriendMessage"; umo.required = true; umo.setAttribute("aria-label", "会话 UMO"); umo.setAttribute("list", "known-sessions");
    const weight = el("input"); weight.type = "number"; weight.min = "0"; weight.step = "0.1"; weight.value = original.weight ?? 1; weight.setAttribute("aria-label", "抽选权重");
    const entry = { original, row, check, umo, weight };
    const remove = button("×", () => { row.remove(); sessionRows.splice(sessionRows.indexOf(entry), 1); dirty = true; }, "secondary", true); remove.setAttribute("aria-label", "移除此会话");
    [check, umo, weight].forEach((control) => control.addEventListener("input", () => { dirty = true; }));
    append(row, check, umo, weight, remove); sessionRows.push(entry); sessionContainer.append(row);
  };
  (Array.isArray(settings.sessions) ? settings.sessions : []).forEach((session) => addSession(typeof session === "string" ? { umo: session, enabled: true, weight: 1 } : session));
  const dataList = el("datalist"); dataList.id = "known-sessions";
  rows("sessions").forEach((session) => { const option = el("option", "", `${session.title || ""} ${session.persona_id || ""}`); option.value = session.umo; dataList.append(option); });
  const sessionBody = append(el("div", "stack"), sessionContainer, dataList, append(el("div", "actions"), button("＋ 添加会话", () => { addSession(); dirty = true; }, "secondary", true)), el("p", "hint", "会话需使用绑定人格。权重表示抽中概率，数值越大机会越多；抽选数量与发送限制在下方设置。输入 UMO 时可选择宿主已知会话。"));
  form.append(card("接入会话与主动白名单", "从左到右：启用、会话 UMO、抽选权重。", sessionBody));
  form.append(card("节奏与主动社交", "日程社交、临时调用与生活分享共用发送限制。", append(el("div", "form-grid"), f("每次抽选对象数", "social.target_count", { type: "number", min: 1, max: 20 }, 1), f("同对象冷却（分钟）", "social.cooldown_minutes", { type: "number", min: 0, max: 10080 }, 60), f("每日发送上限", "social.daily_limit", { type: "number", min: 0, max: 1000 }, 5), f("群聊插话间隔（分钟）", "social.interjection_interval_minutes", { type: "number", min: 1, max: 1440 }, 30), f("免打扰开始", "social.quiet_start", { type: "time" }, "23:00"), f("免打扰结束", "social.quiet_end", { type: "time" }, "08:00"), f("生活检查间隔（秒）", "life.tick_seconds", { type: "number", min: 10, max: 3600 }, 60), f("提前细化活动（分钟）", "life.detail_minutes", { type: "number", min: 0, max: 120 }, 10))));
  form.append(card("外部见闻来源", "来源未配置或依赖不可用时，模块会报告原因。", append(el("div", "form-grid"), f("新闻 RSS / Atom 地址", "news.feeds", { type: "textarea", lines: true, wide: true, hint: "每行一个完整地址。" }, []), f("单次新闻数量", "news.limit", { type: "number", min: 1, max: 30 }, 5), f("天气地点", "weather.location", { placeholder: "例如：北京" }), f("天气来源地址", "weather.url", { type: "url", wide: true, placeholder: "https://…", hint: "使用可配置天气接口，格式要求见插件配置说明。" }), f("搜索工具名称", "search.tool_name", { hint: "填写 AstrBot 已注册工具的名称。" }), f("搜索关键词参数名", "search.query_argument", {}, "query"), f("B 站依赖插件", "bilibili.plugin_name", {}, "astrbot_plugin_bilibili_ai_bot"), f("近期 B 站见闻数量", "bilibili.recent_limit", { type: "number", min: 1, max: 30 }, 5), el("p", "hint wide", "B 站依赖插件只调用搜索、观看与见闻读取，不启用完整自主浏览、点赞或评论。"))));
  const feeds = form.querySelector('[name="news.feeds"]');
  if (Array.isArray(settings.news?.feeds)) feeds.value = settings.news.feeds.join("\n");
  append(form, append(el("div", "form-actions"), button("保存全部设置", () => form.requestSubmit(), "primary")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const next = applyFields(clone(settings), form);
    next.sessions = sessionRows.map((row) => ({ ...row.original, umo: row.umo.value.trim(), enabled: row.check.checked, weight: Number(row.weight.value) }));
    if (new Set(next.sessions.map((row) => row.umo)).size !== next.sessions.length) { notice("会话 UMO 不能重复，请合并重复白名单。", true); return; }
    await saveSettings(next);
  });
  return form;
}

function editActivity(record) {
  openEditor("编辑日程活动", [
    field("标题", "title", record.title || "", { required: true }),
    field("开始时间", "start", record.start || record.start_at || "", { required: true, hint: "ISO 时间，例如 2026-09-05T09:00:00+08:00。" }),
    field("结束时间", "end", record.end || record.end_at || "", { required: true, hint: "填写 ISO 时间；活动应当在此时间前结束。" }),
    field("活动内容", "description", record.description || record.content || "", { type: "textarea", required: true }),
    field("行为类型", "kind", record.kind || "fiction", { options: ["fiction", "social", "news", "search", "weather", "bilibili"].map((kind) => ({ value: kind, label: kindNames[kind] })) }),
    el("p", "hint", `所属场合：${scopeLabel(record.scope)}。真实行为执行结果由系统记录，编辑不会发送消息。`),
  ], (patch) => action("update_activity", { id: record.id, patch }));
}
function renderSchedule() {
  const root = el("div", "stack");
  const stateBody = append(el("div", "stack"), append(el("div", "split-metric"), el("span", "", `精力：${snapshot.state?.energy ?? "—"}`), el("span", "", `情绪：${snapshot.state?.mood ?? "—"}`)), el("p", "state-summary", snapshot.state?.routine || "暂未设置当前作息。"), details(snapshot.state || {}, "查看状态数据"));
  root.append(card("此刻的状态", "轻微影响活动与表达，不改变核心人格。", stateBody, button("调整状态", () => openEditor("调整当前状态", [field("精力", "energy", snapshot.state?.energy ?? 80, { type: "number", min: 0, max: 100 }), field("情绪", "mood", snapshot.state?.mood || "平静"), field("当前作息", "routine", snapshot.state?.routine || "", { type: "textarea" })], (patch) => action("update_state", { patch })), "secondary", true)));
  const date = el("input"); date.type = "date"; date.value = scheduleDate || dayNow(); date.setAttribute("aria-label", "日程日期"); date.style.width = "auto";
  date.addEventListener("change", () => { scheduleDate = date.value; render(); });
  const toolbar = append(el("div", "section-toolbar"), date, append(el("div", "actions"), button("生成当天大纲", () => confirmAction("生成日程大纲", "根据人格、生活状态和相关记忆调用模型生成当天大纲。已有日程会继续保留，不会反复生成。", () => action("plan_day", { date: date.value }), false, "生成大纲"), "primary")));
  const selectedDay = date.value;
  const activities = rows("activities").filter((row) => {
    const time = row.start_at || row.start || row.date;
    return !time || String(time).startsWith(selectedDay);
  });
  const list = recordList(activities, { timeline: true, emptyTitle: "这一天还没有安排", emptyDescription: "生成每日大纲后，可以调整尚未执行的活动。", actions: (record) => record.status === "planned" ? append(el("div", "actions"), button("编辑", () => editActivity(record), "secondary", true), !record.detailed ? button("细化活动", () => action("detail_activity", { id: record.id }), "secondary", true) : null) : null });
  root.append(card("日程安排", "计划、角色生活与实际行动结果分别记录；不会把计划中的聊天记成已经发生。", append(el("div", "stack"), toolbar, list)));
  return root;
}

function editMemory(record = {}) {
  const important = append(el("label", "inline-check"), el("input"), el("span", "", "重要记忆，长期保留"));
  important.firstChild.type = "checkbox"; important.firstChild.name = "important"; important.firstChild.checked = Boolean(record.important || record.pinned);
  openEditor(record.id ? "编辑记忆" : "添加记忆", [
    field("内容", "content", record.content || record.text || "", { type: "textarea", required: true, readOnly: Boolean(record.profile && record.scope === "global"), hint: record.profile && record.scope === "global" ? "此人物画像由已核对的谈话证据形成，内容保持只读，可调整保留标记或删除。" : "" }),
    field("分类", "kind", record.kind || "event", { options: ["knowledge", "event", "skill", "emotional"].map((kind) => ({ value: kind, label: kindNames[kind] })) }),
    record.id ? el("p", "hint", `所属场合：${scopeLabel(record.scope)}；人物：${record.person_id || "角色自身 / 未关联"}。编辑保留原有场合与人物。`) : field("所属场合", "scope", "global", { options: scopeOptions(), hint: "私人谈话、经历与约定请选择对应会话。" }),
    record.id ? el("span") : field("人物标识", "person_id", "", { hint: "留空表示角色自身或不关联人物。" }), important,
  ], (patch) => {
    if (!record.id) return action("remember", patch);
    const { content: text, ...rest } = patch;
    return action("update_memory", { id: record.id, patch: { ...rest, text } });
  });
}
function renderMemory() {
  const root = el("div", "stack");
  const search = el("input"); search.type = "search"; search.placeholder = "搜索内容、人物或场合"; search.value = memorySearch; search.setAttribute("aria-label", "搜索记忆");
  const kindSelect = field("分类", "filter-kind", memoryKind, { options: [{ value: "", label: "全部分类" }, ...Object.entries(kindNames).filter(([key]) => ["knowledge", "event", "skill", "emotional", "profile"].includes(key)).map(([value, label]) => ({ value, label }))] }).querySelector("select"); kindSelect.setAttribute("aria-label", "筛选记忆分类");
  const scopeSelect = field("场合", "filter-scope", memoryScope, { options: [{ value: "", label: "全部场合" }, ...scopeOptions()] }).querySelector("select"); scopeSelect.setAttribute("aria-label", "筛选记忆场合");
  const listContainer = el("div");
  function fill() {
    const filtered = rows("memories").filter((row) => (!memoryKind || (memoryKind === "profile" ? row.profile : row.kind === memoryKind)) && (!memoryScope || (row.scope || "global") === memoryScope) && stringify(row).toLocaleLowerCase().includes(memorySearch.toLocaleLowerCase()));
    listContainer.replaceChildren(recordList(filtered, {
      head: (record) => {
        const check = el("input"); check.type = "checkbox"; check.checked = selectedMemories.has(record.id); check.setAttribute("aria-label", `选择记忆 ${recordTitle(record)}`);
        check.addEventListener("change", () => { if (check.checked) selectedMemories.add(record.id); else selectedMemories.delete(record.id); }); return check;
      },
      actions: (record) => append(el("div", "actions"), button("编辑", () => editMemory(record), "secondary", true), button("删除", () => confirmAction("删除这条记忆", "删除后将不再参与检索。此操作不会删除其他记忆或 AstrBot 原始聊天。", () => action("delete_memory", { id: record.id }), true, "删除记忆"), "danger", true)),
    }));
  }
  search.addEventListener("input", () => { memorySearch = search.value; fill(); });
  kindSelect.addEventListener("change", () => { memoryKind = kindSelect.value; fill(); });
  scopeSelect.addEventListener("change", () => { memoryScope = scopeSelect.value; fill(); });
  const merge = () => {
    const chosen = rows("memories").filter((row) => selectedMemories.has(row.id));
    if (chosen.length < 2) { notice("请先勾选至少两条记忆。", true); return; }
    if (new Set(chosen.map((row) => row.scope || "global")).size > 1) { notice("只能合并同一场合的记忆，避免把私人内容带到其他场合。", true); return; }
    if (chosen.some((row) => row.profile && row.scope === "global")) { notice("全局人物画像来自可核对的谈话证据，不能用手工合并的内容替换。", true); return; }
    openEditor("合并选中的记忆", [el("p", "muted", `将 ${chosen.length} 条同场合记忆合并，请检查合并后的内容。`), field("合并内容", "content", chosen.map((row) => row.content || row.text || "").join("\n"), { type: "textarea", required: true })], async (values) => {
      const result = await action("merge_memories", { ids: chosen.map((row) => row.id), content: values.content });
      if (result !== false) selectedMemories.clear(); return result;
    }, "合并记忆");
  };
  fill();
  const actions = append(el("div", "actions"), button("维护记忆", () => action("maintain_memory"), "secondary", true), button("合并所选", merge, "secondary", true), button("添加记忆", () => editMemory(), "primary", true));
  root.append(card("记忆与人物认知", "认识同一个人，谈话分场合。画像、共同回忆与自身体会可按分类查看。", append(el("div", "stack"), append(el("div", "filterbar"), search, kindSelect, scopeSelect), listContainer), actions));
  root.append(el("p", "hint", "此页供管理员查看全部场合。角色实际检索仍按当前会话过滤；私人记忆衍生的日程、日记与笔记继续保留来源场合。"));
  return root;
}

function renderJournal() {
  const root = el("div", "stack");
  const form = el("form", "stack");
  append(form, append(el("div", "form-grid"), field("来源", "source", "news", { options: ["news", "search", "weather", "bilibili", "bilibili_watch", "bilibili_recent"].map((value) => ({ value, label: kindNames[value] })) }), field("关键词 / 视频 BV 号", "query", "", { placeholder: "搜索关键词；观看视频填写 BV 号；新闻与天气可留空" })), append(el("div", "form-actions"), button("执行一次探索", () => form.requestSubmit(), "primary")));
  form.addEventListener("submit", (event) => { event.preventDefault(); action("explore", readFields(form)); });
  root.append(card("收集一段新见闻", "手动调用指定来源，记录实际结果与来源。搜索结果不会被记录为已观看。", form));
  const generate = () => openEditor("生成日记或笔记", [field("日期", "date", dayNow(), { type: "date", required: true }), field("类型", "kind", "journal", { options: [{ value: "journal", label: "生活日记" }, { value: "note", label: "见闻笔记" }] }), field("所属场合", "scope", "global", { options: scopeOptions(), hint: "只使用该场合允许回顾的内容。" })], (values) => action("generate_journal", values), "生成");
  const entryActions = (record) => append(el("div", "actions"), button("删除", () => confirmAction("删除这篇记录", "此操作删除选中的日记或笔记，原始见闻和记忆继续保留。", () => action("delete_entry", { id: record.id }), true, "删除记录"), "danger", true));
  append(root, append(el("div", "grid"), card("外部见闻", "真实读取的内容及其来源", recordList(rows("observations"))), card("日记与见闻笔记", "从已有经历出发，保留来源场合", recordList(rows("entries"), { actions: entryActions }), button("生成记录", generate, "secondary", true))));
  return root;
}

function usageTable() {
  const records = rows("usage");
  if (!records.length) return empty("暂时没有调用记录", "模型调用后会显示使用模块、耗时和调用结果。");
  const table = el("table", "data-table");
  const head = el("thead"); const tr = el("tr");
  ["时间", "模块 / 模型", "结果", "用量 / 耗时"].forEach((value) => tr.append(el("th", "", value))); head.append(tr); table.append(head);
  const body = el("tbody");
  records.forEach((record) => {
    const row = el("tr");
    const usage = record.usage || record;
    const total = usage.total_tokens ?? usage.tokens;
    const tokenText = total !== undefined ? `${stringify(total)} tokens` : usage.input_tokens !== undefined || usage.output_tokens !== undefined ? `输入 ${usage.input_tokens ?? "—"} · 输出 ${usage.output_tokens ?? "—"}` : "—";
    append(row, el("td", "", stamp(record.created_at || record.timestamp)), append(el("td"), el("div", "", moduleNames[record.module] || record.module || "—"), el("small", "muted", record.provider_id || record.model || record.provider || usage.provider || "—")), append(el("td"), badge(record.status || (record.error ? "error" : "ok"))), append(el("td"), el("div", "", tokenText), record.duration_ms !== undefined ? el("small", "muted", `${record.duration_ms} ms`) : null, details(record)));
    body.append(row);
  });
  table.append(body);
  return append(el("div", "table-wrap"), table);
}
function renderSocial() {
  const root = el("div", "stack");
  const form = el("form", "stack");
  append(form, field("这次为什么想找人聊天？", "reason", "", { type: "textarea", required: true, placeholder: "例如：数学课有些无聊，想找个群聊聊最近的趣事。" }), el("p", "hint warning", "执行后会向已配置白名单中的抽选对象真实发送消息，遵守当前人格、频率与免打扰设置。"), append(el("div", "form-actions"), button("抽选对象并发送", () => form.requestSubmit(), "primary")));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = readFields(form);
    confirmAction("发起一次主动社交", "角色将按白名单权重随机选择对象，结合相应会话内容生成消息并实际发送。", () => action("social", values), false, "确认发送");
  });
  root.append(card("主动找人聊天", "日程中的社交、模型临时调用与此入口使用同一套规则。", form));
  const search = el("input"); search.type = "search"; search.value = recordSearch; search.placeholder = "筛选对象、消息或状态"; search.setAttribute("aria-label", "筛选社交记录");
  const deliveries = el("div");
  const fill = () => deliveries.replaceChildren(recordList(rows("deliveries").filter((record) => stringify(record).toLocaleLowerCase().includes(recordSearch.toLocaleLowerCase())).map((record) => ({ ...record, title: record.title || scopeLabel(record.umo || record.target || record.scope), content: record.content || record.message || record.text }))));
  search.addEventListener("input", () => { recordSearch = search.value; fill(); }); fill();
  root.append(card("主动消息记录", "消息发出只代表已发送，不代表对方已经回应。", append(el("div", "stack"), search, deliveries)));
  append(root, append(el("div", "grid"), card("行动与事件", "日程执行、跳过与失败原因", recordList(rows("events"))), card("模型调用", "检查模型、调用结果与用量", usageTable())));
  return root;
}

function renderData() {
  const root = el("div", "stack");
  const exportAction = () => request(async () => {
    const backup = await bridge.apiGet("export");
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = el("a"); link.href = url; link.download = `living-world-${dayNow()}.json`; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }, "备份已准备下载，请妥善保存");
  const file = el("input"); file.type = "file"; file.accept = ".json,application/json"; file.setAttribute("aria-label", "选择 Living World 备份文件");
  const preview = el("div");
  let imported = null;
  file.addEventListener("change", async () => {
    imported = null; preview.replaceChildren();
    const selected = file.files?.[0]; if (!selected) return;
    try {
      if (selected.size > 20 * 1024 * 1024) throw new Error("备份文件超过 20 MB，请检查文件是否正确。");
      imported = JSON.parse(await selected.text());
      if (!imported || typeof imported !== "object" || Array.isArray(imported)) throw new Error("备份必须是 JSON 对象。");
      preview.append(el("p", "hint", `已读取 ${selected.name} · ${(selected.size / 1024).toFixed(1)} KB`), details(imported, "预览备份内容"));
    } catch (error) { notice(`读取备份失败：${error.message}`, true); }
  });
  const importAction = () => {
    if (!imported) { notice("请先选择有效的 JSON 备份文件。", true); return; }
    confirmAction("恢复备份", "恢复备份中的配置，补入本地缺失的业务记录，保留本地已有记录与发送凭据。恢复后全部模块关闭，可检查配置后逐项开启。请先导出当前数据。", () => request(async () => {
      const result = await bridge.apiPost("import", imported); imported = null;
      lastResult = { action: "import", result, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }) };
      await readState(); dirty = false; render();
    }, "备份已恢复"), true, "恢复备份");
  };
  append(root, append(el("div", "grid"), card("导出备份", "保存配置、生活、记忆和运行记录，供迁移或恢复。", append(el("div", "stack"), el("p", "muted", "备份可能含私人记忆与角色资料，请保存在可信的位置。插件数据不包含 AstrBot 原始聊天历史。"), append(el("div", "actions"), button("下载 JSON 备份", exportAction, "primary")))), card("从备份恢复", "先选择文件并预览，再确认恢复。", append(el("div", "stack"), file, preview, append(el("div", "actions"), button("恢复所选备份", importAction, "secondary"))))));
  root.append(card("当前数据概况", "只读查看，不会触发探索或发送。", details({ version: snapshot.version, counts: Object.fromEntries(["activities", "memories", "observations", "entries", "events", "deliveries", "usage"].map((key) => [key, rows(key).length])), diagnostics: snapshot.diagnostics }, "查看数据统计与诊断")));
  return root;
}

function render() {
  const view = Object.hasOwn(titles, location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  $("#page-title").textContent = titles[view];
  document.title = `${titles[view]} · Living World`;
  document.querySelectorAll("#navigation a").forEach((link) => { if (link.dataset.view === view) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
  if (!snapshot) return;
  const renderers = { overview: renderOverview, settings: renderSettings, schedule: renderSchedule, memory: renderMemory, journal: renderJournal, social: renderSocial, data: renderData };
  content.replaceChildren(renderers[view]());
  if (lastResult && view !== "settings") content.firstChild.append(card("最近一次操作结果", `${lastResult.time} · ${lastResult.action}`, details(lastResult.result, "查看后端执行结果")));
  setBusy(busy);
}
window.addEventListener("hashchange", () => { dirty = false; render(); });
window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
$("#navigation").addEventListener("click", (event) => {
  const link = event.target.closest("a[data-view]");
  if (!link || !dirty) return;
  event.preventDefault();
  confirmAction("离开尚未保存的设置", "本页的修改尚未保存，离开后需要重新填写。", () => { dirty = false; location.hash = link.dataset.view; }, false, "放弃修改并离开");
});
$("#refresh").addEventListener("click", () => {
  if (dirty) confirmAction("刷新并放弃未保存的修改", "刷新会重新读取后端数据，覆盖当前未保存的设置。", refresh, false, "刷新");
  else refresh();
});
try {
  if (!bridge) throw new Error("请从 AstrBot 插件详情中的 Pages 打开此页面，以连接插件后端。");
  await bridge.ready();
  await refresh();
} catch (error) {
  $("#connection").textContent = "连接不可用";
  content.replaceChildren(empty("暂时无法连接", error.message));
  notice(error.message, true);
}
