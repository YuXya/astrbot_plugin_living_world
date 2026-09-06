const bridge = window.AstrBotPluginPage;
const $ = (selector) => document.querySelector(selector);
const content = $("#content");
const titles = { whitelist: "聊天对象与白名单", sources: "内容来源", debug: "调试与调用记录", overview: "今日生活", settings: "角色、模型与模块", schedule: "日程与行动", memory: "记忆与人物", journal: "见闻与日记", social: "社交与调用", data: "数据管理" };
const moduleNames = { daily_digest: "AI 日报", debug: "调试记录", life: "日程生活", state: "角色状态", memory: "长期记忆", reply: "被动回复", interjection: "群聊插话", proactive: "主动社交", news: "新闻阅读", search: "主动搜索", weather: "天气", bilibili: "B 站见闻", journal: "生活日记", notes: "见闻笔记" };
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
let debugCategory = "";
let testRequest = "";
let debugTask = "life.plan";
const debugSelections = new Map();
const debugTaskNames = { "chat.turn": "聊天回复", "reply.model": "聊天回复", "reply.request": "聊天回复", "reply.tool": "聊天工具", "life.plan": "生成日程", "life.plan_day": "生成今日日程", "life.detail": "细化活动", "life.adjust": "调整日程", "news.select": "挑选新闻", "news.read": "阅读新闻", "search.topic": "选择搜索主题", "search.note": "搜索见闻笔记", "journal.write": "生成日记", "debug.test": "模型试跑", "test": "模型试跑" };

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
Object.assign(statusNames, { observed: "已观察", captured: "已捕获", generated: "已生成", partial: "部分完成", unknown: "结果未确认", interrupted: "已中断" });
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
  control.addEventListener("input", () => { if (control.closest("form[data-settings]")) dirty = true; });
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
    const item = el("article", `record${options.currentId === record.id ? " is-current" : ""}`);
    const title = el("div", "record-title", recordTitle(record));
    const meta = el("div", "record-meta");
    if (record.status) meta.append(badge(record.status));
    if (record.kind || record.source || record.type) meta.append(badge(record.kind || record.source || record.type));
    if (record.scope) meta.append(el("span", "", scopeLabel(record.scope)));
    if (record.person_id) meta.append(el("span", "", `人物 ${record.person_id}`));
    const time = record.start_at || record.start || record.created_at || record.timestamp || record.date || record.day;
    if (time) meta.append(el("span", options.timeline ? "timeline-time" : "", options.timeline && record.end ? `${clockTime(time)} — ${clockTime(record.end)}` : stamp(time)));
    if (options.currentId === record.id) meta.append(badge("当前活动", "good"));
    if (record.important || record.pinned) meta.append(badge("重要保留", "good"));
    if (record.profile) meta.append(badge("人物画像", "good"));
    append(item, append(el("div", "record-head"), title, options.head?.(record)), meta);
    const text = record.content || record.text || record.summary || record.description || record.error;
    if (text && text !== recordTitle(record)) item.append(el("p", "record-body", stringify(text)));
    const urls = [...new Set([record.url, record.source_url, ...(Array.isArray(record.sources) ? record.sources.map((source) => typeof source === "string" ? source : source.url) : [])])];
    urls.filter((url) => typeof url === "string" && /^https?:\/\//i.test(url)).forEach((url, index) => {
      const link = el("a", "muted", `来源 ${index + 1} ↗`);
      link.href = url; link.title = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.style.marginRight = "12px";
      item.append(link);
    });
    if (record.actions) item.append(actionBadges(record));
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

