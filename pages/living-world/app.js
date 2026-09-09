const bridge = window.AstrBotPluginPage;
const $ = (selector) => document.querySelector(selector);
const content = $("#content");
const titles = { whitelist: "聊天对象与白名单", sources: "内容来源", debug: "调试与调用记录", overview: "今日生活", settings: "角色、模型与模块", drives: "内在状态", schedule: "日程与行动", memory: "记忆与人物", journal: "见闻与日记", social: "社交与调用", data: "数据管理" };
const moduleNames = { daily_digest: "AI 日报", debug: "调试记录", life: "日程生活", state: "角色状态", drives: "内在状态", memory: "长期记忆", reply: "被动回复", interjection: "群聊插话", proactive: "主动社交", news: "新闻阅读", search: "主动搜索", weather: "天气", bilibili: "B 站见闻", journal: "生活日记", notes: "见闻笔记" };
const kindNames = { knowledge: "知识", event: "事件", skill: "技能", emotional: "情感与体会", profile: "人物画像", journal: "日记", note: "笔记", notes: "笔记", news: "新闻", search: "搜索", weather: "天气", bilibili: "B 站搜索", bilibili_watch: "观看 B 站视频", bilibili_recent: "读取 B 站历史见闻", fiction: "角色日常", life: "生活", social: "社交", read: "已读取", searched: "已搜索", watched: "已观看" };
const statusNames = { enabled: "已开启", disabled: "已关闭", ready: "就绪", running: "运行中", paused: "已暂停", unavailable: "不可用", error: "异常", failed: "失败", planned: "已计划", pending: "待执行", reserved: "已预留", detailed: "已细化", completed: "已完成", done: "已完成", sent: "已发送", skipped: "已跳过", cancelled: "已取消", expired: "已过期", success: "成功", succeeded: "成功", ok: "正常" };
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
let debugBodySequence = 0;
let selectedDrive = "loneliness";
const driveDrafts = new Map();
const driveNames = { loneliness: "寂寞值", energy: "精力" };
const debugTaskNames = { "chat.turn": "聊天回复", "reply.model": "聊天回复", "reply.request": "聊天回复", "reply.tool": "聊天工具", "life.plan": "生成日程", "life.plan_day": "生成今日日程", "life.detail": "细化活动", "life.adjust": "调整日程", "news.select": "挑选新闻", "news.read": "阅读新闻", "search.topic": "选择搜索主题", "search.note": "搜索见闻笔记", "journal.write": "生成日记", "journal.brief": "日记简报", "notes.write": "生成笔记", "notes.brief": "笔记简报", "debug.test": "模型试跑", "test": "模型试跑" };

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
  document.querySelectorAll("button").forEach((node) => { node.disabled = value || node.dataset.disabled === "true"; });
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
  return request(async () => { await readState(); dirty = false; render(); }, location.hash === "#drives" ? "数值已刷新，未保存的修改继续保留" : "数据已刷新");
}
async function action(name, payload = {}) {
  return request(async () => {
    const result = await bridge.apiPost("action", { action: name, ...payload });
    lastResult = { action: name, result, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }) };
    await readState();
    render();
    return result ?? true;
  }, (result) => result?.reason === "brief_failed" ? "简报生成失败，已保留正文和已有简报；可点击生成简报重试。" : result?.status === "skipped" ? `本次未执行：${result.text || result.reason || "请查看操作结果"}` : result?.status === "failed" ? `本次执行失败：${result.text || result.error || result.reason || "请查看操作结果"}` : "操作已完成，请查看执行结果");
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
    if (options.body) append(item, options.body(record));
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
function actionReason(value) {
  const labels = { regenerated: "重新细化", outline_changed: "活动大纲已修改", activity_updated: "活动大纲已修改", module_disabled: "行动模块已关闭", daily_limit_reduced: "降低每日上限后取消", expired_action_window: "已超过行动有效时间", expired_before_action: "执行前活动已结束", overdue_after_restart: "重启时已过期，不补做", execution_interrupted_outcome_unknown: "上次执行中断，结果未确认，不重复执行" };
  return labels[value] || value;
}
function actionBadges(activity) {
  const box = el("div", "action-badges");
  for (const [name, label] of [["news", "新闻"], ["search", "搜索"], ["social", "主动聊天"]]) {
    const item = activity.actions?.[name]; if (!item?.enabled) continue;
    const execution = item.execution || {};
    const result = badge(`${label} ${clockTime(item.at || activity.start)} · ${statusNames[execution.status] || execution.status || "计划机会"}`, execution.status === "succeeded" || execution.status === "sent" ? "good" : "");
    result.title = [item.intent || item.reason, actionReason(execution.reason || execution.result?.reason)].filter(Boolean).join("；");
    box.append(result);
    if (execution.reason) box.append(el("span", "muted", `${label}：${actionReason(execution.reason)}`));
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
  for (const [label, value] of [["心情", state.mood || "未知"], ["精力", snapshot.drives?.meters?.energy?.display_value === undefined ? "未知" : `${snapshot.drives.meters.energy.display_value} / 100`], ["地点", current?.location || state.location || "未知"], ["睡眠", sleepLabels[sleep] || sleep], ["天气", weather?.factual_summary || weather?.text || "尚未取得天气"], ["今日活动", `${today.length} 个活动`]]) stateGrid.append(append(el("div", "state-tile"), el("span", "", label), el("strong", "", value)));
  const currentBody = append(el("div", "current-activity"), el("p", "eyebrow", current ? `${clockTime(current.start)} — ${clockTime(current.end)} · 当前活动` : "当前没有进行中的活动"), el("h2", "", current?.title || "等待下一段生活"), el("p", "", current?.description || current?.content || "当天日程生成后，活动将在自己的时间开始。"), current ? actionBadges(current) : null);
  const nextBody = append(el("div", "stack"), el("div", "next-social-time", next ? clockTime(next.action.at || next.activity.start) : "暂无待执行联系"), el("p", "record-body", next ? next.action.intent || next.activity.title : "临近活动细化时，再决定是否主动聊天。"), el("p", "hint", "活动细化决定是否主动聊天；对象按白名单权重抽选，并检查冷却、免打扰与发送上限。"), linkButton("查看聊天对象与白名单", "whitelist"));
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
  form.append(card("角色与世界", "使用指定 AstrBot 人格，补充角色资料与生活背景。精力与寂寞值在「内在状态」管理。", append(el("div", "form-grid"), f("绑定人格", "persona_id", { options: personas, required: true }), f("时区", "character.timezone", {}, "Asia/Shanghai"), f("角色补充资料", "character.profile", { type: "textarea" }), f("世界设定", "character.world", { type: "textarea" }), f("初始情绪", "character.mood", {}, "平静"), f("初始地点", "character.location", { hint: "没有当前日程地点时采用此设置，留空显示未知。" }), f("初始睡眠状态", "character.sleep_state", { options: ["未知", "清醒", "睡眠"] }, "未知"))));
  form.append(card("业务能力", "关闭停止该项行为并保留已有数据；开启后按日程和模块规则运行。", modulePanel(true)));
  const groupReply = f("本轮群聊回复要求", "reply.group_prompt", { type: "textarea", rows: 5, hint: "默认按极短模式接话。文案可改；保存后从下一轮群聊生效。留空则不附加要求，最多 8000 字符。" }, snapshot.reply_defaults?.group_prompt || "");
  groupReply.querySelector("textarea").maxLength = 8000;
  const groupReplyBox = card("群聊回复", "注入位置固定：本轮 user 消息最后，位于生活资料块之外、之后。只用于已接入的普通群聊回复，不写入聊天历史。", append(el("div", "stack"), groupReply, button("恢复极短默认文案", () => { groupReply.querySelector("textarea").value = snapshot.reply_defaults?.group_prompt || ""; dirty = true; notice("已填入默认文案，点击「保存全部设置」后生效"); }, "secondary", true)));
  groupReplyBox.id = "group-reply-settings";
  form.append(groupReplyBox);
  const models = el("div", "form-grid");
  [["默认模型", "default"], ["生活与日程", "life"], ["记忆提炼", "memory"], ["主动社交", "social"], ["外部见闻", "exploration"], ["日记与笔记", "journal"]].forEach(([label, key]) => models.append(f(label, `models.${key}`, { options: providers })));
  form.append(card("模型分配", "模块留空继承默认模型；默认模型留空沿用宿主配置。", models));
  form.append(card("其他设置入口", "日程大纲、行动上限、聊天白名单和内容来源在各自页面管理。", append(el("div", "actions"), linkButton("日程生成参数", "schedule"), linkButton("聊天对象与白名单", "whitelist"), linkButton("内容来源", "sources"))));
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
    const preview = el("p", "session-line");
    const sessionInfo = el("div", "session-status");
    const debugLink = el("span", "actions");
    const showStatus = (value) => {
      const expanded = sessionInfo.querySelector("details")?.open || false;
      const detailBody = append(el("div", "session-details-body"), preview);
      const details = append(el("details", "session-details"), el("summary", "", "会话与检查详情"), detailBody);
      details.open = expanded;
      debugLink.replaceChildren();
      if (!value) {
        sessionInfo.replaceChildren(el("p", "session-line", "尚未检查会话。没有历史也可以接入；检查只读，不调用模型或发送消息。"), details);
        return;
      }
      const historyLabel = value.history_status === "found" ? `已找到历史：${value.history_count} 条` : value.history_status === "error" ? "历史读取失败" : "首次对话／暂无历史";
      const source = { host_default: "AstrBot 默认设置", conversation: "当前对话指定", session_rule: "宿主会话规则", unresolved: "尚未解析" }[value.persona_source] || value.persona_source || "未知";
      const contextStatus = value.context_status || {};
      const attempt = contextStatus.last_attempt;
      const injected = contextStatus.last_injected;
      sessionInfo.replaceChildren(append(el("div", "actions"), badge(historyLabel, value.history_status === "error" ? "bad" : ""), badge(value.allowed ? "配置允许接入" : "配置未通过", value.allowed ? "good" : "bad")),
        el("p", "session-line", `实际人格：${value.persona_id || "未解析"} · 插件绑定：${value.bound_persona || settings.persona_id || "未设置"} · 人格来源：${source}`));
      detailBody.append(
        el("p", "session-line", `真实会话：${value.actual_scope || "未知"} · 连接类型：${value.platform_name || "未知"}`),
        el("p", "session-line", `历史来源：${value.history_source || "未知"} · 对话：${value.conversation_id || "暂无"} · ${stamp(value.checked_at)}`));
      const reason = `${value.reason || ""}${value.reason_code ? `（${value.reason_code}）` : ""}`;
      if (reason) (value.allowed ? detailBody : sessionInfo).append(el("p", value.allowed ? "session-line" : "session-line danger-copy", reason));
      if (value.history_error) sessionInfo.append(el("p", "session-line danger-copy", value.history_error));
      if (attempt) {
        const sameInjection = injected && attempt.at === injected.at && attempt.turn_id === injected.turn_id && attempt.reason === injected.reason;
        (sameInjection ? detailBody : sessionInfo).append(el("p", "session-line", `最近处理：${stamp(attempt.at)} · ${attempt.reason}`));
      } else if (!injected) sessionInfo.append(el("p", "session-line", "尚未观察到实际聊天接入；配置检查通过不代表已经注入上下文。"));
      if (injected) sessionInfo.append(el("p", "session-line", `最近实际注入：${stamp(injected.at)} · ${injected.reason}`));
      const turn = attempt?.turn_id || injected?.turn_id;
      if (turn) {
        detailBody.append(el("p", "session-line", `对应聊天轮次：${turn}`));
        if (rows("debug_records").some((row) => row.turn_id === turn)) {
          const link = linkButton("查看这轮接入记录", `debug?turn=${encodeURIComponent(turn)}`);
          link.addEventListener("click", () => { debugCategory = ""; });
          debugLink.append(link);
        }
        else detailBody.append(el("p", "session-line", "该轮调试记录已清理、超出保留数量或当时未开启调试；接入状态仍保留。"));
      }
      sessionInfo.append(details);
    };
    const controls = append(el("div", "form-grid whitelist-fields"), connectionField, typeField, numberField, weightField);
    const entry = { original, originalUMO, connection, type, numberValue, box, controls, enabledField }; entries.push(entry);
    const currentScope = () => { const v = readFields(controls); return v.connection === connection && v.type === type && v.number === numberValue && originalUMO ? originalUMO : `${v.connection}:${v.type}:${v.number.trim()}`; };
    const update = () => { preview.textContent = `会话标识：${currentScope()} · 自动填写，无需手工拼接`; showStatus(rows("session_status").find((row) => row.umo === currentScope())); };
    controls.addEventListener("input", update); update();
    append(box, controls, append(el("div", "section-toolbar"), enabledField, button("移除这个对象", () => { entries.splice(entries.indexOf(entry), 1); box.remove(); dirty = true; }, "danger", true)));
    box.append(sessionInfo, append(el("div", "actions session-status-actions"), button("检查会话与历史", async () => {
      const result = await request(() => bridge.apiPost("action", { action: "inspect_session", scope: currentScope() }), "会话检查完成；没有调用模型或发送消息");
      if (result !== false) {
        snapshot.session_status = [...rows("session_status").filter((row) => row.umo !== result.umo), clone(result)];
        showStatus(result);
      }
    }, "secondary", true), debugLink));
    if (number.includes("_")) box.append(el("p", "session-line", "已有配置包含群成员会话标识；不改目标时保留该标识。聊天记录和人格判断仍使用真实场合。"));
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
  openEditor("编辑未开始的日程活动", [
    el("p", "hint", "只调整未开始活动的大纲。修改时间、内容或地点等信息后，旧细化归档并作废，取消尚未执行的行动决定，之后自动或手动重新细化。保存不调用模型、不发送消息。"),
    field("标题", "title", record.title || "", { required: true }),
    field("开始时间", "start", record.start || "", { required: true, hint: "填写 ISO 时间，保留时区。" }),
    field("结束时间", "end", record.end || "", { required: true }),
    field("活动内容", "content", record.content || record.description || "", { type: "textarea", required: true }),
    field("地点", "location", record.location || ""),
    field("睡眠状态", "sleep_state", record.sleep_state || "unknown", { options: [{ value: "unknown", label: "未知" }, { value: "awake", label: "清醒" }, { value: "asleep", label: "睡眠中" }] }),
  ], (patch) => action("update_activity", { id: record.id, patch }), "保存活动调整");
}
function detailActivity(record) {
  if (dirty) { notice("请先保存日程设置，再细化活动。", true); return; }
  const regenerate = Boolean(record.detailed);
  openEditor(regenerate ? "重新细化活动" : "细化活动", [
    el("p", "hint", "将真实调用模型，结合活动、相关记忆、今日安排、当前想法和发送限制，决定细节与是否安排新闻、搜索、主动聊天。可以全部不安排。本次调用不执行来源或发送消息；采用的未来行动将照常执行。"),
    el("p", "record-body", `${record.title} · ${clockTime(record.start)} — ${clockTime(record.end)}`),
    regenerate ? el("p", "muted", "合格后替换旧细化与尚未执行的行动决定；失败或活动已经开始时保留旧结果。内在状态和真实执行记录保留。") : null,
    field("本次要求（可选）", "instruction", "", { type: "textarea", rows: 4, placeholder: "例如：这次专心散步，不安排主动聊天。", hint: "只用于这次细化，不保存成公共模板，既有发送限制仍然生效。" }),
  ].filter(Boolean), (values) => action("detail_activity", { id: record.id, instruction: values.instruction.trim(), regenerate }), regenerate ? "调用模型并重新细化" : "调用模型并细化");
}
function activityDetail(record) {
  const box = el("div", "activity-detail");
  if (record.content && record.description && record.description !== record.content) box.append(el("p", "record-body", record.description));
  if (record.incident) box.append(el("p", "record-body", `日常小插曲：${record.incident}`));
  if (!record.detailed) box.append(el("p", "muted", "待细化：尚未决定新闻、搜索或主动聊天。"));
  if (record.detail_error) box.append(el("p", "hint warning", `细化失败：${record.detail_error}。${Number(record.detail_attempts || 0) >= 2 ? "自动重试已结束，活动开始前可手动重试。" : "自动细化最多尝试两次，间隔至少 60 秒，活动开始后不再重试。"}`));
  if (record.detailed) {
    const decisions = append(el("details", "activity-decisions"), el("summary", "", "细化决定与行动结果"));
    for (const [key, label] of [["news", "新闻"], ["search", "搜索"], ["social", "主动聊天"]]) {
      const item = record.actions?.[key]; if (!item) continue;
      const execution = item.execution || {};
      const row = append(el("div", "activity-decision"), el("strong", "", `${label} · ${item.enabled ? clockTime(item.at) : "不安排"}`));
      if (item.reason) row.append(el("p", "record-body", `判断理由：${item.reason}`));
      if (item.enabled && item.intent) row.append(el("p", "record-body", `执行意图：${item.intent}`));
      if (item.enabled || ![undefined, "disabled"].includes(execution.status)) row.append(badge(execution.status || "pending"));
      if (execution.reason || execution.result?.reason) row.append(el("p", "record-body", actionReason(execution.reason || execution.result.reason)));
      if (execution.result) row.append(details(execution.result, "实际执行结果"));
      decisions.append(row);
    }
    box.append(decisions);
  }
  const history = rows("detail_history").filter((item) => item.activity_id === record.id).sort((a, b) => String(b.archived_at).localeCompare(String(a.archived_at)));
  if (history.length) {
    const archives = append(el("details", "activity-detail-history"), el("summary", "", `细化历史 · ${history.length} 个版本`));
    history.forEach((item) => archives.append(details(item, `${stamp(item.archived_at) || "旧细化"}${item.reason ? ` · ${actionReason(item.reason)}` : ""}`)));
    box.append(archives);
  }
  return box;
}
function actionDate(value) {
  try { return new Intl.DateTimeFormat("en-CA", { timeZone: valueAt(snapshot.settings, "character.timezone", "Asia/Shanghai") }).format(new Date(value)); } catch { return String(value || "").slice(0, 10); }
}
function scheduleSummary(date) {
  const box = el("div", "schedule-counts");
  const usage = rows("action_usage").filter((item) => item.date === date);
  const history = [...rows("life_day_history").slice().reverse().flatMap((item) => item.activities || []), ...rows("detail_history").slice().reverse().map((item) => item.activity).filter(Boolean)];
  const usageDates = new Map(rows("action_usage").map((item) => [item.id, item.date]));
  const actual = new Map(rows("actions").map((item) => [item.id, item]));
  for (const [key, name] of [["news", "新闻"], ["search", "搜索"], ["social", "主动聊天"]]) {
    const recorded = [...new Map([...history, ...rows("activities")].map((item) => [item.actions?.[key]?.id || `${item.id}:${key}`, item])).values()];
    const identity = (item) => item.actions?.[key]?.id || `${item.id}:${key}`;
    const resultStatus = (item) => (usageDates.has(identity(item)) && actual.get(identity(item))?.status) || item.actions[key].execution?.status;
    const marked = recorded.filter((item) => item.actions?.[key] && (usageDates.get(identity(item)) || actionDate(item.actions[key].execution?.finished_at || item.actions[key].at || item.start)) === date);
    const scheduled = rows("activities").filter((item) => item.actions?.[key]?.enabled && actionDate(item.actions[key].at || item.start) === date);
    const success = marked.filter((item) => ["success", "succeeded", "sent", "completed", "done"].includes(resultStatus(item)));
    const failed = marked.filter((item) => ["failed", "interrupted", "unknown"].includes(resultStatus(item)));
    const skipped = marked.filter((item) => ["skipped", "expired", "cancelled"].includes(resultStatus(item)));
    const stored = date === dayNow() ? snapshot.day_summary?.counts?.[key] : null;
    const started = stored?.started ?? new Set(usage.filter((item) => item.kind === key).map((item) => item.id)).size;
    const arranged = stored?.arranged ?? scheduled.length;
    const unit = key === "social" ? "轮" : "次";
    const body = append(el("div", "metric"), el("div", "metric-label", `${name} · ${unit}`), el("div", "metric-value", `已开始 ${started} ${unit}`), el("div", "metric-foot", `已安排 ${arranged} ${unit}`), el("div", "metric-foot", `成功 ${stored?.success ?? success.length} · 失败 ${stored?.failed ?? failed.length} · 跳过 ${stored?.skipped ?? skipped.length}`));
    if (failed.length || skipped.length) body.append(details([...failed, ...skipped].map((item) => ({ activity: item.title, ...item.actions[key].execution })), "查看跳过与失败原因"));
    if (date !== dayNow()) body.append(el("div", "metric-foot", "已开始及结果按实际执行日期统计"));
    box.append(body);
  }
  return box;
}
function renderSchedule() {
  const root = el("div", "stack"); const settings = snapshot.settings || {};
  const form = settingsForm("保存日程设置", "每天自动生成活动大纲，临近活动时再细化并决定行动。保存设置不会调用模型。");
  const f = (label, key, options, fallback) => field(label, `life.${key}`, valueAt(settings, `life.${key}`, fallback), options);
  const numberOptions = { type: "number", min: 0, max: 48, step: 1 };
  form.append(card("大纲生成参数", "生成时间和活动数修改默认次日生效；保存后可用「重新生成日程」立即用于今天。大纲只安排活动，不分配行动数量。", append(el("div", "form-grid"), f("每日生成时间", "daily_plan_time", { type: "time", required: true }, "06:00"), f("每天活动数", "activity_count", { ...numberOptions, min: 1 }, 10), f("提前细化活动（分钟）", "detail_minutes", { type: "number", min: 0, max: 120 }, 10))));
  form.append(append(el("div", "actions"), linkButton("内在状态", "drives"), linkButton("聊天对象与白名单", "whitelist")), el("p", "hint", "每个活动每类最多一次，可以多类同时存在或全部不安排。细化只收到内在状态对应的当前想法；日程行动实际开始后扣值，失败不退还。一轮主动聊天可抽选多个不同对象，各对象仍受白名单、冷却、免打扰和发送上限约束。"));
  form.addEventListener("submit", (event) => {
    event.preventDefault(); const next = applyFields(clone(settings), form);
    saveSettings(next);
  });
  root.append(form);
  const date = el("input"); date.type = "date"; date.value = scheduleDate || dayNow(); date.setAttribute("aria-label", "日程日期"); date.style.width = "auto";
  date.addEventListener("change", () => { scheduleDate = date.value; render(); });
  const activities = orderedActivities(date.value);
  const editable = snapshot.day_regenerating ? [] : activities.filter((item) => item.status === "planned" && new Date(item.start).getTime() > Date.now());
  const batch = () => openEditor("批量调整未来活动", [el("p", "hint", "仅修改列出的未开始活动大纲，活动总数保持。大纲改变后，旧细化归档并作废，取消尚未执行的行动决定。保存不调用模型、不发送消息。"), field("活动调整 JSON", "json", stringify({ updates: editable.map((item) => ({ id: item.id, changes: { title: item.title, start: item.start, end: item.end, content: item.content || item.description || "", location: item.location || "", sleep_state: item.sleep_state || "unknown" } })) }), { type: "textarea", rows: 18 })], (values) => { try { return action("update_activities", JSON.parse(values.json)); } catch (error) { notice(`JSON 格式错误：${error.message}`, true); return false; } }, "保存未来活动调整");
  const regenerate = button("重新生成日程", () => {
    if (dirty) { notice("请先保存日程设置，再重新生成日程。", true); return; }
    confirmAction("重新生成今天的日程", "将真实调用模型，使用最新已保存参数和当前模板替换今天整份活动大纲。旧日程、细化和执行记录归档保留，失败时保留原日程。新活动在细化后决定未来行动；内在状态、白名单、发送次数和冷却限制不重置，过期行动不补做。", () => action("regenerate_day", { date: date.value }), false, "调用模型并重新生成");
  }, "primary");
  regenerate.dataset.disabled = String(date.value !== dayNow() || Boolean(snapshot.day_regenerating));
  const batchButton = button("批量调整未来活动", batch, "secondary");
  batchButton.dataset.disabled = String(Boolean(snapshot.day_regenerating) || !editable.length);
  const toolbar = append(el("div", "section-toolbar"), date, append(el("div", "actions"), regenerate, batchButton));
  const list = recordList(activities, { timeline: true, emptyTitle: "这一天还没有安排", emptyDescription: "到生成时间自动生成；也可手动重新生成今天的日程。", body: activityDetail, actions: (record) => editable.includes(record) ? append(el("div", "actions"), button("编辑", () => editActivity(record), "secondary", true), button(record.detailed ? "重新细化" : "细化活动", () => detailActivity(record), "secondary", true)) : null });
  root.append(card("日程与实际行动", "先生成大纲，再细化活动；细化决定是否安排新闻、搜索、主动聊天。按新闻 → 搜索 → 聊天执行，每项行动只执行一次。", append(el("div", "stack"), toolbar, snapshot.day_regenerating ? el("p", "hint", "正在重新生成日程：等待已有执行结束并生成新计划，期间暂停日程推进和活动编辑。完成后刷新查看结果。") : el("p", "muted", "重新生成日程保留内在状态。统计包含所选日期内被替换的旧版本实际行动；实际发送继续遵守白名单与发送限制。"), scheduleSummary(date.value), list)));
  const day = rows("life_days").find((item) => (item.date || item.day || item.id?.slice(0, 10)) === date.value && (!item.scope || item.scope === "global"));
  const original = day ? append(el("div", "stack"), details(day.parameters || day.params || {}, "当日采用的生成参数"), details(day.full_request || day.request || {}, "生成输入快照（非 API 原文）"), details(day.raw_json ?? day.raw_response ?? "升级前日程未记录模型生成文本", "模型生成的日程文本"), details(day.adopted_activities || day.adopted || activities, "校验后采用的日程"), jsonButtons(day, `living-world-schedule-${date.value}.json`)) : empty("尚无正式生成记录", "生成参数、输入快照和模型生成文本会随正式日程长期保存，不受调试保留次数限制。");
  const debugId = day?.full_request?._debug_record_id;
  const debugView = debugId ? rows("debug_views").find((view) => view.id === debugId || view.record_ids?.includes(debugId)) : null;
  const archiveLink = linkButton(debugId ? "查看这次调用的 API 原文" : "调试与调用记录", debugId ? `debug?turn=${encodeURIComponent(debugView?.id || debugId)}` : "debug");
  archiveLink.addEventListener("click", () => { debugCategory = ""; });
  const archives = el("div", "stack");
  for (const previous of rows("life_day_history").filter((item) => item.date === date.value).sort((a, b) => String(b.archived_at).localeCompare(String(a.archived_at)))) {
    const entry = append(el("details", "schedule-history"), el("summary", "", `历史版本 · ${previous.archived_at || "时间未记录"}`));
    const previousId = previous.full_request?._debug_record_id;
    append(entry, details(previous.parameters || {}, "该版本采用的生成参数"), details(previous.full_request || previous.request || {}, "该版本生成输入快照（非 API 原文）"), details(previous.raw_json ?? "旧版未记录模型生成文本", "该版本模型生成文本"), details(previous.adopted_activities || previous.activities || [], "该版本校验后采用的日程"), details(previous.activities || [], "替换前的活动与实际执行记录"), jsonButtons(previous, `living-world-schedule-${date.value}-${previous.id}.json`));
    if (previousId) {
      const previousView = rows("debug_views").find((view) => view.id === previousId || view.record_ids?.includes(previousId));
      const link = linkButton("查看该版本的 API 原文", `debug?turn=${encodeURIComponent(previousView?.id || previousId)}`);
      link.addEventListener("click", () => { debugCategory = ""; }); entry.append(link);
    }
    archives.append(entry);
  }
  root.append(card("正式日程生成档案", "保存当前日程和已归档历史版本。实际 API 请求与返回请到调试记录查看；调试记录过期后原文可能已清理。", append(el("div", "stack"), original, archives), archiveLink));
  root.append(card("当前生活状态", "调整状态只保存生活数据，不触发模型调用或真实消息。精力与寂寞值请到「内在状态」调整。", details(snapshot.state || {}, "查看状态数据"), button("调整当前状态", () => openEditor("调整当前状态", [field("心情", "mood", snapshot.state?.mood || "平静"), field("当前作息", "routine", snapshot.state?.routine || "", { type: "textarea" })], (patch) => action("update_state", { patch })), "secondary", true)));
  return root;
}

function driveDraft(id) {
  const meter = snapshot.drives.meters[id];
  if (!driveDrafts.has(id)) driveDrafts.set(id, { configDirty: false, valueDirty: false });
  const draft = driveDrafts.get(id);
  if (!draft.configDirty) draft.config = clone(meter.config);
  if (!draft.valueDirty) draft.value = String(meter.value);
  return draft;
}
function hasDriveDrafts() {
  return [...driveDrafts.values()].some((draft) => draft.configDirty || draft.valueDirty);
}
function driveStatusCards() {
  const box = el("div", "drive-status-cards"); box.id = "drive-status-cards";
  const active = Boolean(snapshot.drives?.enabled);
  for (const [id, name] of Object.entries(driveNames)) {
    const meter = snapshot.drives?.meters?.[id]; if (!meter) continue;
    const body = append(el("div", "drive-summary"), el("div", "drive-value", `${meter.display_value} / 100`), el("p", "muted", `当前阶段 ${meter.stage.min}—${meter.stage.max}`));
    const progress = el("progress"); progress.max = 100; progress.value = meter.display_value; progress.setAttribute("aria-label", `${name}当前值`); body.append(progress);
    append(body, el("p", "field-label", active ? "下次细化会收到的文案" : "当前阶段文案（暂停注入）"), el("p", "drive-thought", active ? meter.thought : meter.stage.text));
    if (!active) body.append(el("p", "muted", "模块已暂停，下次细化不注入这项想法。"));
    const item = card(name, "", body, badge(active ? "enabled" : "paused")); item.dataset.drive = id; box.append(item);
  }
  return box;
}
function updateDriveStatusCards() {
  $("#drive-status-cards")?.replaceWith(driveStatusCards());
}
async function saveDrive(id, name, payload, committed) {
  return request(async () => {
    const result = await bridge.apiPost("action", { action: name, id, ...payload });
    snapshot.drives = result;
    snapshot.settings.drives = Object.fromEntries(Object.entries(result.meters).map(([key, meter]) => [key, clone(meter.config)]));
    snapshot.settings.modules.drives = result.enabled;
    committed(); render();
    return result;
  }, name === "set_drive_value" ? "当前值已应用，参数与阶段草稿继续保留" : "参数与阶段已保存，当前值继续按运行规则变化");
}
function renderDrives() {
  const root = el("div", "stack drives-page");
  if (!snapshot.drives?.meters) return append(root, empty("内在状态暂不可用", "请确认插件已更新并刷新页面。"));
  root.append(driveStatusCards());
  const toolbar = append(el("div", "section-toolbar"), el("p", "muted", "两项数值由程序管理；AI 只在细化时看到阶段文案，不会收到数值和计算规则。"), linkButton("模块启停设置", "settings"));
  root.append(toolbar);
  const tabs = el("div", "debug-tabs drive-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "内在状态设置");
  for (const [id, name] of Object.entries(driveNames)) {
    const tab = button(name, () => { selectedDrive = id; render(); $("#drive-tab-" + id)?.focus(); });
    tab.id = `drive-tab-${id}`; tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", String(id === selectedDrive)); tab.setAttribute("aria-controls", "drive-editor"); tab.tabIndex = id === selectedDrive ? 0 : -1;
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); selectedDrive = event.key === "Home" ? "loneliness" : event.key === "End" ? "energy" : id === "loneliness" ? "energy" : "loneliness"; render(); $("#drive-tab-" + selectedDrive)?.focus();
    }); tabs.append(tab);
  }
  root.append(tabs);
  const id = selectedDrive; const draft = driveDraft(id);
  const panel = el("div", "stack drive-editor"); panel.id = "drive-editor"; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `drive-tab-${id}`);
  const markDraft = () => { draft.configDirty = true; dirty = true; };
  const currentForm = el("form", "drive-current-form");
  const current = field(`${driveNames[id]}当前值`, "drive.value", draft.value, { type: "number", min: 0, max: 100, step: "any", required: true });
  const currentInput = current.querySelector("input");
  currentInput.addEventListener("input", () => { draft.value = currentInput.value; draft.valueDirty = true; dirty = true; });
  const apply = button("应用当前值", () => {}, "primary"); apply.type = "submit";
  currentForm.addEventListener("submit", (event) => {
    event.preventDefault(); const submitted = currentInput.value;
    saveDrive(id, "set_drive_value", { value: Number(submitted) }, () => { if (draft.value === submitted) draft.valueDirty = false; });
  });
  append(currentForm, current, apply, el("p", "muted", "0—100，可填小数；仅应用当前值。"));
  const currentCard = append(el("section", "card drive-current-card"), currentForm);
  currentCard.setAttribute("aria-label", `调整${driveNames[id]}`); panel.append(currentCard);
  const configForm = el("form", "stack drive-config-form");
  const rateFields = el("div", "drive-rate-fields");
  const rate = field("每在线小时增加", "drive.growth_per_hour", draft.config.growth_per_hour, { type: "number", min: 0, step: "any", required: true });
  rate.querySelector("input").addEventListener("input", (event) => { draft.config.growth_per_hour = event.target.value; markDraft(); });
  rateFields.append(rate);
  for (const [kind, value] of Object.entries(draft.config.costs)) {
    const labels = { social: "每轮主动聊天减少", news: "每次新闻阅读减少", search: "每次主动搜索减少" };
    const cost = field(labels[kind] || kind, `drive.costs.${kind}`, value, { type: "number", min: 0, step: "any", required: true });
    cost.querySelector("input").addEventListener("input", (event) => { draft.config.costs[kind] = event.target.value; markDraft(); }); rateFields.append(cost);
  }
  const rules = append(el("details", "drive-rules"), el("summary", "", "计算与生效规则"),
    el("p", "", "按在线时间连续增长，离线或模块暂停时不增长；增长与消耗可设为 0。当前值可填小数，显示和阶段匹配取整数部分。"),
    el("p", "", "仅日程行动实际开始时扣值，失败不退；不足时仍可执行，最低归零。聊天一轮多对象只扣一次；手动来源、试跑、被动回复、群聊插话和独立 AI 日报不扣。"),
    el("p", "", "应用当前值与保存参数分别生效，不调用模型。已有细化保持原决定，需要立即采用新想法时手动重新细化。"));
  configForm.append(rateFields, rules);
  const stages = el("div", "drive-stages");
  function renderStages(focusIndex = -1) {
    stages.replaceChildren();
    draft.config.stages.forEach((stage, index) => {
      const min = index ? draft.config.stages[index - 1].max + 1 : 0;
      const stageRow = el("section", "drive-stage"); stageRow.dataset.index = String(index);
      const split = button("＋", () => {
        const oldMax = stage.max; stage.max = Math.floor((min + oldMax) / 2);
        draft.config.stages.splice(index + 1, 0, { max: oldMax, text: stage.text }); markDraft(); renderStages(index + 1);
      }, "secondary", true);
      split.setAttribute("aria-label", `拆分第 ${index + 1} 阶段`); split.dataset.disabled = String(min === stage.max); split.disabled = min === stage.max;
      const remove = button("删除", () => {
        if (index) draft.config.stages[index - 1].max = stage.max;
        draft.config.stages.splice(index, 1); markDraft(); renderStages(Math.max(0, index - 1));
      }, "secondary", true);
      remove.setAttribute("aria-label", `删除第 ${index + 1} 阶段`); remove.dataset.disabled = String(draft.config.stages.length === 1); remove.disabled = draft.config.stages.length === 1;
      const title = el("h3", "drive-stage-title", `阶段 ${index + 1} · ${min}—${stage.max}`);
      const boundary = field("结束值", `drive.stage.${index}.max`, stage.max, { type: "number", min, max: index === draft.config.stages.length - 1 ? 100 : draft.config.stages[index + 1].max - 1, step: 1, required: true, readOnly: index === draft.config.stages.length - 1 });
      boundary.classList.add("drive-stage-boundary");
      boundary.querySelector("input").title = index === draft.config.stages.length - 1 ? "最后一段固定到 100" : "修改后，下一段起始值自动跟随";
      boundary.querySelector("input").addEventListener("change", (event) => {
        const value = Number(event.target.value);
        if (!event.target.reportValidity() || !Number.isInteger(value)) { event.target.value = stage.max; return; }
        stage.max = value; markDraft(); renderStages(index);
      });
      const meaning = field("注入的想法", `drive.stage.${index}.text`, stage.text, { type: "textarea", rows: 1, required: true });
      meaning.classList.add("drive-stage-meaning");
      meaning.querySelector("textarea").addEventListener("input", (event) => { stage.text = event.target.value; markDraft(); });
      append(stageRow, title, boundary, meaning, append(el("div", "actions drive-stage-actions"), split, remove)); stages.append(stageRow);
    });
    if (focusIndex >= 0) stages.querySelector(`[data-index="${focusIndex}"] textarea`)?.focus();
  }
  renderStages();
  configForm.append(heading("阶段与想法", "修改结束值会联动下一段，最后一段固定到 100。＋ 拆分；删除并入低一档，最低档并入下一档。“必须聊天”仅为倾向，AI 自行判断。"), stages);
  const save = button("保存设置", () => {}, "primary"); save.type = "submit";
  configForm.append(append(el("div", "form-actions"), save));
  configForm.addEventListener("submit", (event) => {
    event.preventDefault(); const before = stringify(draft.config); const config = clone(draft.config);
    config.growth_per_hour = Number(config.growth_per_hour); for (const key of Object.keys(config.costs)) config.costs[key] = Number(config.costs[key]);
    saveDrive(id, "save_drive_settings", { config }, () => { if (stringify(draft.config) === before) draft.configDirty = false; });
  });
  const configCard = card("增长、消耗与阶段", "参数单独保存，切换和刷新保留草稿。", configForm);
  configCard.classList.add("drive-config-card"); panel.append(configCard);
  root.append(panel); dirty = hasDriveDrafts();
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
  const limits = settingsForm("保存上下文用量", "从下一次组装上下文生效，不调用模型、不删除记忆。已发出的请求和历史调试保持原样。");
  limits.id = "memory-context-settings";
  limits.addEventListener("submit", (event) => { event.preventDefault(); saveSettings(applyFields(clone(snapshot.settings), limits)); });
  const memorySettings = snapshot.settings?.memory || {};
  limits.append(card("上下文用量", "用于聊天、日程生成、调整、细化及自动试跑资料中的相关记忆。日记／笔记占总条数，不额外叠加；实际命中可能更少。", append(el("div", "form-grid memory-context-fields"),
    field("相关记忆总条数", "memory.context_limit", memorySettings.context_limit ?? 10, { type: "number", min: 0, max: 50, step: 1, hint: "默认 10，范围 0—50；0 表示不注入相关记忆和人物认知。" }),
    field("其中日记／笔记最多", "memory.journal_limit", memorySettings.journal_limit ?? 2, { type: "number", min: 0, max: 50, step: 1, hint: "默认 2，范围 0—50；0 表示不注入日记／笔记简报。" }),
    field("每份简报最多字符", "memory.brief_max_chars", memorySettings.brief_max_chars ?? 200, { type: "number", min: 50, max: 1000, step: 1, hint: "默认 200，范围 50—1000；含标点。降低后立即限制旧简报的注入长度。" })
  )));
  root.append(limits, el("p", "hint", "这里限制相关记忆区；日程、近期经历、见闻和宿主聊天历史是其他资料区，不计入这些条数。日记／笔记全文仍可在「新闻、搜索与日记」查看；旧记录没有简报时不注入，可在那里点击「生成简报」。含虚构日常的日记简报仅在对应角色日期召回。"));
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
  const generate = () => openEditor("生成日记或笔记", [el("p", "hint", "先调用模型生成并保存正文，再调用一次模型提炼上下文简报，不发送消息。简报失败保留正文，可单独重试。"), field("日期", "date", dayNow(), { type: "date", required: true }), field("类型", "kind", "journal", { options: [{ value: "journal", label: "生活日记" }, { value: "note", label: "见闻笔记" }] }), field("所属场合", "scope", "global", { options: scopeOptions(), hint: "只使用该场合允许回顾的内容。" })], (values) => action("generate_journal", values), "调用 AI 并保存记录");
  const entries = el("div", "stack"); entries.id = "journal-entries";
  for (const record of rows("entries")) {
    const preview = record.summary ? el("p", "record-body", record.summary) : el("p", "hint", record.summary_status === "failed" ? "简报生成失败，正文已保留；当前不注入这篇全文，可点击生成简报重试。" : "尚无简报，当前不注入这篇全文。点击生成简报后可参与召回。");
    const actions = append(el("div", "actions"), button(record.summary ? "重新生成简报" : "生成简报", () => confirmAction("生成上下文简报", "调用一次模型提炼这篇原文并保存简报，不改正文、不发送消息。失败保留已有简报；适用记忆开关、用量和场合限制。", () => action("summarize_journal", { id: record.id, regenerate: Boolean(record.summary) }), false, "调用 AI 生成简报"), "secondary", true), button("删除记录", () => confirmAction("删除这篇记录", "删除选中的日记或笔记及其派生记忆，不删除原始见闻，不调用模型。", () => action("delete_entry", { id: record.id }), true, "删除记录"), "danger", true));
    entries.append(append(el("article", "record"), append(el("div", "record-head"), append(el("div"), el("h3", "record-title", `${record.day || ""} ${kindNames[record.kind] || "日记"}`), el("p", "hint", `所属场合：${scopeLabel(record.scope)}`)), actions), preview, append(el("details"), el("summary", "", "查看完整正文"), el("div", "record-body", record.text || ""))));
  }
  if (!rows("entries").length) entries.append(empty());
  append(root, append(el("div", "grid"), card("日记与见闻笔记", "正文与简报分别保存；注入条数和简报长度在「记忆与人物 → 上下文用量」设置。", entries, button("生成日记或笔记", generate, "secondary", true)), card("天气与 B 站见闻", "搜索、已观看与历史记忆分别标明。", observationList(rows("observations").filter((item) => !observationKind(item, "news") && !observationKind(item, "search") && !observationKind(item, "daily_digest"))))));
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
function formatDebugJSON(raw, prefix = "", ending = "\n") {
  try { JSON.parse(raw); } catch { return null; }
  // Validate without reserializing: duplicate keys and numeric spelling are evidence.
  const tokens = raw.match(/"(?:\\[\s\S]|[^"\\])*"|[{}\[\],:]|[^\s{}\[\],:]+/g) || [];
  const parts = [prefix], folds = [], stack = [];
  let depth = 0, offset = prefix.length, lineStart = 0;
  const write = (...text) => {
    parts.push(...text);
    for (const part of text) {
      const lineBreak = Math.max(part.lastIndexOf("\n"), part.lastIndexOf("\r"));
      if (lineBreak >= 0) lineStart = offset + lineBreak + 1;
      offset += part.length;
    }
  };
  const newline = () => `${ending}${prefix}${"  ".repeat(depth)}`;
  tokens.forEach((token, index) => {
    if (token === "{" || token === "[") {
      const nonempty = tokens[index + 1] !== (token === "{" ? "}" : "]");
      const fold = nonempty ? { start: offset, lineStart, end: 0, children: [] } : null;
      if (fold) (stack.at(-1)?.children || folds).push(fold);
      stack.push(fold); write(token); depth++;
      if (nonempty) write(newline());
    } else if (token === "}" || token === "]") {
      depth--;
      if (tokens[index - 1] !== (token === "}" ? "{" : "[")) write(newline());
      const fold = stack.pop(); if (fold) fold.end = offset;
      write(token);
    } else if (token === ",") write(token, newline());
    else if (token === ":") write(": ");
    else if (token.startsWith('"')) {
      // Consume complete escapes so literal backslashes (including paths) stay intact.
      const text = token.replace(/\\(?:u[\da-fA-F]{4}|[\s\S])/g, (escape) => {
        if (escape === "\\n" || /^\\u000a$/i.test(escape)) return "\n";
        if (escape === "\\r" || /^\\u000d$/i.test(escape)) return "\r";
        return escape;
      });
      write(text.replace(/\r\n?|\n/g, newline));
    } else write(token);
  });
  return { text: parts.join(""), folds };
}
function formatDebugBody(raw, type) {
  const plain = (text) => ({ text, folds: [] });
  if (type !== "sse") return [formatDebugJSON(raw) ?? plain(raw)];
  const output = [];
  let event = [];
  const formatEvent = () => {
    const data = event.filter((line) => /^data(?::|$)/.test(line.text));
    const formatted = data.length ? formatDebugJSON(data.map((line) => line.text.replace(/^data(?:: ?)?/, "")).join("\n"), "data: ", data[0].ending) : null;
    if (formatted === null) return [plain(event.map((line) => line.raw).join(""))];
    // Format data within one complete event, retaining metadata and event boundaries.
    return event.flatMap((line) => {
      if (line === data[0]) return [{ ...formatted, text: formatted.text + line.ending }];
      return /^data(?::|$)/.test(line.text) ? [] : [plain(line.raw)];
    });
  };
  for (const line of raw.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g) || []) {
    if (!line) continue;
    const ending = line.match(/(?:\r\n|\r|\n)$/)?.[0] || "";
    const text = ending ? line.slice(0, -ending.length) : line;
    if (text === "" && ending) { output.push(...formatEvent(), plain(line)); event = []; }
    else event.push({ text, ending, raw: line });
  }
  // An event without a terminating blank line may still be in flight.
  output.push(plain(event.map((line) => line.raw).join("")));
  return output;
}
function renderDebugBody(parts, state, idPrefix) {
  const root = el("span", "debug-json-document"), handles = [];
  parts.forEach((part, partIndex) => {
    // Iterative rendering also handles deeply nested input without recursive DOM builders.
    const pending = [{ parent: root, start: 0, end: part.text.length, folds: part.folds }];
    while (pending.length) {
      const frame = pending.pop();
      let cursor = frame.start;
      for (const fold of frame.folds) {
        frame.parent.append(document.createTextNode(part.text.slice(cursor, fold.lineStart)));
        const key = `${partIndex}-${fold.start}`;
        const node = el("span", "debug-json-node"); node.dataset.fold = key;
        const toggle = el("button", "debug-json-toggle"); toggle.type = "button";
        const children = el("span", "debug-json-children"); children.id = `${idPrefix}-${key}`;
        const ellipsis = el("span", "debug-json-ellipsis");
        const kind = part.text[fold.start] === "{" ? "对象" : "数组";
        toggle.setAttribute("aria-controls", children.id);
        const setCollapsed = (collapsed) => {
          if (collapsed) state.collapsed.add(key); else state.collapsed.delete(key);
          children.hidden = collapsed; ellipsis.textContent = collapsed ? "…" : "";
          toggle.setAttribute("aria-expanded", String(!collapsed));
          toggle.setAttribute("aria-label", `${collapsed ? "展开" : "收起"}${kind}`);
          toggle.title = `${collapsed ? "展开" : "收起"}${kind}`;
        };
        toggle.addEventListener("click", () => setCollapsed(!children.hidden));
        setCollapsed(state.collapsed.has(key)); handles.push(setCollapsed);
        append(node, toggle, document.createTextNode(part.text.slice(fold.lineStart, fold.start + 1)), children, ellipsis, document.createTextNode(part.text[fold.end]));
        frame.parent.append(node);
        pending.push({ parent: children, start: fold.start + 1, end: fold.end, folds: fold.children });
        cursor = fold.end + 1;
      }
      frame.parent.append(document.createTextNode(part.text.slice(cursor, frame.end)));
    }
  });
  return { root, handles };
}
function debugBodyViewer(raw, type, selected, direction, callKey) {
  const viewer = el("div", "debug-body-viewer");
  const controls = el("div", "actions"); controls.setAttribute("role", "group");
  controls.setAttribute("aria-label", `${direction === "request" ? "请求" : "返回"}正文显示方式`);
  const note = el("p", "muted", "格式化仅供阅读：点击左侧箭头可收起对象或数组，正文保留换行；复制、下载始终保留完整原文。无法格式化的内容按原文展示。");
  const pre = el("pre", "debug-raw");
  selected.bodyModes ??= {};
  selected.bodyFolds ??= new Map();
  const stateKey = JSON.stringify([callKey, direction]);
  let state = selected.bodyFolds.get(stateKey);
  if (!state || state.raw !== raw || state.type !== type) {
    state = { raw, type, collapsed: new Set() }; selected.bodyFolds.set(stateKey, state);
  }
  let formatted;
  const idPrefix = `debug-json-${++debugBodySequence}`;
  const foldControls = append(el("div", "actions debug-fold-controls"),
    button("全部展开", () => formatted?.handles.forEach((setCollapsed) => setCollapsed(false)), "secondary", true),
    button("全部收起", () => formatted?.handles.forEach((setCollapsed) => setCollapsed(true)), "secondary", true));
  const choices = [["formatted", "格式化显示"], ["raw", "原文"]].map(([mode, label]) => {
    const choice = button(label, () => { selected.bodyModes[direction] = mode; update(); }, "secondary", true);
    controls.append(choice); return { mode, choice };
  });
  const update = () => {
    const mode = selected.bodyModes[direction] || "formatted";
    choices.forEach(({ mode: value, choice }) => choice.setAttribute("aria-pressed", String(value === mode)));
    if (mode === "formatted") {
      formatted ??= renderDebugBody(formatDebugBody(raw, type), state, idPrefix);
      pre.replaceChildren(formatted.root);
    } else pre.textContent = raw;
    const hasFolds = mode === "formatted" && Boolean(formatted?.handles.length);
    foldControls.hidden = !hasFolds; pre.classList.toggle("has-folds", hasFolds);
    pre.dataset.mode = mode;
    note.hidden = mode === "raw";
  };
  append(viewer, controls, foldControls, note, pre); update(); return viewer;
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
  selected.sourceOpen ??= new Map();
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
    panel.querySelectorAll(".debug-source").forEach((item) => selected.sourceOpen.set(item.dataset.sourceKey, item.open));
    buttons.forEach((tab, index) => { tab.setAttribute("aria-selected", String(index === selected.tab)); tab.tabIndex = index === selected.tab ? 0 : -1; });
    panel.setAttribute("aria-labelledby", buttons[selected.tab].id); panel.replaceChildren();
    if (selected.tab === 0) {
      panel.append(el("p", "muted", "这里展示组装时记录的信息来源与实际选用资料；最终发往接口的内容以②为准。"));
      const sources = el("div", "debug-sources");
      (view.sources || []).forEach((source, index) => {
        const key = JSON.stringify([index, source.title, source.source, source.placement]);
        const item = el("details", "debug-source");
        item.dataset.sourceKey = key;
        const summary = append(el("summary", "debug-source-summary"), el("span", "debug-source-title", source.title || "上下文资料"), el("span", "debug-source-origin", `来源：${source.source || "未记录"}${source.placement ? ` · 放入：${source.placement}` : ""}`));
        item.open = selected.sourceOpen.get(key) ?? true;
        item.addEventListener("toggle", () => { if (item.isConnected) selected.sourceOpen.set(key, item.open); });
        sources.append(append(item, summary, append(el("div", "debug-source-content"), readableValue(source.content))));
      });
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
        append(panel, rawBodyButtons(raw, `living-world-${safeId}-${isRequest ? "request" : "response"}.${type === "sse" ? "sse" : type === "json" ? "json" : "txt"}`, isRequest ? "request" : "response", type), debugBodyViewer(raw, type, selected, isRequest ? "request" : "response", call.id ?? selected.call));
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
  const priorTemplates = rows("prompt_template_history");
  if (priorTemplates.length) {
    const archived = append(el("details", "card template-history"), el("summary", "", `升级前的提示词模板 · ${priorTemplates.length} 份`), el("p", "hint", "升级时备份的旧指令，只供核对。当前日程使用匹配新流程的模板；这里的文本不会自动重新启用。"));
    priorTemplates.forEach((item) => archived.append(details(item.template ?? item, `${debugLabel(item.task || item.id)}${item.archived_at ? ` · ${stamp(item.archived_at)}` : ""}`)));
    root.append(archived);
  }
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
  const renderers = { whitelist: renderWhitelist, sources: renderSources, debug: renderDebug, overview: renderOverview, settings: renderSettings, drives: renderDrives, schedule: renderSchedule, memory: renderMemory, journal: renderJournal, social: renderSocial, data: renderData };
  content.replaceChildren(renderers[view]());
  if (view === "overview") { const timeline = content.querySelector(".home-timeline"); const current = timeline?.querySelector(".is-current"); if (current) timeline.scrollTop = Math.max(0, current.offsetTop - timeline.offsetTop - 60); }
  if (lastResult && view !== "settings") content.firstChild.append(card("最近一次操作结果", `${lastResult.time} · ${lastResult.action}`, details(lastResult.result, "查看后端执行结果")));
  setBusy(busy);
}
window.addEventListener("hashchange", () => { dirty = false; render(); });
window.addEventListener("beforeunload", (event) => { if (dirty || hasDriveDrafts()) { event.preventDefault(); event.returnValue = ""; } });
$("#navigation").addEventListener("click", (event) => {
  const link = event.target.closest("a[data-view]");
  if (!link || !dirty || location.hash === "#drives") return;
  event.preventDefault();
  confirmAction("离开尚未保存的设置", "本页的修改尚未保存，离开后需要重新填写。", () => { dirty = false; location.hash = link.dataset.view; }, false, "放弃修改并离开");
});
$("#refresh").addEventListener("click", () => {
  if (dirty && location.hash !== "#drives") confirmAction("刷新并放弃未保存的修改", "刷新会重新读取后端数据，覆盖当前未保存的设置。", refresh, false, "刷新");
  else refresh();
});
try {
  if (!bridge) throw new Error("请从 AstrBot 插件详情中的 Pages 打开此页面，以连接插件后端。");
  await bridge.ready();
  await refresh();
  setInterval(async () => {
    if (busy || document.hidden || location.hash !== "#drives") return;
    setBusy(true);
    try { await readState(); updateDriveStatusCards(); } catch (error) { notice(`数值刷新失败：${error.message}`, true); } finally { setBusy(false); }
  }, 60000);
} catch (error) {
  $("#connection").textContent = "连接不可用";
  content.replaceChildren(empty("暂时无法连接", error.message));
  notice(error.message, true);
}