function linkButton(label, view) {
  const node = el("a", "button secondary small", label); node.href = `#${view}`; return node;
}
function checkField(label, name, enabled) {
  const control = el("input"); control.type = "checkbox"; control.name = name; control.checked = Boolean(enabled);
  control.addEventListener("change", () => { if (control.closest("form[data-settings]")) dirty = true; });
  return append(el("label", "inline-check"), control, el("span", "", label));
}
function settingsForm(label, description) {
  const form = el("form", "stack"); form.dataset.settings = "true";
  form.append(append(el("div", "section-toolbar"), el("p", "muted", description), button(label, () => form.requestSubmit(), "primary")));
  form.addEventListener("input", () => { dirty = true; });
  return form;
}
function orderedActivities(day = dayNow()) {
  return rows("activities").filter((item) => (item.date || item.start?.slice(0, 10)) === day).sort((a, b) => String(a.start).localeCompare(String(b.start)));
}
function currentActivity() {
  const now = Date.now();
  return orderedActivities().find((item) => new Date(item.start).getTime() <= now && new Date(item.end).getTime() > now && !["cancelled", "skipped"].includes(item.status));
}
function clockTime(value) {
  if (!value) return "未定";
  if (/^\d\d:\d\d$/.test(value)) return value;
  try { return new Date(value).toLocaleTimeString("zh-CN", { timeZone: valueAt(snapshot.settings, "character.timezone", "Asia/Shanghai"), hour: "2-digit", minute: "2-digit", hour12: false }); } catch { return String(value); }
}
function actionBadges(activity) {
  const box = el("div", "action-badges");
  for (const [name, label] of [["news", "新闻"], ["search", "搜索"], ["social", "主动聊天"]]) {
    const item = activity.actions?.[name]; if (!item?.enabled) continue;
    const execution = item.execution || {};
    const result = badge(`${label} ${clockTime(item.at || activity.start)} · ${statusNames[execution.status] || execution.status || "计划机会"}`, execution.status === "succeeded" || execution.status === "sent" ? "good" : "");
    result.title = [item.intent || item.reason, execution.reason || execution.result?.reason].filter(Boolean).join("；");
    box.append(result);
    if (execution.reason) box.append(el("span", "muted", `${label}：${execution.reason}`));
  }
  return box;
}
function observationList(records, limit) {
  if (!records.length) return empty("还没有实际见闻", "按日程阅读和搜索后，事实、感想及出处会分别显示。");
  const box = el("div", "list observation-list");
  records.slice(0, limit ?? records.length).forEach((record) => {
    const item = el("article", "record");
    append(item, append(el("div", "record-meta"), badge(record.kind || record.module || record.source), el("span", "", stamp(record.created_at))), el("h3", "observation-title", record.title || record.query || recordTitle(record)));
    if (record.selection_reason || record.reason) item.append(el("p", "muted", `为什么关注：${record.selection_reason || record.reason}`));
    if (record.factual_summary) item.append(el("p", "record-body", record.factual_summary));
    if (record.impression) item.append(append(el("div", "impression"), el("span", "eyebrow", "角色感想"), el("p", "", record.impression)));
    if (!record.factual_summary && !record.impression) item.append(el("p", "record-body", record.text || record.raw_text || ""));
    if (record.reading_basis) item.append(el("p", "muted", `阅读依据：${typeof record.reading_basis === "string" ? record.reading_basis : stringify(record.reading_basis)}`));
    const sources = [...new Set([record.url, ...(record.sources || []).map((source) => typeof source === "string" ? source : source.url)].filter(Boolean))];
    for (const url of sources) if (/^https?:\/\//i.test(url)) {
      const link = el("a", "source-link", url); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link);
    }
    item.append(details(record, "原始见闻与来源")); box.append(item);
  });
  return box;
}
function observationKind(item, kind) { return item.module === kind || item.source === kind || item.kind === kind; }
function renderOverview() {
  const root = el("div", "stack");
  const state = snapshot.state || {};
  const current = currentActivity();
  const today = orderedActivities();
  const persona = rows("personas").find((row) => row.id === snapshot.settings?.persona_id);
  const weather = rows("observations").find((item) => observationKind(item, "weather"));
  const next = today.flatMap((item) => item.actions?.social?.enabled ? [{ activity: item, action: item.actions.social }] : []).filter((item) => (!item.action.execution?.status || item.action.execution.status === "pending") && new Date(item.action.at || item.activity.start).getTime() >= Date.now()).sort((a, b) => String(a.action.at || a.activity.start).localeCompare(String(b.action.at || b.activity.start)))[0];
  root.append(append(el("div", "section-toolbar"), el("p", "muted", `${dayNow()} · ${persona?.name || snapshot.settings?.persona_id || "尚未绑定人格"} · 管理员视图`), append(el("div", "actions"), linkButton("聊天对象与白名单", "whitelist"), linkButton("日程生成参数", "schedule"))));
  const stateGrid = el("div", "state-grid");
  const sleep = current?.sleep_state || state.sleep_state || "未知";
  const sleepLabels = { awake: "清醒", asleep: "睡眠中", sleeping: "睡眠中", unknown: "未知" };
  for (const [label, value] of [["心情", state.mood || "未知"], ["精力", state.energy === undefined ? "未知" : `${state.energy} / 100`], ["地点", current?.location || state.location || "未知"], ["睡眠", sleepLabels[sleep] || sleep], ["天气", weather?.factual_summary || weather?.text || "尚未取得天气"], ["今日活动", `${today.length} 个活动`]]) stateGrid.append(append(el("div", "state-tile"), el("span", "", label), el("strong", "", value)));
  const currentBody = append(el("div", "current-activity"), el("p", "eyebrow", current ? `${clockTime(current.start)} — ${clockTime(current.end)} · 当前活动` : "当前没有进行中的活动"), el("h2", "", current?.title || "等待下一段生活"), el("p", "", current?.description || current?.content || "当天日程生成后，活动将在自己的时间开始。"), current ? actionBadges(current) : null);
  const nextBody = append(el("div", "stack"), el("div", "next-social-time", next ? clockTime(next.action.at || next.activity.start) : "暂无待执行联系"), el("p", "record-body", next ? next.action.intent || next.activity.title : "主动聊天由日程中的聊天标记安排。"), el("p", "hint", "计划机会不保证发送；对象按白名单权重抽选，并检查冷却、免打扰与每日上限。"), linkButton("查看聊天对象与白名单", "whitelist"));
  const timeline = recordList(today, { timeline: true, currentId: current?.id, emptyTitle: "今天还没有正式日程" });
  timeline.classList.add("home-timeline");
  append(root, append(el("div", "overview-grid"), append(el("div", "stack"), card("此刻的角色", "状态来自设定与日程，没有依据时显示未知。", stateGrid), card("现在在做什么", "角色日常与真实行动分别记录。", currentBody)), card("今日时间线", "新闻 → 搜索 → 聊天；同一活动可以安排多种行动。", timeline, linkButton("完整日程", "schedule")), card("下一次主动联系", "内容到执行时结合聊天场合生成。", nextBody)));
  append(root, append(el("div", "grid"), card("新闻阅读与感想", "真实内容、角色感想与阅读依据分别保留。", observationList(rows("observations").filter((item) => observationKind(item, "news") || observationKind(item, "daily_digest")), 3), linkButton("阅读历史", "journal")), card("搜索发现", "从活动和记忆出发，保留查询与来源。", observationList(rows("observations").filter((item) => observationKind(item, "search")), 3), linkButton("搜索历史", "journal"))));
  const diagnostics = append(el("details", "card"), el("summary", "", "运行能力与诊断"), modulePanel(), recordList(rows("diagnostics").map((row) => typeof row === "string" ? { title: row } : row), { emptyTitle: "没有待处理诊断" }));
  root.append(diagnostics);
  return root;
}
function renderSettings() {
  const form = settingsForm("保存全部设置", "保存会更新角色、模型和模块开关；不会发起模型调用或发送 QQ 消息。"); form.id = "settings-form";
  const settings = snapshot.settings || {};
  const f = (label, path, options = {}, fallback = "") => field(label, path, valueAt(settings, path, fallback), options);
  const personas = [{ value: "", label: "请选择绑定人格" }, ...rows("personas").map((row) => ({ value: row.id, label: row.name || row.id }))];
  const providers = [{ value: "", label: "沿用默认模型" }, ...rows("providers").map((row) => ({ value: row.id, label: row.name || row.id }))];
  form.append(card("角色与世界", "使用指定 AstrBot 人格，补充角色资料与生活背景。", append(el("div", "form-grid"), f("绑定人格", "persona_id", { options: personas, required: true }), f("时区", "character.timezone", {}, "Asia/Shanghai"), f("角色补充资料", "character.profile", { type: "textarea" }), f("世界设定", "character.world", { type: "textarea" }), f("初始精力", "character.energy", { type: "number", min: 0, max: 100 }, 70), f("初始情绪", "character.mood", {}, "平静"), f("初始地点", "character.location", { hint: "没有当前日程地点时采用此设置，留空显示未知。" }), f("初始睡眠状态", "character.sleep_state", { options: ["未知", "清醒", "睡眠"] }, "未知"))));
  form.append(card("业务能力", "关闭停止该项行为并保留已有数据；开启后按日程和模块规则运行。", modulePanel(true)));
  const models = el("div", "form-grid");
  [["默认模型", "default"], ["生活与日程", "life"], ["记忆提炼", "memory"], ["主动社交", "social"], ["外部见闻", "exploration"], ["日记与笔记", "journal"]].forEach(([label, key]) => models.append(f(label, `models.${key}`, { options: providers })));
  form.append(card("模型分配", "模块留空继承默认模型；默认模型留空沿用宿主配置。", models));
  form.append(card("其他设置入口", "日程配额、聊天白名单和内容来源在各自页面管理。", append(el("div", "actions"), linkButton("日程生成参数", "schedule"), linkButton("聊天对象与白名单", "whitelist"), linkButton("内容来源", "sources"))));
  form.addEventListener("submit", (event) => { event.preventDefault(); saveSettings(applyFields(clone(settings), form)); });
  return form;
}
function renderWhitelist() {
  const settings = snapshot.settings || {};
  const form = settingsForm("保存聊天对象与限制", "这是填写白名单的位置。保存只改变配置；日程执行时才可能发送消息。");
  const holder = el("div", "stack"); const entries = [];
  const platforms = rows("platforms").length ? rows("platforms") : snapshot.catalogs?.platforms || [];
  const knownIds = [...new Set([...platforms.map((item) => item.id), ...(settings.sessions || []).map((item) => (typeof item === "string" ? item : item.umo || "").split(":")[0])].filter(Boolean))];
  const add = (original = {}) => {
    const originalUMO = typeof original === "string" ? original : original.umo || "";
    original = typeof original === "string" ? { umo: original } : clone(original);
    const [connection = "", type = "GroupMessage", number = ""] = originalUMO.split(":");
    const box = el("div", "whitelist-entry"); const numberValue = number.split("_").at(-1);
    const connectionField = field("QQ 连接", "connection", connection || knownIds[0] || "", { options: [{ value: "", label: "选择 QQ 连接" }, ...knownIds.map((id) => ({ value: id, label: platforms.find((item) => item.id === id)?.name || id }))], required: true });
    const typeField = field("聊天类型", "type", type, { options: [{ value: "GroupMessage", label: "群聊" }, { value: "FriendMessage", label: "私聊" }] });
    const numberField = field("群号 / QQ 号", "number", numberValue, { required: true, placeholder: "只填写数字" });
    const weightField = field("抽选权重", "weight", original.weight ?? 1, { type: "number", min: 0, step: 0.1 });
    const enabledField = checkField("启用此对象", "enabled", original.enabled !== false);
    const preview = el("p", "muted");
    const sessionInfo = el("div", "stack session-status");
    const showStatus = (value) => {
      if (!value) { sessionInfo.replaceChildren(el("p", "hint", "尚未检查会话。没有历史也可以接入；检查只读，不调用模型或发送消息。")); return; }
      const historyLabel = value.history_status === "found" ? `已找到历史：${value.history_count} 条` : value.history_status === "error" ? "历史读取失败" : "首次对话／暂无历史";
      const source = { host_default: "AstrBot 默认设置", conversation: "当前对话指定", session_rule: "宿主会话规则", unresolved: "尚未解析" }[value.persona_source] || value.persona_source || "未知";
      const contextStatus = value.context_status || {};
      const attempt = contextStatus.last_attempt;
      const injected = contextStatus.last_injected;
      sessionInfo.replaceChildren(append(el("div", "actions"), badge(historyLabel, value.history_status === "error" ? "bad" : ""), badge(value.allowed ? "配置允许接入" : "配置未通过", value.allowed ? "good" : "bad")),
        el("p", "hint", `真实会话：${value.actual_scope || "未知"} · 连接类型：${value.platform_name || "未知"}`),
        el("p", "hint", `实际人格：${value.persona_id || "未解析"} · 插件绑定：${value.bound_persona || settings.persona_id || "未设置"} · 人格来源：${source}`),
        el("p", "hint", `${value.reason || ""}${value.reason_code ? `（${value.reason_code}）` : ""}`),
        el("p", "hint", `历史来源：${value.history_source || "未知"} · 对话：${value.conversation_id || "暂无"} · ${stamp(value.checked_at)}`));
      if (value.history_error) sessionInfo.append(el("p", "danger-copy", value.history_error));
      sessionInfo.append(el("p", "hint", attempt ? `最近处理：${stamp(attempt.at)} · ${attempt.reason}` : "尚未观察到实际聊天接入；配置检查通过不代表已经注入上下文。"));
      if (injected) sessionInfo.append(el("p", "hint", `最近实际注入：${stamp(injected.at)} · ${injected.reason}`));
      const turn = attempt?.turn_id || injected?.turn_id;
      if (turn) {
        sessionInfo.append(el("p", "hint", `对应聊天轮次：${turn}`));
        if (rows("debug_records").some((row) => row.turn_id === turn)) {
          const link = linkButton("查看这轮接入记录", `debug?turn=${encodeURIComponent(turn)}`);
          link.addEventListener("click", () => { debugCategory = ""; });
          sessionInfo.append(link);
        }
        else sessionInfo.append(el("p", "hint", "该轮调试记录已清理、超出保留数量或当时未开启调试；接入状态仍保留。"));
      }
    };
    const controls = append(el("div", "form-grid whitelist-fields"), connectionField, typeField, numberField, weightField);
    const entry = { original, originalUMO, connection, type, numberValue, box, controls, enabledField }; entries.push(entry);
    const currentScope = () => { const v = readFields(controls); return v.connection === connection && v.type === type && v.number === numberValue && originalUMO ? originalUMO : `${v.connection}:${v.type}:${v.number.trim()}`; };
    const update = () => { preview.textContent = `会话标识：${currentScope()} · 自动填写，无需手工拼接`; showStatus(rows("session_status").find((row) => row.umo === currentScope())); };
    controls.addEventListener("input", update); update();
    append(box, controls, append(el("div", "section-toolbar"), enabledField, button("移除这个对象", () => { entries.splice(entries.indexOf(entry), 1); box.remove(); dirty = true; }, "danger", true)), preview);
    box.append(sessionInfo, button("检查会话与历史", async () => {
      const result = await request(() => bridge.apiPost("action", { action: "inspect_session", scope: currentScope() }), "会话检查完成；没有调用模型或发送消息");
      if (result !== false) {
        snapshot.session_status = [...rows("session_status").filter((row) => row.umo !== result.umo), clone(result)];
        showStatus(result);
      }
    }, "secondary", true));
    if (number.includes("_")) box.append(el("p", "hint", "已有配置包含群成员会话标识；不改目标时保留该标识。聊天记录和人格判断仍使用真实场合。"));
    holder.append(box);
  };
  (settings.sessions || []).forEach(add);
  form.append(card("聊天对象白名单", "每次按权重随机抽取不同对象；权重越大越容易抽中。人格不匹配的场合不会接入。", append(el("div", "stack"), holder, button("添加聊天对象", () => { add(); dirty = true; }, "secondary"))));
  const f = (label, key, options, fallback) => field(label, `social.${key}`, valueAt(settings, `social.${key}`, fallback), options);
  form.append(card("发送限制与群聊插话", "计划聊天共用发送限制。群聊插话另受宿主配置与插话间隔约束。", append(el("div", "form-grid"), f("每次抽选对象数", "target_count", { type: "number", min: 1, max: 20 }, 1), f("同对象冷却（分钟）", "cooldown_minutes", { type: "number", min: 0, max: 10080 }, 60), f("每日发送上限", "daily_limit", { type: "number", min: 0, max: 1000 }, 5), f("群聊插话间隔（分钟）", "interjection_interval_minutes", { type: "number", min: 1, max: 1440 }, 30), f("免打扰开始", "quiet_start", { type: "time" }, "23:00"), f("免打扰结束", "quiet_end", { type: "time" }, "08:00"))));
  form.addEventListener("submit", (event) => {
    event.preventDefault(); const next = clone(settings);
    next.social = { ...next.social, ...readFields(form).social };
    next.sessions = entries.map((entry) => {
      const v = readFields(entry.controls); const unchanged = v.connection === entry.connection && v.type === entry.type && v.number === entry.numberValue;
      return { ...entry.original, umo: unchanged && entry.originalUMO ? entry.originalUMO : `${v.connection}:${v.type}:${v.number.trim()}`, enabled: entry.enabledField.querySelector("input").checked, weight: v.weight };
    });
    if (entries.some((entry) => !/^\d+$/.test(readFields(entry.controls).number))) { notice("群号 / QQ 号只填写数字。", true); return; }
    if (new Set(next.sessions.map((row) => row.umo)).size !== next.sessions.length) { notice("聊天对象重复，请合并重复白名单。", true); return; }
    saveSettings(next);
  });
  form.append(card("实际主动消息", "只读历史；发送成功不代表对方已经回应。", recordList(rows("deliveries").map((item) => ({ ...item, title: scopeLabel(item.umo || item.target || item.scope) })))));
  return form;
}

function editActivity(record) {
  const actions = el("div", "stack");
  for (const [key, label] of [["news", "新闻"], ["search", "搜索"], ["social", "主动聊天"]]) {
    const item = record.actions?.[key] || {};
    actions.append(append(el("div", "activity-action-editor"), checkField(`安排${label}`, `actions.${key}.enabled`, item.enabled), field(`${label}意图`, `actions.${key}.intent`, item.intent || item.reason || ""), field(`${label}执行时间`, `actions.${key}.at`, item.at || record.start || "", { hint: "填写活动内的 ISO 时间，例：2026-09-05T09:10:00+08:00。" })));
  }
  openEditor("编辑未开始的日程活动", [
    el("p", "hint", "仅修改尚未开始活动，不发送消息、不执行工具。标记总数必须符合当天配额；交换多个活动的标记请使用「批量调整未来活动」。"),
    field("标题", "title", record.title || "", { required: true }),
    field("开始时间", "start", record.start || "", { required: true, hint: "填写 ISO 时间，保留时区。" }),
    field("结束时间", "end", record.end || "", { required: true }),
    field("活动内容", "content", record.content || record.description || "", { type: "textarea", required: true }),
    field("地点", "location", record.location || ""),
    field("睡眠状态", "sleep_state", record.sleep_state || "未知", { options: [{ value: "未知", label: "未知" }, { value: "清醒", label: "清醒" }, { value: "睡眠", label: "睡眠中" }] }),
    record.actions ? actions : el("p", "hint", "这是升级前保留的日程；已有执行记录不重新生成。"),
  ], (patch) => action("update_activity", { id: record.id, patch }), "保存活动调整");
}
function scheduleSummary(activities) {
  const box = el("div", "schedule-counts");
  for (const [key, name] of [["news", "新闻"], ["search", "搜索"], ["social", "主动聊天"]]) {
    const marked = activities.filter((item) => item.actions?.[key]?.enabled);
    const success = marked.filter((item) => ["success", "succeeded", "sent", "completed", "done"].includes(item.actions[key].execution?.status) || item.actions[key].execution?.result?.status === "success");
    const skipped = marked.filter((item) => ["skipped", "failed", "expired", "cancelled"].includes(item.actions[key].execution?.status));
    const body = append(el("div", "metric"), el("div", "metric-label", name), el("div", "metric-value", `${marked.length} / ${success.length}`), el("div", "metric-foot", `计划机会 / 实际成功 · 跳过或失败 ${skipped.length}`));
    if (skipped.length) body.append(details(skipped.map((item) => ({ activity: item.title, ...item.actions[key].execution })), "查看跳过与失败原因"));
    box.append(body);
  }
  return box;
}
function renderSchedule() {
  const root = el("div", "stack"); const settings = snapshot.settings || {};
  const form = settingsForm("保存日程生成参数", "每天只生成一份正式日程。时间与次数修改默认次日生效，不重建今天已有日程；保存不会调用模型。");
  const f = (label, key, options, fallback) => field(label, `life.${key}`, valueAt(settings, `life.${key}`, fallback), options);
  const numberOptions = { type: "number", min: 0, max: 48, step: 1 };
  form.append(card("每日生成参数", "AI 在活动中安排新闻、搜索、聊天标记，三类可以落在同一个活动。", append(el("div", "form-grid"), f("每日生成时间", "daily_plan_time", { type: "time", required: true }, "06:00"), f("每天活动数", "activity_count", { ...numberOptions, min: 1 }, 10), f("新闻活动数", "news_count", numberOptions, 2), f("搜索活动数", "search_count", numberOptions, 2), f("主动聊天活动数", "social_count", numberOptions, 3), f("提前细化活动（分钟）", "detail_minutes", { type: "number", min: 0, max: 120 }, 10)), linkButton("聊天对象与白名单", "whitelist")));
  form.addEventListener("submit", (event) => {
    event.preventDefault(); const next = applyFields(clone(settings), form);
    if (["news_count", "search_count", "social_count"].some((key) => next.life[key] > next.life.activity_count)) { notice("每类行动最多在每个活动中安排一次，次数不能超过活动数。", true); return; }
    saveSettings(next);
  });
  root.append(form);
  const date = el("input"); date.type = "date"; date.value = scheduleDate || dayNow(); date.setAttribute("aria-label", "日程日期"); date.style.width = "auto";
  date.addEventListener("change", () => { scheduleDate = date.value; render(); });
  const activities = orderedActivities(date.value);
  const editable = activities.filter((item) => item.status === "planned" && new Date(item.start).getTime() > Date.now());
  const batch = () => openEditor("批量调整未来活动", [el("p", "hint", "仅修改列出的未开始活动。可交换新闻、搜索、聊天标记，但总数不得增加；已有执行记录保持。保存不调用模型、不发送消息。"), field("活动调整 JSON", "json", stringify({ updates: editable.map((item) => ({ id: item.id, changes: { title: item.title, start: item.start, end: item.end, content: item.content || item.description || "", location: item.location || "", sleep_state: item.sleep_state || "unknown", actions: Object.fromEntries(Object.entries(item.actions || {}).map(([key, value]) => [key, { enabled: value.enabled, intent: value.intent || "", at: value.at }])) } })) }), { type: "textarea", rows: 18 })], (values) => { try { return action("update_activities", JSON.parse(values.json)); } catch (error) { notice(`JSON 格式错误：${error.message}`, true); return false; } }, "保存未来活动调整");
  const toolbar = append(el("div", "section-toolbar"), date, append(el("div", "actions"), button("补生成缺失日程", () => confirmAction("补生成缺失的正式日程", "将真实调用模型生成指定日期的一份正式日程并保存。已有正式日程会保留；过期行动不会集中补发。", () => action("plan_day", { date: date.value }), false, "调用模型并生成"), "primary"), button("批量调整未来活动", batch, "secondary")));
  const list = recordList(activities, { timeline: true, emptyTitle: "这一天还没有安排", emptyDescription: "到生成时间自动生成；首次启动缺少当天日程时才补生成。", actions: (record) => editable.includes(record) ? append(el("div", "actions"), button("编辑", () => editActivity(record), "secondary", true), !record.detailed ? button("调用 AI 细化活动", () => action("detail_activity", { id: record.id }), "secondary", true) : null) : null });
  root.append(card("日程与实际行动", "按新闻 → 搜索 → 聊天执行；每个标记只执行一次。数量表示计划机会，实际发送仍遵守白名单与发送限制。", append(el("div", "stack"), toolbar, scheduleSummary(activities), list)));
  const day = rows("life_days").find((item) => (item.date || item.day || item.id?.slice(0, 10)) === date.value && (!item.scope || item.scope === "global"));
  const original = day ? append(el("div", "stack"), details(day.parameters || day.params || {}, "当日采用的生成参数"), details(day.full_request || day.request || {}, "生成输入快照（非 API 原文）"), details(day.raw_json ?? day.raw_response ?? "升级前日程未记录模型生成文本", "模型生成的日程文本"), details(day.adopted_activities || day.adopted || activities, "校验后采用的日程"), jsonButtons(day, `living-world-schedule-${date.value}.json`)) : empty("尚无正式生成记录", "生成参数、输入快照和模型生成文本会随正式日程长期保存，不受调试保留次数限制。");
  const debugId = day?.full_request?._debug_record_id;
  const debugView = debugId ? rows("debug_views").find((view) => view.id === debugId || view.record_ids?.includes(debugId)) : null;
  const archiveLink = linkButton(debugId ? "查看这次调用的 API 原文" : "调试与调用记录", debugId ? `debug?turn=${encodeURIComponent(debugView?.id || debugId)}` : "debug");
  archiveLink.addEventListener("click", () => { debugCategory = ""; });
  root.append(card("正式日程生成档案", "保存生成输入、模型生成文本和采用结果。实际 API 请求与返回请到调试记录查看；调试记录过期后原文可能已清理。", original, archiveLink));
  root.append(card("当前生活状态", "调整状态只保存生活数据，不触发模型调用或真实消息。", details(snapshot.state || {}, "查看状态数据"), button("调整当前状态", () => openEditor("调整当前状态", [field("精力", "energy", snapshot.state?.energy ?? 70, { type: "number", min: 0, max: 100 }), field("心情", "mood", snapshot.state?.mood || "平静"), field("当前作息", "routine", snapshot.state?.routine || "", { type: "textarea" })], (patch) => action("update_state", { patch })), "secondary", true)));
  return root;
}

function editMemory(record = {}) {
  const important = append(el("label", "inline-check"), el("input"), el("span", "", "重要记忆，长期保留"));
  important.firstChild.type = "checkbox"; important.firstChild.name = "important"; important.firstChild.checked = Boolean(record.important || record.pinned);
  openEditor(record.id ? "编辑记忆" : "添加记忆", [
    field("内容", "content", record.content || record.text || "", { type: "textarea", required: true, readOnly: Boolean(record.profile && record.scope === "global"), hint: record.profile && record.scope === "global" ? "此人物画像由已核对的谈话证据形成，内容保持只读，可调整保留标记或删除。" : "" }),
    field("分类", "kind", record.kind || "event", { options: ["knowledge", "event", "skill", "emotional"].map((kind) => ({ value: kind, label: kindNames[kind] })) }),
    record.id ? el("p", "hint", `所属场合：${scopeLabel(record.scope)}；人物：${record.person_id || "角色自身 / 未关联"}。编辑保留原有场合与人物；保存后可能调用 AI 核对未来日程，不发送 QQ 消息。`) : field("所属场合", "scope", "global", { options: scopeOptions(), hint: "私人谈话、经历与约定请选择对应会话。" }),
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
      actions: (record) => append(el("div", "actions"), button("编辑", () => editMemory(record), "secondary", true), button("删除", () => confirmAction("删除这条记忆", "删除后将不再参与检索，可能调用 AI 核对未来日程；不发送 QQ 消息，不删除其他记忆或 AstrBot 原始聊天。", () => action("delete_memory", { id: record.id }), true, "删除记忆"), "danger", true)),
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
    openEditor("合并选中的记忆", [el("p", "muted", `将 ${chosen.length} 条同场合记忆合并，可能调用 AI 核对未来日程，不发送 QQ 消息；请检查合并后的内容。`), field("合并内容", "content", chosen.map((row) => row.content || row.text || "").join("\n"), { type: "textarea", required: true })], async (values) => {
      const result = await action("merge_memories", { ids: chosen.map((row) => row.id), content: values.content });
      if (result !== false) selectedMemories.clear(); return result;
    }, "合并记忆");
  };
  fill();
  const actions = append(el("div", "actions"), button("合并所选", merge, "secondary", true), button("添加记忆", () => editMemory(), "primary", true));
  root.append(card("记忆与人物认知", "认识同一个人，谈话分场合。画像、共同回忆与自身体会可按分类查看。", append(el("div", "stack"), append(el("div", "filterbar"), search, kindSelect, scopeSelect), listContainer), actions));
  root.append(el("p", "hint", "此页供管理员查看全部场合。角色实际检索仍按当前会话过滤；私人记忆衍生的日程、日记与笔记继续保留来源场合。"));
  return root;
}

function realSourceAction(source, label, queryLabel = "", placeholder = "") {
  const body = el("div", "stack");
  const query = queryLabel ? field(queryLabel, "query", "", { placeholder }) : null;
  body.append(el("p", "muted", "此操作会真实调用来源和可能调用模型，成功后写入正式见闻；不会主动发送 QQ 消息。"));
  if (query) body.append(query);
  body.append(button(label, () => action("explore", { source, query: query?.querySelector("input").value || "", scope: "global" }), "secondary"));
  return body;
}
function renderJournal() {
  const root = el("div", "stack");
  append(root, append(el("div", "grid"), card("新闻阅读", "候选内容 → 按兴趣选择 → 阅读 → 感想。", observationList(rows("observations").filter((item) => observationKind(item, "news") || observationKind(item, "daily_digest")))), card("主动搜索记录", "活动和记忆中的兴趣成为查询，结果形成见闻笔记。", observationList(rows("observations").filter((item) => observationKind(item, "search"))))));
  const generate = () => openEditor("生成日记或笔记", [el("p", "hint", "真实调用模型，使用已有经历生成并保存记录，不发送消息。"), field("日期", "date", dayNow(), { type: "date", required: true }), field("类型", "kind", "journal", { options: [{ value: "journal", label: "生活日记" }, { value: "note", label: "见闻笔记" }] }), field("所属场合", "scope", "global", { options: scopeOptions(), hint: "只使用该场合允许回顾的内容。" })], (values) => action("generate_journal", values), "调用 AI 并保存记录");
  const entryActions = (record) => append(el("div", "actions"), button("删除记录", () => confirmAction("删除这篇记录", "删除选中的日记或笔记，原始见闻和记忆继续保留，不调用模型。", () => action("delete_entry", { id: record.id }), true, "删除记录"), "danger", true));
  append(root, append(el("div", "grid"), card("日记与见闻笔记", "从已有经历出发，保留来源场合。", recordList(rows("entries"), { actions: entryActions }), button("生成日记或笔记", generate, "secondary", true)), card("天气与 B 站见闻", "搜索、已观看与历史记忆分别标明。", observationList(rows("observations").filter((item) => !observationKind(item, "news") && !observationKind(item, "search") && !observationKind(item, "daily_digest"))))));
  const manual = append(el("details", "card"), el("summary", "", "手动读取来源（真实调用并保存见闻）"), append(el("div", "grid manual-sources"), card("新闻", "立即阅读一次，与日程执行记录分开。", realSourceAction("news", "读取新闻并写感想")), card("网页搜索", "沿用 AstrBot 网页搜索设置。", realSourceAction("search", "搜索并保存见闻", "搜索内容", "留空由 AI 根据活动选择主题")), card("和风天气", "读取已配置地点的天气。", realSourceAction("weather", "读取天气并保存")), card("B 站搜索", "搜索结果不会记成已经观看。", realSourceAction("bilibili", "搜索 B 站视频", "视频关键词")), card("观看视频", "调用 Bilibili AI Bot 的指定视频观看能力。", realSourceAction("bilibili_watch", "观看指定视频并保存", "视频 BV 号", "BV…")), card("公开视频记忆", "读取历史见闻，不记成今天新看过。", realSourceAction("bilibili_recent", "读取公开视频记忆"))));
  root.append(manual);
  return root;
}
function renderSources() {
  const settings = snapshot.settings || {};
  const form = settingsForm("保存来源设置", "保存只修改来源配置，不执行阅读。天气连接测试会真实访问和风天气，使用下方已保存的设置。");
  const f = (label, path, options = {}, fallback = "") => field(label, path, valueAt(settings, path, fallback), options);
  const weather = append(el("div", "form-grid"), f("地点 / 和风 Location ID", "weather.location", { placeholder: "北京 / 101010100" }), f("和风 API Host", "weather.api_host", { placeholder: "xxx.re.qweatherapi.com", hint: "填写和风控制台提供的 API Host。" }), f("认证方式", "weather.auth_mode", { options: [{ value: "api_key", label: "API Key" }, { value: "jwt", label: "JWT" }] }, "api_key"), f("认证凭据", "weather.credential", { type: "password", hint: "API Key 或生成后的 JWT；用于和风请求，不写入模型提示词。" }));
  form.append(card("和风天气", "固定使用和风天气。连接测试不生成角色感想、不发送消息。", weather, button("测试天气连接", () => {
    if (dirty) { notice("请先保存来源设置，再测试天气连接。", true); return; }
    action("test_weather");
  }, "secondary", true)));
  const biliStatus = rows("modules").find((item) => item.id === "bilibili");
  const dependency = snapshot.bilibili_dependency || snapshot.source_status?.bilibili;
  form.append(card("Bilibili AI Bot", "固定依赖 astrbot_plugin_bilibili_ai_bot；提供搜索、指定观看与公开视频记忆。", append(el("div", "stack"), badge(dependency?.available === true ? "ready" : dependency?.status || biliStatus?.status || "依赖状态待检测"), el("p", "muted", dependency?.text || dependency?.reason || biliStatus?.error || "缺失或停用依赖只影响 B 站与相应日报来源。"), f("近期公开视频记忆数量", "bilibili.recent_limit", { type: "number", min: 1, max: 30 }, 5), el("p", "hint", "日报会核对作者和发布日期；无法核实时明确标记，不把搜索或旧记忆当成新观看。"))));
  form.append(card("网页搜索", "自动沿用 AstrBot 的网页搜索服务与凭据。", el("p", "muted", "请在 AstrBot 的网页搜索设置中配置服务。这里无需填写工具名称或参数名。")));
  const newsRows = []; const newsHolder = el("div", "stack");
  const addNews = (source = { id: `custom-${Date.now()}`, name: "", url: "", enabled: true }) => {
    const item = el("div", "source-entry");
    const fields = append(el("div", "form-grid"), field("来源名称", "name", source.name, { required: true }), field("RSS / Atom 地址", "url", source.url, { type: "url", required: true }), checkField("启用这个新闻源", "enabled", source.enabled !== false));
    const entry = { source, item, fields }; newsRows.push(entry);
    append(item, fields, button("移除这个新闻源", () => { newsRows.splice(newsRows.indexOf(entry), 1); item.remove(); dirty = true; }, "danger", true)); newsHolder.append(item);
  };
  (settings.news?.sources || (settings.news?.feeds || []).map((url, i) => ({ id: `legacy-${i}`, name: `已有来源 ${i + 1}`, url, enabled: true }))).forEach(addNews);
  form.append(card("新闻源", "内置 BBC 中文、Google 新闻中文、Solidot、Hacker News、MIT Technology Review、Ars Technica，可逐项调整。", append(el("div", "stack"), newsHolder, append(el("div", "actions"), button("添加新闻源", () => { addNews(); dirty = true; }, "secondary", true), button("恢复六个默认新闻源", () => confirmAction("恢复默认新闻源", "将新闻来源列表替换为六个默认来源；已有见闻保留，不发起网络请求。", () => { dirty = false; return action("restore_news_sources"); }, false, "恢复默认来源"), "secondary", true)), f("每次候选新闻数量", "news.limit", { type: "number", min: 1, max: 30 }, 5))));
  const digestRows = []; const digestHolder = el("div", "stack");
  (settings.daily_digest?.sources || []).forEach((source) => {
    const fields = append(el("div", "form-grid"), field("日报名称", "name", source.name, { required: true }), field("B 站作者 UID", "uid", source.uid, { required: true }), field("每日读取时间", "time", source.time || "12:00", { type: "time", required: true }), field("检索关键词", "keywords", Array.isArray(source.keywords) ? source.keywords.join(" ") : source.keywords || "", { type: "textarea", rows: 2, hint: "多个关键词用空格分隔。" }), checkField("启用这个日报", "enabled", source.enabled !== false));
    digestRows.push({ source, fields }); digestHolder.append(append(el("div", "source-entry"), fields));
  });
  form.append(card("独立 AI 日报", "黑鸦 Heya 默认 12:00，橘鸦 Juya 默认 23:00。每天各一次，不占日程名额、不额外触发聊天，重启不集中补读。", append(el("div", "stack"), digestHolder, details(rows("daily_digest_runs"), "查看日报执行状态与跳过原因"), button("恢复两路默认 AI 日报", () => confirmAction("恢复默认 AI 日报", "恢复黑鸦 Heya 12:00 和橘鸦 Juya 23:00 的来源设置；已有阅读记录保留，不发起网络调用。", () => { dirty = false; return action("restore_digest_sources"); }, false, "恢复默认日报"), "secondary", true))));
  form.addEventListener("submit", (event) => {
    event.preventDefault(); const next = applyFields(clone(settings), form);
    for (const key of ["name", "url", "enabled", "uid", "time", "keywords"]) delete next[key];
    next.news.sources = newsRows.map((entry) => ({ ...entry.source, ...readFields(entry.fields) }));
    next.daily_digest = { ...next.daily_digest, sources: digestRows.map((entry) => ({ ...entry.source, ...readFields(entry.fields) })) };
    saveSettings(next);
  });
  return form;
}
function downloadJSON(value, filename) {
  const url = URL.createObjectURL(new Blob([stringify(value)], { type: "application/json;charset=utf-8" }));
  const link = el("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
}
function jsonButtons(value, filename) {
  return append(el("div", "actions"), button("复制 JSON", async () => {
    try { await navigator.clipboard.writeText(stringify(value)); notice("JSON 已复制"); }
    catch { openEditor("复制 JSON", field("完整 JSON", "copy", stringify(value), { type: "textarea", rows: 18, readOnly: true }), () => true, "关闭"); }
  }, "secondary", true), button("下载 JSON", () => downloadJSON(value, filename), "secondary", true));
}
function debugLabel(task) {
  if (debugTaskNames[task]) return debugTaskNames[task];
  const module = String(task || "").split(".")[0];
  return moduleNames[module] || "模型调用";
}
function rawBodyButtons(body, filename, direction, type = "json") {
  const label = direction === "request" ? "请求" : "返回";
  return append(el("div", "actions"), button(`复制${label}原文`, async () => {
    try { await navigator.clipboard.writeText(body); notice(`${label}原文已复制`); }
    catch { openEditor(`复制${label}原文`, field("原始正文", "copy", body, { type: "textarea", rows: 18, readOnly: true }), () => true, "关闭"); }
  }, "secondary", true), button(`下载${label}原文`, () => {
    const mime = type === "json" ? "application/json" : "text/plain";
    const url = URL.createObjectURL(new Blob([body], { type: `${mime};charset=utf-8` }));
    const link = el("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  }, "secondary", true));
}
function readableValue(value, depth = 0) {
  if (value == null || value === "") return el("p", "muted", "暂无内容");
  if (typeof value !== "object") return el("div", "debug-prose", value);
  if (depth > 7) return el("pre", "", stringify(value));
  if (Array.isArray(value)) {
    const list = el("div", "debug-value-list");
    value.forEach((item) => list.append(readableValue(item, depth + 1)));
    return list.childElementCount ? list : el("p", "muted", "暂无内容");
  }
  if (Array.isArray(value.activities)) {
    const timeline = el("div", "debug-value-list");
    value.activities.forEach((activity) => timeline.append(append(el("div", "debug-activity"),
      el("p", "timeline-time", `${activity.start || ""} — ${activity.end || ""}`),
      el("h4", "", activity.title || activity.content || "活动"),
      el("p", "debug-prose", activity.description || ""),
      el("p", "muted", [activity.location, activity.sleep_state].filter(Boolean).join(" · ")),
      activity.actions ? el("p", "muted", Object.entries(activity.actions).filter(([, action]) => action?.enabled).map(([key]) => ({ news: "阅读新闻", search: "搜索", social: "主动聊天" })[key] || key).join(" · ")) : null)));
    const remaining = { ...value }; delete remaining.activities;
    if (Object.keys(remaining).length) timeline.append(readableValue(remaining, depth + 1));
    return timeline;
  }
  const names = { role: "角色", content: "内容", text: "正文", name: "名称", title: "标题", description: "说明", source: "来源", sources: "来源", intent: "意图", arguments: "参数", function: "工具", type: "类型", status: "状态", prompt_tokens: "输入 token", completion_tokens: "输出 token", total_tokens: "总 token", input_tokens: "输入 token", output_tokens: "输出 token", cached_tokens: "缓存 token", reasoning_tokens: "推理 token", query: "搜索内容", factual_summary: "事实摘要", impression: "角色感想", selection_reason: "选题理由", reading_basis: "阅读依据", reason: "原因", message: "消息", chain: "消息内容" };
  const list = el("dl", "debug-value");
  for (const [key, item] of Object.entries(value)) {
    const shown = key === "role" ? ({ user: "用户", assistant: "AI", system: "系统指令", developer: "开发者指令", tool: "工具结果" })[item] || item : item;
    append(list, el("dt", "", names[key] || key), append(el("dd"), readableValue(shown, depth + 1)));
  }
  return list.childElementCount ? list : el("p", "muted", "暂无内容");
}
function copyRecordToTrial(record) {
  let draft = clone(record.request);
  if (draft.arguments) {
    const args = draft.arguments;
    const parameters = { ...args };
    for (const key of ["prompt", "system_prompt", "contexts", "image_urls", "audio_urls", "func_tool", "model", "session_id", "extra_user_content_parts", "tool_calls_result"]) delete parameters[key];
    draft = { task: record.task, module: record.module || "reply", scope: record.scope, provider_id: draft.provider_id, model: args.model || draft.model, prompt_mode: "raw", prompt: args.prompt || "", system_prompt: args.system_prompt || "", contexts: args.contexts || [], image_urls: args.image_urls || [], audio_urls: args.audio_urls || [], tools: args.func_tool || [], extra_user_content_parts: args.extra_user_content_parts || [], tool_calls_result: args.tool_calls_result || [], positional_arguments: draft.positional_arguments || [], parameters };
  }
  testRequest = stringify(draft); render(); $("[name=request_json]")?.scrollIntoView({ behavior: "smooth", block: "center" });
}
function formatTaskContext(context) {
  if (context == null) return "";
  const text = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (typeof context === "object" && !Array.isArray(context)) return Object.entries(context).map(([key, value]) => `【${key}】\n${text(value)}`).join("\n\n");
  return text(context);
}
function toolArguments(value) {
  if (typeof value !== "string") return value;
  try { return JSON.parse(value); } catch { return value; }
}
function toolResultContent(value) {
  if (value && typeof value === "object" && Array.isArray(value.content)) {
    const list = el("div", "debug-value-list");
    value.content.forEach((part) => list.append(part?.type === "text" && typeof part.text === "string" ? el("div", "debug-prose", part.text) : readableValue(part)));
    list.append(details(value, "工具返回详情（第三方公开结果）"));
    return list;
  }
  return readableValue(value);
}
function legacyDebugViews(records) {
  const grouped = new Map();
  for (const record of records) {
    const id = record.turn_id || record.id;
    if (!grouped.has(id)) grouped.set(id, []);
    grouped.get(id).push(record);
  }
  return [...grouped.entries()].map(([id, group]) => {
    const first = group.find((record) => record.kind === "turn") || group[0];
    return { id, task: first.task, scope: first.scope, created_at: first.created_at, status: first.status, error: first.error, legacy: true, categories: [...new Set(group.map((record) => record.category || record.task))],
      sources: group.filter((record) => ["chat.context", "reply.request"].includes(record.task) || (!record.turn_id && record.request)).map((record) => ({ title: debugLabel(record.task), source: "旧版请求快照（非 API 原文）", content: record.request })),
      calls: [], adopted: group.filter((record) => ["model", "test"].includes(record.kind)).map((record) => record.response ?? record.reply ?? record.result),
      sends: group.filter((record) => record.task === "reply.send").map((record) => ({ status: record.status, content: record.request?.message })) };
  });
}
function renderDebugView(view, records, initiallyOpen) {
  const container = el("details", "debug-round"); container.dataset.turn = view.id;
  const focused = new URLSearchParams(location.hash.split("?")[1] || "").get("turn");
  container.open = focused ? focused === view.id || view.record_ids?.includes(focused) : initiallyOpen;
  const related = records.filter((record) => record.turn_id === view.id || record.id === view.id);
  const calls = Array.isArray(view.calls) ? view.calls : [];
  const selected = debugSelections.get(view.id) || { call: 0, tab: 0 };
  if (selected.call >= calls.length) selected.call = 0;
  debugSelections.set(view.id, selected);
  append(container, append(el("summary", "debug-round-summary"), append(el("span"), el("strong", "", view.title || debugLabel(view.task)), el("small", "muted", `${scopeLabel(view.scope)} · ${stamp(view.created_at) || "时间未记录"}${calls.length ? ` · ${calls.length} 次请求` : ""}`)), badge(view.status)));
  const body = el("div", "debug-round-body");
  if (view.legacy) body.append(el("p", "hint warning", "旧版快照，非 API 原文。升级前没有捕获实际 HTTP 正文，不能补成原始请求或返回。"));
  if (view.error) body.append(el("p", "danger-copy", stringify(view.error)));
  const controls = el("div", "debug-call-controls");
  const callSelector = field("查看第几次请求", "debug_call", String(selected.call), { options: calls.length ? calls.map((call, index) => ({ value: String(index), label: `第 ${index + 1} 次 · ${call.model || call.provider_id || "模型"} · ${statusNames[call.status] || call.status || "等待返回"}` })) : [{ value: "0", label: "没有可查看的 API 请求" }] });
  callSelector.querySelector("select").disabled = calls.length < 2;
  controls.append(callSelector); body.append(controls);
  const tabs = el("div", "debug-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "调用记录四项视图");
  const panel = el("div", "debug-tab-panel"); panel.setAttribute("role", "tabpanel");
  const names = ["① 上下文与信息来源", "② API 原始请求", "③ API 原始返回", "④ 回复阅读版"];
  const buttons = names.map((name, index) => {
    const tab = button(name, () => { selected.tab = index; update(); }, "secondary", true);
    tab.setAttribute("role", "tab"); tab.id = `debug-tab-${view.id}-${index}`; tab.setAttribute("aria-controls", `debug-panel-${view.id}`);
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); selected.tab = event.key === "Home" ? 0 : event.key === "End" ? 3 : (index + (event.key === "ArrowRight" ? 1 : 3)) % 4; update(); buttons[selected.tab].focus();
    }); tabs.append(tab); return tab;
  });
  panel.id = `debug-panel-${view.id}`;
  const update = () => {
    const call = calls[selected.call];
    buttons.forEach((tab, index) => { tab.setAttribute("aria-selected", String(index === selected.tab)); tab.tabIndex = index === selected.tab ? 0 : -1; });
    panel.setAttribute("aria-labelledby", buttons[selected.tab].id); panel.replaceChildren();
    if (selected.tab === 0) {
      panel.append(el("p", "muted", "这里展示组装时记录的信息来源与实际选用资料；最终发往接口的内容以②为准。"));
      const sources = el("div", "debug-sources");
      for (const source of view.sources || []) sources.append(append(el("section", "debug-source"), el("h4", "", source.title || "上下文资料"), el("p", "debug-source-origin", `来源：${source.source || "未记录"}${source.placement ? ` · 放入：${source.placement}` : ""}`), readableValue(source.content)));
      panel.append(sources.childElementCount ? sources : empty("没有可用的信息来源清单", "本次尚未组装上下文，或这条旧记录没有保存来源。"));
      const injection = append(el("section", "debug-injection"), el("h4", "", "插件实际加入的完整文本"));
      if (view.stable_injected_text) append(injection, el("p", "muted", "稳定角色资料 · system 消息"), el("div", "debug-prose", stringify(view.stable_injected_text)));
      if (view.injected_text) append(injection, el("p", "muted", "本轮动态资料"), el("div", "debug-prose", stringify(view.injected_text)));
      if (!view.stable_injected_text && !view.injected_text) injection.append(el("p", "muted", "未记录注入文本；不根据其他字段推测。"));
      panel.append(injection);
      if (view.legacy && view.legacy_snapshot != null) panel.append(details(view.legacy_snapshot, "旧版输入快照（非 API 原文）"));
    } else if (selected.tab === 1 || selected.tab === 2) {
      const isRequest = selected.tab === 1;
      const raw = isRequest ? call?.request_body : call?.response_body;
      if (typeof raw === "string") {
        const type = isRequest ? "json" : call.response_type || "text";
        const safeId = String(call.id || `${view.id}-${selected.call + 1}`).replace(/[^\w.-]/g, "_");
        panel.append(el("p", "muted", isRequest ? "实际发往模型接口的请求正文，仅隐藏认证凭据。复制和下载直接保留正文，不加包装。" : type === "sse" ? "这是接口实际返回的事件流（SSE），不是一份单独 JSON。④提供合并阅读，合并结果不是原文。" : "接口实际返回的正文，保留原有字段；下方内容没有经过回复提取。"));
        if (call.url || call.http_status) panel.append(el("p", "debug-endpoint", [call.method, call.url, call.http_status ? `HTTP ${call.http_status}` : ""].filter(Boolean).join(" · ")));
        append(panel, rawBodyButtons(raw, `living-world-${safeId}-${isRequest ? "request" : "response"}.${type === "sse" ? "sse" : type === "json" ? "json" : "txt"}`, isRequest ? "request" : "response", type), el("pre", "debug-raw", raw));
        if (call.error) panel.append(el("p", "danger-copy", stringify(call.error)));
      } else {
        panel.append(empty(isRequest ? "没有捕获 API 原始请求" : "没有收到可用的 API 原始返回", view.legacy ? "旧版只保存了宿主快照，不能当作 API 原文。" : call?.error || view.error || (call ? `捕获状态：${statusNames[call.capture_status] || call.capture_status || "未知"}。不使用中间参数代替原文。` : "本次没有记录到模型 HTTP 请求；请查看接入结果或捕获状态。")));
      }
    } else {
      const reading = call?.reading || {};
      if (call?.response_type === "sse") panel.append(el("p", "hint", "以下为事件流合并后的阅读内容；接口原文保留在③。"));
      if (view.legacy) panel.append(el("p", "muted", "以下来自旧版解析结果，不代表接口原始返回。"));
      const section = (title, value) => panel.append(append(el("section", "debug-reading-section"), el("h4", "", title), readableValue(value)));
      if (reading.text) section("回复正文", reading.text);
      if (reading.reasoning) section("接口返回的推理内容", reading.reasoning);
      if (reading.tool_calls?.length) {
        const tools = append(el("section", "debug-reading-section"), el("h4", "", "模型请求调用的工具"));
        for (const tool of reading.tool_calls) {
          const fn = tool.function || tool;
          tools.append(append(el("div", "debug-tool-result"), el("h4", "", fn.name || "工具调用"), readableValue(toolArguments(fn.arguments ?? fn.input ?? {}))));
        }
        panel.append(tools);
      }
      if (reading.structured != null) section("结构化结果", reading.structured);
      if (reading.usage && Object.keys(reading.usage).length) section("本次模型用量", reading.usage);
      if (reading.error) section("接口返回的错误", reading.error);
      if (!panel.childElementCount || (!reading.text && !reading.reasoning && !reading.tool_calls?.length && reading.structured == null)) panel.append(el("p", "muted", "本次没有可提取的模型回复；失败、取消和空返回不会生成替代内容。"));
      if (view.tool_results?.length) {
        const results = append(el("section", "debug-reading-section"), el("h4", "", "工具实际返回"), el("p", "muted", "这里只展示工具参数和公开返回，不表示已捕获第三方工具内部的模型调用。"));
        for (const tool of view.tool_results) results.append(append(el("div", "debug-tool-result"), append(el("div", "record-head"), el("h4", "", tool.request?.name || tool.task || "工具调用"), badge(tool.status)), el("p", "muted", "调用参数"), readableValue(toolArguments(tool.request?.arguments ?? tool.request)), el("p", "muted", "返回内容"), toolResultContent(tool.result)));
        panel.append(results);
      }
      if (view.adopted?.length) {
        const adopted = append(el("section", "debug-reading-section"), el("h4", "", "插件最终采用的内容"));
        for (const item of view.adopted) {
          if (item && typeof item === "object" && Object.hasOwn(item, "content")) {
            const result = append(el("div", "debug-adopted"), item.title ? el("h4", "", item.title) : null, item.status ? badge(item.status) : null, readableValue(item.content));
            if (item.error) result.append(el("p", "danger-copy", stringify(item.error)));
            adopted.append(result);
          } else adopted.append(readableValue(item));
        }
        panel.append(adopted);
      }
      if (view.legacy && view.legacy_response != null) panel.append(append(el("section", "debug-reading-section"), el("h4", "", "旧版保存的回复结果"), readableValue(view.legacy_response)));
      if (view.sends?.length) {
        const sends = el("section", "debug-reading-section"); sends.append(el("h4", "", "实际聊天发送"));
        for (const send of view.sends) sends.append(append(el("div", "debug-send"), badge(send.status), readableValue(send.content || send.text || send.message), send.error ? el("p", "danger-copy", stringify(send.error)) : null));
        panel.append(sends);
      }
    }
    const oldTrial = controls.querySelector(".debug-trial-copy"); if (oldTrial) oldTrial.remove();
    const replay = records.find((record) => record.id === call?.record_id) || related.filter((record) => ["model", "test"].includes(record.kind))[selected.call];
    if (replay?.request) { const copy = button("复制到试跑编辑器", () => copyRecordToTrial(replay), "secondary", true); copy.classList.add("debug-trial-copy"); controls.append(copy); }
  };
  callSelector.querySelector("select").addEventListener("change", (event) => { selected.call = Number(event.target.value); update(); });
  append(body, tabs, panel); container.append(body); update(); return container;
}
function renderDebug() {
  const root = el("div", "stack");
  root.append(el("p", "hint", "每次调用只看四项：信息来源、API 原始请求、API 原始返回、回复阅读版。原文来自实际 HTTP 收发，仅隐藏认证凭据；管理员可查看所有场合。"));
  const settings = snapshot.settings || {};
  const retention = settingsForm("保存记录数量", "聊天按完整轮次保留最近 N 轮；后台任务按类别保留最近 N 次。清理不影响正式数据。");
  retention.append(field("每类保留次数", "debug.retain_per_category", settings.debug?.retain_per_category ?? 10, { type: "number", min: 1, max: 1000 }));
  retention.addEventListener("submit", (event) => { event.preventDefault(); saveSettings(applyFields(clone(settings), retention)); });
  root.append(card("调试记录保留", "默认最近 10 轮聊天及每类 10 次后台调用；工具后的模型调用也保留。", retention));
  if (snapshot.provider_capture_available === false) root.append(el("p", "hint warning", "当前模型捕获适配不可用，未捕获内容会明确标注；旧版快照不会冒充 API 原文。"));
  const templates = snapshot.debug?.templates || [];
  const records = rows("debug_records");
  const options = [...new Set([...templates.map((item) => item.task), ...records.map((item) => item.task).filter(Boolean)])];
  if (options.length && !options.includes(debugTask)) debugTask = options[0];
  const taskField = field("任务类别", "task", debugTask, { options: options.length ? options : [debugTask] });
  const scopeField = field("测试场合", "scope", "global", { options: scopeOptions() });
  let mode = "structured";
  try { const current = JSON.parse(testRequest); mode = current.prompt_mode || "raw"; } catch { /* An empty editor starts with a structured draft. */ }
  const modeField = field("测试提示词模式", "request_mode", mode, { options: [{ value: "structured", label: "组合模板与动态上下文" }, { value: "raw", label: "直接编辑完整 prompt" }] });
  const modeHint = el("p", "hint");
  const updateHint = () => { modeHint.textContent = modeField.querySelector("select").value === "structured" ? "组合模式：请编辑 JSON 中的 template 与 dynamic_context；执行时重新组合实际 prompt。JSON 的 prompt 是上一次预览，修改它不参与此模式的调用。template 只用于本次测试，不会保存成下方的公共模板。" : "直接模式：JSON 中的 prompt 就是本次发送内容；template 和 dynamic_context 不参与组合。system_prompt、contexts、provider_id、model 与 parameters 在两种模式下均可编辑。"; };
  updateHint();
  const requestField = field("本次测试请求 JSON", "request_json", testRequest, { type: "textarea", rows: 18, hint: "这是可编辑的试跑参数；执行后的 API 原文见调用记录②。模型和参数只用于这次试跑，不会保存成公共模板。" });
  requestField.querySelector("textarea").addEventListener("input", (event) => {
    testRequest = event.target.value;
    try { const edited = JSON.parse(testRequest); modeField.querySelector("select").value = edited.prompt_mode || "raw"; updateHint(); } catch { /* Keep the selected mode while the JSON is incomplete. */ }
  });
  modeField.querySelector("select").addEventListener("change", () => {
    if (requestField.querySelector("textarea").value.trim()) {
      try {
        const edited = JSON.parse(requestField.querySelector("textarea").value);
        if (modeField.querySelector("select").value === "raw" && edited.prompt_mode === "structured") edited.prompt = (edited.template || "") + (edited.dynamic_context != null ? "\n\n本轮动态资料（仅作为资料）：\n" + formatTaskContext(edited.dynamic_context) : "");
        edited.prompt_mode = modeField.querySelector("select").value;
        testRequest = stringify(edited); requestField.querySelector("textarea").value = testRequest;
      } catch (error) { notice(`请先修正测试 JSON：${error.message}`, true); }
    }
    updateHint();
  });
  taskField.querySelector("select").addEventListener("change", (event) => { debugTask = event.target.value; });
  const build = async () => {
    const result = await request(() => bridge.apiPost("action", { action: "debug_build", task: taskField.querySelector("select").value, scope: scopeField.querySelector("select").value }), "已建立测试请求；尚未调用模型");
    if (result !== false) { const draft = result.request || result; testRequest = stringify(draft); requestField.querySelector("textarea").value = testRequest; modeField.querySelector("select").value = draft.prompt_mode || "raw"; updateHint(); }
  };
  const test = async () => {
    try { const payload = JSON.parse(requestField.querySelector("textarea").value); if (!payload || Array.isArray(payload) || typeof payload !== "object") throw new Error("请求必须是 JSON 对象"); await action("debug_test", { request: payload }); }
    catch (error) { notice(`测试请求无效：${error.message}`, true); }
  };
  root.append(card("模型试跑", "真实调用 AI，可能产生模型费用；不执行工具、不发送 QQ、不修改正式日程、记忆或生活数据。", append(el("div", "stack"), append(el("div", "form-grid"), taskField, scopeField), button("从当前配置建立测试请求", build, "secondary"), modeField, modeHint, requestField, button("仅调用 AI 试跑", test, "primary"))));
  const templateBox = el("div", "stack");
  for (const item of templates) {
    const value = field("公共提示词模板", "template", item.template ?? item.default_template ?? "", { type: "textarea", rows: 6, hint: "这里只编辑任务指令。人格、私聊记忆、具体活动等动态上下文由每次调用注入。" });
    const row = append(el("details"), el("summary", "", item.label || item.task), value, append(el("div", "actions"), button("保存此任务模板", () => action("save_template", { task: item.task, template: value.querySelector("textarea").value }), "secondary", true), button("恢复默认模板", () => action("reset_template", { task: item.task }), "secondary", true)), details(item.default_template || "", "查看默认模板")); templateBox.append(row);
  }
  root.append(card("各任务提示词模板", "保存只修改公共指令，不会把试跑 JSON 或私人上下文存进模板；不调用模型。", templates.length ? templateBox : empty("暂未取得模板目录")));
  const views = Array.isArray(snapshot.debug_views) ? snapshot.debug_views : legacyDebugViews(records);
  const categories = [...new Set(views.flatMap((view) => view.categories?.length ? view.categories : [view.task]).filter(Boolean))];
  const category = field("筛选任务类别", "category", debugCategory, { options: [{ value: "", label: "全部类别" }, ...categories.map((task) => ({ value: task, label: `${debugLabel(task)} · ${task}` }))] });
  category.querySelector("select").addEventListener("change", (event) => { debugCategory = event.target.value; render(); });
  const list = el("div", "debug-round-list");
  const filtered = views.filter((view) => !debugCategory || (view.categories || [view.task]).includes(debugCategory));
  const focused = new URLSearchParams(location.hash.split("?")[1] || "").get("turn");
  if (focused && !views.some((view) => view.id === focused || view.record_ids?.includes(focused))) list.append(el("p", "hint warning", "这次调用的调试记录已清理或尚未产生；正式日程档案仍保留。"));
  filtered.forEach((view, index) => list.append(renderDebugView(view, records, index === 0)));
  const callPanel = card("调用记录", "选择一次聊天或后台任务；多次模型请求使用下拉框切换，请求与返回始终成对。",
    append(el("div", "stack"), category, list.childElementCount ? list : empty("此类别暂时没有调用记录")),
    button(debugCategory ? "清空此类调试记录" : "清空全部调试记录", () => confirmAction("清空调试记录", "删除选定类别的完整聊天轮次和后台任务记录；不删除正式日程、记忆、消息历史或执行防重记录，不产生真实调用。", () => action("debug_clear", debugCategory ? { category: debugCategory } : {}), true, "清空调试记录"), "danger", true));
  root.insertBefore(callPanel, root.children[1] || null);
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
function renderSocial() { return renderWhitelist(); }

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
  root.append(card("整理旧记忆", "高级操作：淡化普通旧记忆、归档低强度记录；重要记忆保留。只整理记忆数据，不调用 AI、不发送消息。", button("整理旧记忆", () => action("maintain_memory"), "secondary")));
  root.append(card("当前数据概况", "只读查看，不会触发探索或发送。", details({ version: snapshot.version, counts: Object.fromEntries(["activities", "memories", "observations", "entries", "events", "deliveries", "usage"].map((key) => [key, rows(key).length])), diagnostics: snapshot.diagnostics }, "查看数据统计与诊断")));
  return root;
}

function render() {
  const route = location.hash.slice(1).split("?")[0];
  const view = Object.hasOwn(titles, route) ? route : "overview";
  $("#page-title").textContent = titles[view];
  document.title = `${titles[view]} · Living World`;
  document.querySelectorAll("#navigation a").forEach((link) => { if (link.dataset.view === view) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
  if (!snapshot) return;
  const renderers = { whitelist: renderWhitelist, sources: renderSources, debug: renderDebug, overview: renderOverview, settings: renderSettings, schedule: renderSchedule, memory: renderMemory, journal: renderJournal, social: renderSocial, data: renderData };
  content.replaceChildren(renderers[view]());
  if (view === "overview") { const timeline = content.querySelector(".home-timeline"); const current = timeline?.querySelector(".is-current"); if (current) timeline.scrollTop = Math.max(0, current.offsetTop - timeline.offsetTop - 60); }
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
