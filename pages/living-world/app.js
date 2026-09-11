import { PAGES, resolveRoute, routeHash, reorderedLayout } from "./navigation.js";
import { createMemoryBrowser } from "./memory-browser.js";
const bridge = window.AstrBotPluginPage;
const $ = (selector) => document.querySelector(selector);
const content = $("#content");
const moduleNames = { daily_digest: "AI 日报", debug: "调试记录", life: "日程生活", state: "角色状态", drives: "内在状态", memory: "长期记忆", reply: "被动回复", interjection: "群聊插话", proactive: "主动社交", news: "新闻阅读", search: "主动搜索", weather: "天气", bilibili: "B 站见闻", journal: "生活日记", notes: "见闻笔记" };
const kindNames = { knowledge: "知识", event: "事件", skill: "技能", emotional: "情感与体会", profile: "人物认知", journal: "日记", note: "笔记", notes: "笔记", news: "新闻", search: "搜索", weather: "天气", bilibili: "B 站搜索", bilibili_watch: "观看 B 站视频", bilibili_recent: "读取 B 站历史见闻", fiction: "角色日常", life: "生活", social: "社交", read: "已读取", searched: "已搜索", watched: "已观看" };
const statusNames = { enabled: "已开启", disabled: "已关闭", ready: "就绪", running: "运行中", paused: "已暂停", unavailable: "不可用", error: "异常", failed: "失败", planned: "已计划", pending: "待执行", reserved: "已预留", detailed: "已细化", completed: "已完成", done: "已完成", sent: "已发送", skipped: "已跳过", cancelled: "已取消", expired: "已过期", success: "成功", succeeded: "成功", ok: "正常" };
let snapshot = null;
let busy = false;
let lastResult = null;
let dialogHandler = null;
let modalDraft = false;
let scheduleDate = "";
let debugCategory = "";
let retentionDraft = null;
let layoutDraft = null;
let layoutTask = "chat.group";
const templateDrafts = new Map();
const formDrafts = new Map();
const arrayDrafts = new Map();
const selections = new Map();
const listStates = new Map();
let debugPageCache = null;
const rememberedTabs = {};
const scrollPositions = new Map();
let currentRoute = resolveRoute(location.hash);
let sourceReturn = null;
let templateTask = "life.plan";
let debugRound = "";
let pendingBackup = null;
let cancelLayoutDrag = null;
let draftSequence = 0;
function newDraftId() { return globalThis.crypto?.randomUUID?.() || `draft-${Date.now()}-${++draftSequence}`; }

function hasDrafts() {
  return modalDraft || Boolean(pendingBackup) || memoryBrowser.hasDrafts() || hasDebugDrafts() || hasDriveDrafts() || formDrafts.size > 0 || [...arrayDrafts.values()].some((item) => item.changed);
}
function captureFields(form) {
  const values = {};
  form.querySelectorAll("input[name],select[name],textarea[name]").forEach((input) => {
    if (input.dataset.skip || input.readOnly) return;
    values[input.name] = { value: input.type === "checkbox" ? input.checked : input.value, number: input.dataset.number === "true", lines: Boolean(input.dataset.lines) };
  });
  return values;
}
function draftedValue(base, key) {
  const value = clone(base);
  for (const [name, item] of Object.entries(formDrafts.get(key) || {})) {
    setAt(value, name, item.number ? Number(item.value) : item.lines ? item.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean) : item.value);
  }
  return value;
}
function bindDraft(form, key) {
  form.dataset.draftKey = key;
  for (const input of form.querySelectorAll("input[name],select[name],textarea[name]")) {
    const item = formDrafts.get(key)?.[input.name];
    if (item) { if (input.type === "checkbox") input.checked = item.value; else input.value = item.value; }
  }
  const capture = (event) => {
    if (event.target.dataset.skip || !event.target.name) return;
    formDrafts.set(key, captureFields(form));
  };
  form.addEventListener("input", capture); form.addEventListener("change", capture);
  return form;
}
function finishForm(form, key, save = (values) => saveSettings(values)) {
  bindDraft(form, key);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitted = JSON.stringify(formDrafts.get(key));
    const values = readFields(form);
    const result = await save(values);
    if (result !== false) { if (JSON.stringify(formDrafts.get(key)) === submitted) formDrafts.delete(key); render(); }
  });
  return form;
}
function chooser(label, key, options, fallback = "", change = null) {
  let value = selections.get(key) ?? fallback;
  if (options.length && !options.some((item) => String(item.value ?? item) === String(value))) value = options[0].value ?? options[0];
  selections.set(key, value);
  const wrapper = field(label, key, value, { options });
  const select = wrapper.querySelector("select"); select.dataset.skip = "1";
  select.addEventListener("change", () => { selections.set(key, select.value); if (change) change(select.value); else render(); });
  return wrapper;
}
function arraySignature(key) {
  const state = arrayDrafts.get(key);
  return JSON.stringify({ changed: state?.changed, keys: state?.items.map((item) => item.key), forms: [...formDrafts].filter(([name]) => name.startsWith(key + ":")) });
}
function clearArray(key) {
  arrayDrafts.delete(key);
  for (const name of formDrafts.keys()) if (name.startsWith(key + ":")) formDrafts.delete(name);
}
function arrayEditor(key, records, options) {
  let state = arrayDrafts.get(key);
  if (!state) { state = { changed: false, items: records.map((value, index) => ({ key: String(value.id || value.umo || index), value: clone(value) })) }; arrayDrafts.set(key, state); }
  else if (!state.changed && ![...formDrafts.keys()].some((name) => name.startsWith(key + ":"))) {
    const fresh = records.map((value, index) => ({ key: String(value.id || value.umo || index), value: clone(value) }));
    state.items = fresh;
  }
  const base = (item) => options.base ? options.base(item.value) : item.value;
  const valueOf = (item) => draftedValue(base(item), key + ":" + item.key);
  const serialize = (item) => options.serialize ? options.serialize(valueOf(item), item.value) : { ...item.value, ...valueOf(item) };
  const form = settingsForm(options.saveLabel || "保存列表设置", options.description || "保存本面板的列表及设置，不调用模型或发送消息。");
  const choices = state.items.map((item, index) => ({ value: item.key, label: options.label(valueOf(item), index) }));
  const select = chooser(options.selectLabel, key + ".selection", choices);
  const selected = () => state.items.find((item) => item.key === selections.get(key + ".selection"));
  const actions = form.querySelector(".section-toolbar");
  if (options.create) actions.append(button("新增", () => {
    const value = options.create(); const item = { key: newDraftId(), value };
    state.items.push(item); state.changed = true; selections.set(key + ".selection", item.key); render();
  }, "secondary"));
  const remove = button("删除所选", () => { const item = selected(); if (!item) return; state.items = state.items.filter((entry) => entry !== item); formDrafts.delete(key + ":" + item.key); state.changed = true; render(); }, "danger");
  remove.dataset.disabled = String(!choices.length); actions.append(remove);
  if (options.restore) actions.append(button("恢复默认", () => confirmAction("恢复默认列表", "将恢复本来源的默认列表，已有见闻保留，不发起来源读取。", async () => {
    const result = await action(options.restore); if (result !== false) { clearArray(key); render(); } return result;
  }, false, "恢复默认"), "secondary"));
  form.append(select);
  if (options.extra) form.append(finishInlineFields(options.extra(), key + ":extra"));
  const item = selected();
  if (item) {
    const fields = options.fields(base(item), item.value); bindDraft(fields, key + ":" + item.key); fields.refresh?.(); form.append(fields);
  } else form.append(empty("没有配置对象", options.create ? "点击上方新增按钮添加。" : "可恢复默认列表。"));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const values = state.items.map(serialize);
      if (options.validate) options.validate(values);
      const submitted = arraySignature(key);
      const selectedIndex = state.items.indexOf(selected());
      const result = await saveSettings(options.payload(values, draftedValue({}, key + ":extra")));
      if (result !== false) {
        if (arraySignature(key) === submitted) {
          const saved = values[selectedIndex]; clearArray(key);
          if (saved) selections.set(key + ".selection", String(saved.id || saved.umo || selectedIndex));
        }
        render();
      }
    } catch (error) { notice(error.message, true); }
  });
  return form;
}
function finishInlineFields(node, key) { return bindDraft(node, key); }
function openRecord(title, body) { openEditor(title, body, () => true, "关闭"); }
function compactRecords(key, records, options = {}) {
  const root = el("div", "stack compact-records");
  const state = listStates.get(key) || { query: "", page: 0 }; listStates.set(key, state);
  const search = field("搜索记录", key + ".search", state.query, { placeholder: options.searchHint || "搜索标题或正文" });
  const controls = append(el("div", "compact-filter"), search);
  if (options.filter) controls.append(options.filter);
  root.append(controls);
  const list = el("div", "compact-record-list"); const pager = el("div", "pagination"); root.append(pager, list);
  let searchedQuery = null, searchedRows = records;
  const searchTexts = new WeakMap();
  const fill = () => {
    const query = state.query.toLocaleLowerCase();
    if (query !== searchedQuery) {
      searchedQuery = query;
      searchedRows = query ? records.filter(record => {
        let text = searchTexts.get(record);
        if (text === undefined) { text = stringify(record).toLocaleLowerCase(); searchTexts.set(record, text); }
        return text.includes(query);
      }) : records;
    }
    const filtered = searchedRows;
    const pages = Math.max(1, Math.ceil(filtered.length / 10)); state.page = Math.min(state.page, pages - 1);
    list.replaceChildren(); pager.replaceChildren();
    const prev = button("上一页", () => { state.page--; fill(); }, "secondary", true);
    const next = button("下一页", () => { state.page++; fill(); }, "secondary", true);
    prev.disabled = state.page === 0; next.disabled = state.page >= pages - 1;
    prev.dataset.disabled = String(prev.disabled); next.dataset.disabled = String(next.disabled);
    pager.append(prev, el("span", "muted", `${state.page + 1} / ${pages} · ${filtered.length} 条 · 列表每页 10 条`), next);
    for (const record of filtered.slice(state.page * 10, state.page * 10 + 10)) {
      const row = el("article", "compact-record-row"); row.dataset.recordId = record.id || "";
      const title = options.title?.(record) || recordTitle(record);
      const view = button(title, () => openRecord(title, options.body ? options.body(record) : recordList([record])), "record-open"); view.title = title;
      row.append(options.head?.(record) || el("span", "record-dot", "·"), view,
        el("span", "record-date", stamp(options.date ? options.date(record) : record.created_at || record.occurred_at || record.day || record.date)),
        options.actions?.(record) || el("span"));
      list.append(row);
    }
    if (!filtered.length) list.append(empty("没有符合条件的记录"));
  };
  search.querySelector("input").addEventListener("input", (event) => { state.query = event.target.value; state.page = 0; fill(); });
  fill(); return root;
}
function taskDisplay(task) {
  return snapshot?.context_layout_catalog?.tasks?.find((item) => item.id === task)?.label
    || snapshot?.debug?.templates?.find((item) => item.task === task)?.label
    || (debugTaskNames[task] ? `${task}（${debugTaskNames[task]}）` : task);
}
function hasDebugDrafts() { return Boolean(layoutDraft || templateDrafts.size || retentionDraft !== null); }
const debugSelections = new Map();
let debugBodySequence = 0;
let selectedDrive = "loneliness";
const driveDrafts = new Map();
const driveNames = { loneliness: "寂寞值", energy: "精力" };
const debugTaskNames = { "chat.turn": "聊天回复", "reply.model": "聊天回复", "reply.request": "聊天回复", "reply.tool": "聊天工具", "life.plan": "生成日程", "life.plan_day": "生成今日日程", "life.detail": "细化活动", "life.adjust": "调整日程", "news.select": "挑选新闻", "news.read": "阅读新闻", "search.topic": "选择搜索主题", "search.note": "搜索见闻笔记", "journal.write": "生成日记", "journal.brief": "日记简报", "notes.write": "生成笔记", "notes.brief": "笔记简报" };

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
  const node = append(el("details"), el("summary", "", title));
  let loaded = false;
  node.addEventListener("toggle", () => {
    if (node.open && !loaded) { loaded = true; node.append(el("pre", "", stringify(value))); }
  });
  return node;
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
  memoryBrowser.invalidate();
  debugPageCache = null;
  if (snapshot.context_layout_catalog?.menus) Object.assign(PAGES, snapshot.context_layout_catalog.menus);
  $("#connection").textContent = "已连接 AstrBot";
  $("#version").textContent = `Living World ${snapshot.version || ""}`;
  $("#updated").textContent = `${new Date().toLocaleTimeString("zh-CN", { hour12: false })} 更新`;
}
async function refresh() {
  if (snapshot && currentRoute.page === "memory" && currentRoute.tab === "records") return memoryBrowser.refresh();
  return request(async () => { await readState(); render(); }, currentRoute.page === "character" && currentRoute.tab === "drives" ? "数值已刷新，未保存的修改继续保留" : "数据已刷新");
}
async function action(name, payload = {}) {
  return request(async () => {
    const response = await bridge.apiPost("action", { action: name, ...payload });
    const result = response?.page_state ? response.result : response;
    lastResult = { action: name, result, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }) };
    const template = snapshot.debug?.templates?.find(row => row.task === result?.task);
    if (response?.page_state) {
      Object.assign(snapshot, response.page_state);
    } else if (["save_template", "reset_template"].includes(name) && template && typeof result.template === "string") {
      template.template = result.template;
    } else if (name === "update_state") {
      snapshot.state = clone(result);
    } else if (name !== "memory.history") {
      await readState();
    }
    render();
    return result ?? true;
  }, (result) => result?.status === "skipped" ? `本次未执行：${result.text || result.reason || "请查看操作结果"}` : result?.status === "failed" ? `本次执行失败：${result.text || result.error || result.reason || "请查看操作结果"}` : name === "memory.process" ? "已安排后台提炼，点击刷新数据查看进度" : "操作已完成，请查看执行结果");
}
async function saveSettings(settings) {
  return request(async () => {
    const result = await bridge.apiPost("settings", settings);
    const contextOnly = Object.keys(settings).length > 0 && Object.keys(settings).every(key => ["context_usage", "context_layout", "reply"].includes(key));
    if ((contextOnly || result?.settings_only === true) && result?.settings && typeof result.settings === "object") {
      snapshot.settings = clone(result.settings);
    } else {
      await readState();
    }
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
function openEditor(title, body, handler, submitLabel = "保存", danger = false) {
  $("#editor-title").textContent = title;
  $("#editor-body").replaceChildren(...(Array.isArray(body) ? body : [body]));
  $("#editor-submit").textContent = submitLabel;
  $("#editor-submit").className = `button ${danger ? "danger" : "primary"}`;
  dialogHandler = handler; modalDraft = false;
  $("#editor").showModal();
}
function confirmAction(title, text, handler, danger = false, submitLabel = "确认") {
  openEditor(title, el("p", danger ? "danger-copy" : "muted", text), handler, submitLabel, danger);
}
$("#editor-body").addEventListener("input", (event) => { if (event.target.name && !event.target.readOnly) modalDraft = true; });
$("#editor-body").addEventListener("change", (event) => { if (event.target.name && !event.target.readOnly) modalDraft = true; });
$("#editor").addEventListener("close", () => { modalDraft = false; });
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
    if (record.context_category) meta.append(badge(contextName(record.context_category)));
    else if (record.kind || record.source || record.type) meta.append(badge(record.kind || record.source || record.type));
    if (record.scope) meta.append(el("span", "", scopeLabel(record.scope)));
    if (record.person_id) meta.append(el("span", "", `人物 ${record.person_id}`));
    const time = record.start_at || record.start || record.created_at || record.timestamp || record.date || record.day;
    if (time) meta.append(el("span", options.timeline ? "timeline-time" : "", options.timeline && record.end ? `${clockTime(time)} — ${clockTime(record.end)}` : stamp(time)));
    if (options.currentId === record.id) meta.append(badge("当前活动", "good"));
    if (record.important || record.pinned) meta.append(badge("重要保留", "good"));
    if (record.stable && !record.context_category) meta.append(badge("稳定画像", "good"));
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
  return append(el("label", "inline-check"), control, el("span", "", label));
}
function settingsForm(label, description) {
  const form = el("form", "stack"); form.dataset.settings = "true";
  form.append(append(el("div", "section-toolbar"), el("p", "muted", description), button(label, () => form.requestSubmit(), "primary")));
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
function renderOverview(tab = "current") {
  if (tab === "recent") return compactRecords("overview-recent", rows("observations"), { body: (row) => observationList([row]) });
  const root = el("div", "stack");
  const state = snapshot.state || {};
  const current = currentActivity();
  const today = orderedActivities();
  const persona = rows("personas").find((row) => row.id === snapshot.settings?.persona_id);
  const weather = rows("observations").find((item) => observationKind(item, "weather"));
  const next = today.flatMap((item) => item.actions?.social?.enabled ? [{ activity: item, action: item.actions.social }] : []).filter((item) => (!item.action.execution?.status || item.action.execution.status === "pending") && new Date(item.action.at || item.activity.start).getTime() >= Date.now()).sort((a, b) => String(a.action.at || a.activity.start).localeCompare(String(b.action.at || b.activity.start)))[0];
  root.append(append(el("div", "section-toolbar"), el("p", "muted", `${dayNow()} · ${persona?.name || snapshot.settings?.persona_id || "尚未绑定人格"} · 管理员视图`), append(el("div", "actions"), linkButton("聊天对象与白名单", "whitelist"), linkButton("日程生成参数", "schedule?tab=settings"))));
  const stateGrid = el("div", "state-grid");
  const sleep = current?.sleep_state || state.sleep_state || "未知";
  const sleepLabels = { awake: "清醒", asleep: "睡眠中", sleeping: "睡眠中", unknown: "未知" };
  for (const [label, value] of [["心情", state.mood || "未知"], ["精力", snapshot.drives?.meters?.energy?.display_value === undefined ? "未知" : `${snapshot.drives.meters.energy.display_value} / 100`], ["地点", current?.location || state.location || "未知"], ["睡眠", sleepLabels[sleep] || sleep], ["天气", weather?.factual_summary || weather?.text || "尚未取得天气"], ["今日活动", `${today.length} 个活动`]]) stateGrid.append(append(el("div", "state-tile"), el("span", "", label), el("strong", "", value)));
  const currentBody = append(el("div", "current-activity"), el("p", "eyebrow", current ? `${clockTime(current.start)} — ${clockTime(current.end)} · 当前活动` : "当前没有进行中的活动"), el("h2", "", current?.title || "等待下一段生活"), el("p", "", current?.description || current?.content || "当天日程生成后，活动将在自己的时间开始。"), current ? actionBadges(current) : null);
  const nextBody = append(el("div", "stack"), el("div", "next-social-time", next ? clockTime(next.action.at || next.activity.start) : "暂无待执行联系"), el("p", "record-body", next ? next.action.intent || next.activity.title : "临近活动细化时，再决定是否主动聊天。"), el("p", "hint", "活动细化决定是否主动聊天；对象按白名单权重抽选，并检查冷却、免打扰与发送上限。"), linkButton("查看聊天对象与白名单", "whitelist"));
  const timeline = recordList(today, { timeline: true, currentId: current?.id, emptyTitle: "今天还没有正式日程" });
  timeline.classList.add("home-timeline");
  append(root, append(el("div", "overview-grid"), append(el("div", "stack"), card("此刻的角色", "状态来自设定与日程，没有依据时显示未知。", stateGrid), card("现在在做什么", "角色日常与真实行动分别记录。", currentBody)), card("今日时间线", "新闻 → 搜索 → 聊天；同一活动可以安排多种行动。", timeline, linkButton("完整日程", "schedule?tab=timeline")), card("下一次主动联系", "内容到执行时结合聊天场合生成。", nextBody)));
  return root;
}
function renderCharacter(tab) {
  const settings = snapshot.settings || {};
  const f = (label, name, options = {}, fallback = "") => field(label, name, valueAt(settings, name, fallback), options);
  if (tab === "drives") return renderDrives();
  if (tab === "events") return compactRecords("events", byRecordScope("events", rows("events")), { filter: recordScopeFilter("events") });
  if (tab === "state") {
    const form = settingsForm("保存生活状态", "心情与作息是当前生活数据；地点、睡眠由活动及角色默认设置提供。");
    form.append(append(el("div", "form-grid"), field("当前心情", "mood", snapshot.state?.mood || "平静"), field("当前作息", "routine", snapshot.state?.routine || "", { type: "textarea", rows: 3 })));
    form.append(details(snapshot.state || {}, "查看当前状态"));
    return finishForm(form, "character.state", (patch) => action("update_state", { patch }));
  }
  const form = settingsForm("保存角色与世界", "补充人格和生活设定；核心人格正文仍在 AstrBot 人格管理中编辑。");
  const personas = [{ value: "", label: "请选择绑定人格" }, ...rows("personas").map((row) => ({ value: row.id, label: row.name || row.id }))];
  form.append(append(el("div", "form-grid"), f("绑定人格", "persona_id", { options: personas, required: true }), f("时区", "character.timezone", {}, "Asia/Shanghai"),
    f("角色补充资料", "character.profile", { type: "textarea", rows: 4 }), f("世界设定", "character.world", { type: "textarea", rows: 4 }),
    f("初始情绪", "character.mood", {}, "平静"), f("默认地点", "character.location"), f("默认睡眠状态", "character.sleep_state", { options: ["未知", "清醒", "睡眠"] }, "未知")));
  return finishForm(form, "character.profile");
}
function recordScopeFilter(key) {
  return chooser("场合", key + ".scope", [{ value: "", label: "全部场合" }, ...scopeOptions()]);
}
function byRecordScope(key, records) {
  const scope = selections.get(key + ".scope");
  return scope ? records.filter((row) => (row.scope || row.umo || "global") === scope) : records;
}
function sessionStatus(scope) {
  const value = rows("session_status").find((row) => row.umo === scope);
  const box = el("div", "session-status");
  if (!value) return append(box, el("p", "muted", "尚未检查。没有历史也可以接入；检查只读，不调用模型或发送消息。"));
  const history = value.history_status === "found" ? `已找到历史：${value.history_count} 条` : value.history_status === "error" ? "历史读取失败" : "首次对话／暂无历史";
  box.append(append(el("div", "actions"), badge(history, value.history_status === "error" ? "bad" : ""), badge(value.allowed ? "配置允许接入" : "配置未通过", value.allowed ? "good" : "bad")),
    el("p", "session-line", `实际人格：${value.persona_id || "未解析"} · 绑定：${value.bound_persona || snapshot.settings.persona_id || "未设置"} · 来源：${{ host_default: "AstrBot 默认设置", conversation: "当前对话指定", session_rule: "宿主会话规则" }[value.persona_source] || value.persona_source || "未知"}`));
  if (value.reason) box.append(el("p", value.allowed ? "session-line" : "danger-copy", value.reason));
  if (value.history_error) box.append(el("p", "danger-copy", value.history_error));
  const attempt = value.context_status?.last_attempt, injected = value.context_status?.last_injected;
  box.append(el("p", "session-line", injected ? `最近实际注入：${stamp(injected.at)} · ${injected.reason}` : "尚未观察到实际注入；检查通过不代表已经注入。"));
  if (attempt && attempt.at !== injected?.at) box.append(el("p", "session-line", `最近处理：${attempt.reason}`));
  box.append(details(value, "会话与检查详情"));
  const turn = attempt?.turn_id || injected?.turn_id;
  if (turn && rows("debug_records").some((row) => row.turn_id === turn)) box.append(linkButton("查看这轮接入记录", `debug?turn=${encodeURIComponent(turn)}`));
  return box;
}
function whitelistBase(original) {
  const [connection = "", type = "GroupMessage", number = ""] = (original.umo || "").split(":");
  return { connection: connection || rows("platforms")[0]?.id || "", type, number: number.split("_").at(-1), weight: original.weight ?? 1, enabled: original.enabled !== false, display_name: original.display_name || "" };
}
function whitelistFields(value, original) {
  const known = [...new Set([...rows("platforms").map((item) => item.id), ...(snapshot.settings.sessions || []).map((item) => (typeof item === "string" ? item : item.umo || "").split(":")[0])].filter(Boolean))];
  const box = el("div", "stack whitelist-entry");
  const fields = append(el("div", "form-grid"), field("QQ 连接", "connection", value.connection, { options: known, required: true }), field("聊天类型", "type", value.type, { options: [{ value: "GroupMessage", label: "群聊" }, { value: "FriendMessage", label: "私聊" }] }),
    field("群号 / QQ 号", "number", value.number, { required: true }), field("对话称呼（提供给 AI）", "display_name", value.display_name, { hint: "可选，优先使用此称呼；留空自动读取昵称或群名，读取失败显示未设置名字。" }), field("抽选权重", "weight", value.weight, { type: "number", min: 0, step: 0.1 }), checkField("启用此对象", "enabled", value.enabled));
  const scope = () => serializeWhitelist(readFields(fields), original).umo;
  const status = el("div"), preview = el("p", "session-line");
  const update = () => { preview.textContent = `会话标识：${scope()}`; status.replaceChildren(sessionStatus(scope())); };
  const inspect = button("检查会话与历史", async () => {
    const result = await request(() => bridge.apiPost("action", { action: "inspect_session", scope: scope() }), "会话检查完成；没有调用模型或发送消息");
    if (result !== false) { snapshot.session_status = [...rows("session_status").filter((row) => row.umo !== result.umo), clone(result)]; update(); }
  }, "secondary");
  box.append(inspect, fields, preview, status); fields.addEventListener("input", update); fields.addEventListener("change", update); box.refresh = update;
  if ((original.umo || "").includes("_")) box.append(el("p", "hint", "不改目标时保留已有群成员会话标识；人格和历史仍按真实场合判断。"));
  return box;
}
function serializeWhitelist(value, original) {
  const old = whitelistBase(original);
  const same = ["connection", "type", "number"].every((key) => value[key] === old[key]);
  return { ...original, umo: same && original.umo ? original.umo : `${value.connection}:${value.type}:${String(value.number).trim()}`, weight: value.weight, enabled: value.enabled, display_name: String(value.display_name || "").trim() };
}

function renderWhitelist(tab = "targets") {
  const settings = snapshot.settings || {};
  const f = (label, key, options, fallback) => field(label, `social.${key}`, valueAt(settings, `social.${key}`, fallback), options);
  if (tab === "deliveries") return compactRecords("deliveries", byRecordScope("deliveries", rows("deliveries")), { filter: recordScopeFilter("deliveries"), title: (row) => `${scopeLabel(row.umo || row.target || row.scope)} · ${recordTitle(row)}` });
  if (tab === "reply") {
    const form = settingsForm("保存回复与插话设置", "三份要求独立保存；默认分别用于普通群聊、普通私聊和主动聊天正文（含插话正文）。");
    for (const [key, label, resetLabel] of [["group_prompt", "本轮群聊回复要求", "恢复群聊默认文案"], ["private_prompt", "本轮私聊回复要求", "恢复私聊默认文案"], ["proactive_prompt", "本轮主动聊天要求", "恢复主动聊天默认文案"]]) {
      const item = field(label, `reply.${key}`, settings.reply?.[key] ?? snapshot.reply_defaults?.[key] ?? "", { type: "textarea", rows: 4 });
      const input = item.querySelector("textarea"); input.maxLength = 8000;
      const reset = button(resetLabel, () => { input.value = snapshot.reply_defaults?.[key] ?? ""; input.dispatchEvent(new Event("input", { bubbles: true })); notice("已填入默认文案，保存后生效"); }, "secondary");
      form.append(item, append(el("div", "actions"), reset));
    }
    form.append(f("群聊插话间隔（分钟）", "interjection_interval_minutes", { type: "number", min: 1, max: 1440 }, 30),
      el("p", "hint", "作为独立指令，留空不注入，每份最多 8000 字符。可在上下文排序选择各任务使用的要求并调整位置；保存后下一轮生效，工具后的调用沿用本轮快照，不写入聊天历史。"), linkButton("调整上下文位置", "context?tab=layout"), linkButton("回复／插话模块开关", "system?tab=modules"));
    form.id = "group-reply-settings"; return finishForm(form, "chat.reply");
  }
  if (tab === "limits") {
    const form = settingsForm("保存发送限制", "应用于实际日程主动聊天；保存不发送消息。");
    form.append(el("p", "hint", "每轮抽选 1 个目标：一个群或一个私聊对象。没有合格对象时不发送，已选对象失败后不另抽。"), append(el("div", "form-grid"), f("同对象冷却（分钟）", "cooldown_minutes", { type: "number", min: 0, max: 10080 }, 60), f("每日发送上限", "daily_limit", { type: "number", min: 0, max: 1000 }, 5), f("免打扰开始", "quiet_start", { type: "time" }, "23:00"), f("免打扰结束", "quiet_end", { type: "time" }, "08:00")));
    return finishForm(form, "chat.limits");
  }
  return arrayEditor("targets", (settings.sessions || []).map((row) => typeof row === "string" ? { umo: row } : row), {
    selectLabel: "选择聊天对象", saveLabel: "保存聊天白名单", base: whitelistBase, fields: whitelistFields, serialize: serializeWhitelist,
    label: (row, index) => `${index + 1} · ${row.type === "GroupMessage" ? "群聊" : "私聊"} ${row.number || "待填写"}`,
    create: () => ({ umo: "", enabled: true, weight: 1 }), payload: (sessions) => ({ sessions }),
    validate: (sessions) => { if (sessions.some((row) => !/^[^:]+:(GroupMessage|FriendMessage):(?:\d+_)?\d+$/.test(row.umo))) throw new Error("群号 / QQ 号只填写数字，并选择有效的 QQ 连接。"); if (new Set(sessions.map((row) => row.umo)).size !== sessions.length) throw new Error("聊天对象重复，请合并重复白名单。"); },
  });
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
  if (formDrafts.has("schedule.settings")) { notice("请先保存日程设置，再细化活动。", true); return; }
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
function renderSchedule(tab = "timeline") {
  const root = el("div", "stack"); const settings = snapshot.settings || {};
  const form = settingsForm("保存日程设置", "每天自动生成活动大纲，临近活动时再细化并决定行动。保存设置不会调用模型。");
  const f = (label, key, options, fallback) => field(label, `life.${key}`, valueAt(settings, `life.${key}`, fallback), options);
  const numberOptions = { type: "number", min: 0, max: 48, step: 1 };
  form.append(card("大纲生成参数", "生成时间和活动数修改默认次日生效；保存后可用「重新生成日程」立即用于今天。大纲只安排活动，不分配行动数量。", append(el("div", "form-grid"), f("每日生成时间", "daily_plan_time", { type: "time", required: true }, "06:00"), f("每天活动数", "activity_count", { ...numberOptions, min: 1 }, 10), f("提前细化活动（分钟）", "detail_minutes", { type: "number", min: 0, max: 120 }, 10))));
  form.append(append(el("div", "actions"), linkButton("内在状态", "drives"), linkButton("聊天对象与白名单", "whitelist")), el("p", "hint", "每个活动每类最多一次，可以多类同时存在或全部不安排。细化只收到内在状态对应的当前想法；日程行动实际开始后扣值，失败不退还。一轮主动聊天可抽选多个不同对象，各对象仍受白名单、冷却、免打扰和发送上限约束。"));
  if (tab === "settings") return finishForm(form, "schedule.settings");
  const date = el("input"); date.type = "date"; date.value = scheduleDate || dayNow(); date.setAttribute("aria-label", "日程日期"); date.style.width = "auto";
  date.addEventListener("change", () => { scheduleDate = date.value; render(); });
  const activities = orderedActivities(date.value);
  const editable = snapshot.day_regenerating ? [] : activities.filter((item) => item.status === "planned" && new Date(item.start).getTime() > Date.now());
  const batch = () => openEditor("批量调整未来活动", [el("p", "hint", "仅修改列出的未开始活动大纲，活动总数保持。大纲改变后，旧细化归档并作废，取消尚未执行的行动决定。保存不调用模型、不发送消息。"), field("活动调整 JSON", "json", stringify({ updates: editable.map((item) => ({ id: item.id, changes: { title: item.title, start: item.start, end: item.end, content: item.content || item.description || "", location: item.location || "", sleep_state: item.sleep_state || "unknown" } })) }), { type: "textarea", rows: 18 })], (values) => { try { return action("update_activities", JSON.parse(values.json)); } catch (error) { notice(`JSON 格式错误：${error.message}`, true); return false; } }, "保存未来活动调整");
  const regenerate = button("重新生成日程", () => {
    if (formDrafts.has("schedule.settings")) { notice("请先保存日程设置，再重新生成日程。", true); return; }
    confirmAction("重新生成今天的日程", "将真实调用模型，使用最新已保存参数和当前模板替换今天整份活动大纲。旧日程、细化和执行记录归档保留，失败时保留原日程。新活动在细化后决定未来行动；内在状态、白名单、发送次数和冷却限制不重置，过期行动不补做。", () => action("regenerate_day", { date: date.value }), false, "调用模型并重新生成");
  }, "primary");
  regenerate.dataset.disabled = String(date.value !== dayNow() || Boolean(snapshot.day_regenerating));
  const batchButton = button("批量调整未来活动", batch, "secondary");
  batchButton.dataset.disabled = String(Boolean(snapshot.day_regenerating) || !editable.length);
  const toolbar = append(el("div", "section-toolbar"), date, append(el("div", "actions"), regenerate, batchButton));
  const list = recordList(activities, { timeline: true, emptyTitle: "这一天还没有安排", emptyDescription: "到生成时间自动生成；也可手动重新生成今天的日程。", body: (record) => append(el("details"), el("summary", "", "细化与执行详情"), activityDetail(record)), actions: (record) => editable.includes(record) ? append(el("div", "actions"), button("编辑", () => editActivity(record), "secondary", true), button(record.detailed ? "重新细化" : "细化活动", () => detailActivity(record), "secondary", true)) : null });
  root.append(card("日程与实际行动", "先生成大纲，再细化活动；细化决定是否安排新闻、搜索、主动聊天。按新闻 → 搜索 → 聊天执行，每项行动只执行一次。", append(el("div", "stack"), toolbar, snapshot.day_regenerating ? el("p", "hint", "正在重新生成日程：等待已有执行结束并生成新计划，期间暂停日程推进和活动编辑。完成后刷新查看结果。") : el("p", "muted", "重新生成日程保留内在状态。统计包含所选日期内被替换的旧版本实际行动；实际发送继续遵守白名单与发送限制。"), scheduleSummary(date.value), list)));
  if (tab === "timeline") return root;
  root.replaceChildren(date);
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
  const toolbar = append(el("div", "section-toolbar"), el("p", "muted", "两项数值由程序管理；AI 只在细化时看到阶段文案，不会收到数值和计算规则。"), linkButton("模块启停设置", "system?tab=modules"));
  root.append(toolbar);
  selections.set("drive_meter", selectedDrive);
  root.append(chooser("选择内在状态", "drive_meter", Object.entries(driveNames).map(([value, label]) => ({ value, label })), selectedDrive, (value) => { selectedDrive = value; render(); }));
  const id = selectedDrive; const draft = driveDraft(id);
  const panel = el("div", "stack drive-editor"); panel.id = "drive-editor"; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-label", driveNames[id]);
  const markDraft = () => { draft.configDirty = true; };
  const currentForm = el("form", "drive-current-form");
  const current = field(`${driveNames[id]}当前值`, "drive.value", draft.value, { type: "number", min: 0, max: 100, step: "any", required: true });
  const currentInput = current.querySelector("input");
  currentInput.addEventListener("input", () => { draft.value = currentInput.value; draft.valueDirty = true; });
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
    el("p", "", "仅日程行动实际开始时扣值，失败不退；不足时仍可执行，最低归零。聊天一轮多对象只扣一次；手动来源、被动回复、群聊插话和独立 AI 日报不扣。"),
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
  configForm.prepend(append(el("div", "form-actions"), save));
  configForm.addEventListener("submit", (event) => {
    event.preventDefault(); const before = stringify(draft.config); const config = clone(draft.config);
    config.growth_per_hour = Number(config.growth_per_hour); for (const key of Object.keys(config.costs)) config.costs[key] = Number(config.costs[key]);
    saveDrive(id, "save_drive_settings", { config }, () => { if (stringify(draft.config) === before) draft.configDirty = false; });
  });
  const configCard = card("增长、消耗与阶段", "参数单独保存，切换和刷新保留草稿。", configForm);
  configCard.classList.add("drive-config-card"); panel.append(configCard);
  root.append(panel);
  return root;
}

function memoryCheck(label, name, value) {
  const node = append(el("label", "inline-check"), el("input"), el("span", "", label));
  Object.assign(node.firstChild, { type: "checkbox", name, checked: Boolean(value) }); return node;
}
function contextName(id) { return snapshot?.context_layout_catalog?.blocks?.find((item) => item.id === id)?.label || id; }
function sourceIndexButton(id) {
  const item = button("来源", () => showSourceIndex(id), "secondary layout-source", true);
  item.setAttribute("aria-label", contextName(id) + " · 来源"); return item;
}
function renderContextUsage() {
  const categories = [
    { id: "memory.self", label: "记忆与画像 · 自身稳定画像", initial: 5, source: "memory" },
    { id: "memory.people", label: "记忆与画像 · 每人稳定画像", initial: 5, source: "memory" },
    { id: "memory.related", label: "记忆与画像 · 话题相关记忆", initial: 10, source: "memory" },
    { id: "memory.recent", label: "近期记忆", initial: 5, source: "memory.recent" },
    { id: "weather", label: "天气", initial: 1, source: "weather", max: 1 },
  ];
  const data = snapshot.settings.context_usage || { limits: {} };
  const form = settingsForm("保存上下文用量", "各部分独立限量，0 停止对应资料注入；保存不调用模型、不删除存档。"); form.id = "context-usage-settings";
  const total = el("output", "context-usage-total"); total.setAttribute("aria-live", "polite");
  form.append(total, el("p", "hint", "上限按自身画像＋每人画像×最多人物数＋相关记忆＋近期记忆＋天气计算。实际数量还受任务勾选、场合、模块和去重影响；近期记忆优先。日程、宿主人格和聊天历史另计。"));
  const list = el("div", "context-usage-list");
  for (const item of categories) {
    const row = el("div", "context-usage-row"); row.dataset.blockId = item.id;
    const amount = field(item.label + " · 最多条数", "context_usage.limits." + item.id, data.limits[item.id] ?? item.initial, { type: "number", min: 0, max: item.max || 50, step: 1, required: true });
    amount.querySelector("input").dataset.limitId = item.id;
    row.append(amount, sourceIndexButton(item.source));
    list.append(row);
  }
  const people = field("群聊最多人物数", "context_usage.people_limit", data.people_limit ?? 3, { type: "number", min: 0, max: 20, step: 1, required: true, hint: "优先当前说话人，再取近期窗口内的参与者；0 不加载人物画像。" });
  form.append(list, people);
  finishForm(form, "context.usage", () => saveSettings({ context_usage: {
    version: 2,
    limits: Object.fromEntries([...form.querySelectorAll("[data-limit-id]")].map((input) => [input.dataset.limitId, Number(input.value)])),
    people_limit: Number(people.querySelector("input").value),
  } }));
  const updateTotal = () => { total.textContent = `配置合计最多注入 ${[...form.querySelectorAll("[data-limit-id]")].reduce((sum, input) => sum + (Number(input.value) || 0) * (input.dataset.limitId === "memory.people" ? Number(people.querySelector("input").value) || 0 : 1), 0)} 条`; };
  form.querySelector(".section-toolbar").append(button("恢复初始用量", () => {
    for (const input of form.querySelectorAll("[data-limit-id]")) {
      input.value = categories.find(item => item.id === input.dataset.limitId).initial;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    people.querySelector("input").value = 3; people.querySelector("input").dispatchEvent(new Event("input", { bubbles: true }));
  }, "secondary"));
  form.addEventListener("input", updateTotal); updateTotal(); return form;
}
function memoryProgress() {
  const status = snapshot.memory_status || {};
  const queue = status.queue || {};
  const root = el("div", "memory-progress stack");
  root.append(el("p", "hint", `材料提炼：待处理 ${queue.pending || 0}，失败 ${queue.failed || 0}${queue.processing ? " · 正在处理" : ""}。只在所属场合内整理。`));
  root.append(button("处理待提炼材料", () => action("memory.process"), "secondary"), el("p", "hint", "处理材料可能调用记忆模型；不执行搜索或发送 QQ。原始经历、见闻和日记继续保留。"));
  return root;
}
function renderMemorySettings() {
  const data = snapshot.settings.memory || {};
  const form = settingsForm("保存提炼与遗忘设置", "保存本面板参数，下一次处理采用新设置。模型在系统与数据 → 模型分配设置。");
  const f = (label, key, initial, extra = {}) => field(label, `memory.${key}`, data[key] ?? initial, { type: "number", min: 0, step: 1, required: true, ...extra });
  form.append(heading("记忆提炼", "普通聊天使用最终回复及必要背景；来源材料保存后进入后台提炼。"), append(el("div", "form-grid"),
    f("累计聊天轮数", "chat_batch_rounds", 5, { min: 1, max: 100 }),
    field("闲置多少分钟后处理", "memory.chat_idle_minutes", (data.chat_idle_seconds ?? 600) / 60, { type: "number", min: 1 / 60, max: 1440, step: "any", required: true }),
    f("每次最多提炼条数", "reflection_limit", 8, { min: 1, max: 100 }),
    f("检索词理解超时（秒）", "query_timeout_seconds", 15, { min: 1, max: 15 })),
    heading("可选遗忘", "默认关闭。只计算插件在线、记忆模块及此开关同时开启的时间；主动记忆和手动重要项免于遗忘。"),
    memoryCheck("开启遗忘", "memory.forgetting_enabled", data.forgetting_enabled),
    el("p", "hint", "达到遗忘条件会彻底删除该记忆正文、历史版本及索引关联；原始来源仍保留。关闭开关后暂停计时，重新开启不补扣。"),
    append(el("div", "form-grid"), f("普通记忆初始强度", "initial_strength", 10, { min: 0.01, step: "any" }),
      field("低档淡化间隔（在线天数）", "memory.low_decay_days", (data.low_decay_seconds ?? 259200) / 86400, { type: "number", min: 1 / 86400, max: 365, step: "any", required: true }),
      f("每次淡化强度", "low_decay_amount", 1, { min: 0.01, step: "any" }), f("有用反馈增加强度", "useful_strength", 1, { step: "any" }),
      f("有用反馈增加分数", "useful_score", 2.5, { step: 0.5 }), f("中档有用分门槛", "medium_threshold", 3, { step: 0.5 }),
      f("长期保留有用分门槛", "long_threshold", 10, { step: 0.5 }), f("无用反馈降低强度", "useless_strength", 1, { step: 0.5 })),
    el("p", "hint", "低档随在线时间淡化；达到中档后只因明确无用反馈淡化；达到长期门槛后保留。反馈只针对本轮实际提供的记忆，失败或缺失不奖惩。"));
  finishForm(form, "memory.settings", ({ memory }) => {
    const { chat_idle_minutes, low_decay_days, ...values } = memory;
    return saveSettings({ memory: { ...values, chat_idle_seconds: Math.round(chat_idle_minutes * 60), low_decay_seconds: Math.round(low_decay_days * 86400) } });
  });
  return append(el("div", "stack"), form, card("后台提炼", "队列与进度持久化，重启后继续；失败材料保留待重试。", memoryProgress()));
}
const memoryBrowser = createMemoryBrowser({
  el, append, button, field, badge, empty, scopeLabel, scopeOptions,
  api: (name, data) => bridge.apiPost("action", { action: name, ...data }),
  settings: () => snapshot.settings,
  notice,
  navigate: params => navigate("memory", "records", params),
  onCount: count => { if (snapshot && count !== undefined) snapshot.memory_count = count; },
  confirmDelete: (record, remove) => confirmAction("删除这条记忆", "彻底删除该记忆及历史版本，停止召回；原始来源和必要处理标记保留。", remove, true, "删除记忆"),
});
function renderMemory(tab = "records") {
  if (tab === "journals") return renderJournal();
  if (tab === "settings") return renderMemorySettings();
  return memoryBrowser.render(currentRoute.params);
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
  const generate = () => openEditor("生成日记或笔记", [el("p", "hint", "从记忆中取材生成正文，保存后进入统一后台提炼；不发送 QQ。没有材料时跳过。"), field("日期", "date", dayNow(), { type: "date", required: true }), field("类型", "kind", "journal", { options: [{ value: "journal", label: "生活日记" }, { value: "note", label: "见闻笔记" }] }), field("主题（笔记使用）", "topic", "", { hint: "日记按目标日期回顾，笔记按主题召回相关记忆。" }), field("所属场合", "scope", "global", { options: scopeOptions() })], (values) => action("generate_journal", values), "调用 AI 并保存记录");
  const kind = chooser("类型", "journals.kind", [{ value: "", label: "全部类型" }, { value: "journal", label: "生活日记" }, { value: "notes", label: "见闻笔记" }]);
  root.append(append(el("div", "actions"), button("生成日记或笔记", generate, "primary")), append(el("div", "compact-filter"), recordScopeFilter("journals"), kind));
  const records = byRecordScope("journals", rows("entries")).filter((row) => !selections.get("journals.kind") || row.kind === selections.get("journals.kind"));
  const body = (record) => append(el("div", "stack"), el("p", "hint", `所属场合：${scopeLabel(record.scope)}`),
    el("p", "hint", "正文保存后由统一记忆系统提炼。日常聊天通过记忆与画像、近期记忆获取相关内容。"),
    append(el("details"), el("summary", "", "查看完整正文"), el("div", "record-body", record.text || "")));
  root.append(compactRecords("journals", records, { title: (row) => `${row.day || "未记录日期"} · ${kindNames[row.kind] || "日记"}`, body,
    actions: (record) => button("删除", () => confirmAction("删除这篇记录", "删除日记或笔记及派生记忆，不删除原始见闻，不调用模型。", () => action("delete_entry", { id: record.id }), true, "删除记录"), "danger", true),
  })); root.id = "journal-entries"; return root;
}

const sourceNames = { news: "新闻", search: "搜索", weather: "天气", bilibili: "B站", daily_digest: "AI日报" };
function renderSources(tab = "settings") {
  const root = el("div", "stack"), settings = snapshot.settings || {};
  if (tab === "runs") return compactRecords("digest-runs", rows("daily_digest_runs"));
  const options = Object.entries(sourceNames).map(([value, label]) => ({ value, label: tab === "records" ? (value === "weather" ? contextName("weather") : `近期见闻：${label}`) : `来源设置：${label}` }));
  const type = chooser("来源类型", "source." + tab, tab === "records" ? [{ value: "", label: "全部来源" }, ...options] : options, tab === "records" ? "" : "news"); root.append(type);
  const source = selections.get("source." + tab);
  if (tab === "records") {
    root.append(recordScopeFilter("observations"), compactRecords("observations", byRecordScope("observations", rows("observations")).filter((row) => !source || observationKind(row, source)), { body: (row) => observationList([row]) })); return root;
  }
  if (tab === "manual") {
    if (source === "daily_digest") return append(root, el("p", "hint", "AI日报按各自定时点自动读取，每路每天一次。"), linkButton("查看日报执行", "sources?tab=runs"));
    let operation = source;
    if (source === "bilibili") {
      root.append(chooser("读取操作", "bilibili.operation", [{ value: "bilibili", label: "搜索视频" }, { value: "bilibili_watch", label: "观看指定视频" }, { value: "bilibili_recent", label: "读取公开视频记忆" }])); operation = selections.get("bilibili.operation");
    }
    const labels = { news: ["读取新闻并写感想"], search: ["搜索并保存见闻", "搜索内容", "留空由 AI 根据活动选择主题"], weather: ["读取天气并保存"], bilibili: ["搜索 B 站视频", "视频关键词"], bilibili_watch: ["观看指定视频并保存", "视频 BV 号", "BV…"], bilibili_recent: ["读取公开视频记忆"] };
    root.append(bindDraft(realSourceAction(operation, ...labels[operation]), "manual." + operation)); return root;
  }
  const f = (label, name, options = {}, fallback = "") => field(label, name, valueAt(settings, name, fallback), options);
  if (source === "news" || source === "daily_digest") {
    const news = source === "news", key = "source-array." + source;
    const records = news ? settings.news?.sources || [] : settings.daily_digest?.sources || [];
    const fields = (row) => append(el("div", "form-grid"), field(news ? "来源名称" : "日报名称", "name", row.name, { required: true }),
      ...(news ? [field("RSS / Atom 地址", "url", row.url, { type: "url", required: true })] : [field("B 站作者 UID", "uid", row.uid, { required: true }), field("每日读取时间", "time", row.time || "12:00", { type: "time", required: true }), field("检索关键词", "keywords", Array.isArray(row.keywords) ? row.keywords.join(" ") : row.keywords || "", { type: "textarea", rows: 2 })]),
      checkField(news ? "启用这个新闻源" : "启用这个日报", "enabled", row.enabled !== false));
    root.append(arrayEditor(key, records, { selectLabel: news ? "选择新闻源" : "选择日报作者", saveLabel: news ? "保存新闻源设置" : "保存日报设置", fields,
      label: (row, index) => row.name || `新来源 ${index + 1}`, create: () => news ? ({ id: "custom-" + newDraftId(), name: "", url: "", enabled: true }) : ({ id: "custom-" + newDraftId(), name: "", uid: "", keywords: "日报", time: "12:00", enabled: true }),
      restore: news ? "restore_news_sources" : "restore_digest_sources",
      extra: news ? () => f("来源设置：新闻 · 每次读取候选数量", "news.limit", { type: "number", min: 1, max: 30, hint: "这是来源读取数量；注入上限在上下文用量设置。" }, 5) : null,
      payload: (sources, extra) => news ? { news: { limit: extra.news?.limit ?? settings.news?.limit ?? 5, sources } } : { daily_digest: { sources } },
    })); return root;
  }
  if (source === "search") return append(root, el("p", "hint", "自动沿用 AstrBot 的网页搜索服务与凭据。请到 AstrBot 网页搜索设置中配置。"));
  const form = settingsForm("保存来源设置", "只保存当前来源设置，不执行阅读。");
  if (source === "weather") {
    form.append(append(el("div", "form-grid"), f("地点 / 和风 Location ID", "weather.location"), f("和风 API Host", "weather.api_host", { placeholder: "xxx.re.qweatherapi.com" }), f("认证方式", "weather.auth_mode", { options: [{ value: "api_key", label: "API Key" }, { value: "jwt", label: "JWT" }] }, "api_key"), f("认证凭据", "weather.credential", { type: "password" })));
    form.querySelector(".section-toolbar").append(button("测试天气连接", () => { if (formDrafts.has("source.weather")) { notice("请先保存天气设置，再测试连接。", true); return; } action("test_weather"); }, "secondary"));
  } else {
    const dependency = snapshot.bilibili_dependency || {};
    form.append(el("p", "hint", dependency.text || "固定依赖 astrbot_plugin_bilibili_ai_bot 的公开记忆 API v3。"), f("来源设置：B站 · 每次读取公开视频记忆数量", "bilibili.recent_limit", { type: "number", min: 1, max: 30, hint: "这是来源读取数量；注入上限在上下文用量设置。" }, 5));
  }
  root.append(finishForm(form, "source." + source)); return root;
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
function renderDebugView(view, initiallyOpen) {
  const container = el("details", "debug-round"); container.dataset.turn = view.id;
  const focused = new URLSearchParams(location.hash.split("?")[1] || "").get("turn");
  container.open = focused ? focused === view.id || view.record_ids?.includes(focused) : initiallyOpen;
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
        if (source.count !== undefined) summary.append(el("span", "debug-source-count", `本轮实际注入 ${source.count} 条${source.limit !== undefined ? ` · 采用上限 ${source.limit} 条` : ""}`));
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
  };
  callSelector.querySelector("select").addEventListener("change", (event) => { selected.call = Number(event.target.value); update(); });
  append(body, tabs, panel); container.append(body); update(); return container;
}
function renderTemplates() {
  const root = el("div", "stack");
  const templates = snapshot.debug?.templates || [];
  const prior = rows("prompt_template_history");
  const tasks = [...new Set([...templates.map((item) => item.task), ...prior.map((item) => item.task || item.id)])];
  if (!tasks.length) return empty("尚未取得模板目录");
  if (!tasks.includes(templateTask)) templateTask = tasks[0];
  selections.set("template.task", templateTask);
  root.append(chooser("选择任务模板", "template.task", tasks.map((value) => ({ value, label: taskDisplay(value) })), templateTask, (value) => { templateTask = value; selections.delete("template.version"); render(); }));
  const item = templates.find((row) => row.task === templateTask);
  const archived = prior.filter((row) => (row.task || row.id) === templateTask);
  const versions = [...(item ? [{ value: "current", label: "当前使用的模板" }, { value: "default", label: "内置默认模板（只读）" }] : []), ...archived.map((row, index) => ({ value: String(index), label: `旧模板 · ${stamp(row.archived_at) || `版本 ${index + 1}`}` }))];
  root.append(chooser("模板版本", "template.version", versions, "current"));
  const version = selections.get("template.version");
  if (version !== "current") return append(root, el("p", "hint", "只读查看，不会自动启用或覆盖当前模板。"), el("pre", "template-preview", version === "default" ? item?.default_template || "" : archived[Number(version)]?.template || ""));
  const form = settingsForm("保存此任务模板", "只保存本任务公共指令；本轮临时资料不会自动写入模板。");
  const selectedTask = templateTask;
  const value = field("公共提示词模板", "template", templateDrafts.get(selectedTask) ?? item.template ?? item.default_template ?? "", { type: "textarea", rows: 12 });
  value.querySelector("textarea").addEventListener("input", (event) => { templateDrafts.set(selectedTask, event.target.value); });
  const finish = async (operation) => { const submitted = value.querySelector("textarea").value; const result = await operation(); if (result !== false) { if (templateDrafts.get(selectedTask) === submitted) templateDrafts.delete(selectedTask); render(); } };
  form.querySelector(".section-toolbar").append(button("恢复默认模板", () => finish(() => action("reset_template", { task: selectedTask })), "secondary"));
  form.append(value);
  form.addEventListener("submit", (event) => { event.preventDefault(); finish(() => action("save_template", { task: selectedTask, template: value.querySelector("textarea").value })); });
  root.append(form); return root;
}

function lazyDebugView(selected) {
  if (!debugPageCache || debugPageCache.id !== selected.id) {
    const cache = { id: selected.id, loading: true };
    debugPageCache = cache;
    bridge.apiPost("action", { action: "debug_record", id: selected.id }).then(result => {
      cache.result = result;
    }).catch(error => { cache.error = error?.message || String(error); }).finally(() => {
      cache.loading = false;
      if (debugPageCache === cache && currentRoute.page === "context" && currentRoute.tab === "calls") render();
    });
  }
  const cache = debugPageCache;
  if (cache.loading) return el("p", "hint", "正在读取所选调用；其他记录正文尚未加载…");
  if (cache.error) return append(el("div", "stack"), el("p", "danger-copy", cache.error), button("重新读取此轮", () => { debugPageCache = null; render(); }, "secondary"));
  return renderDebugView(cache.result.view, true);
}

function renderDebugRecords() {
  const root = el("div", "stack");
  const settings = snapshot.settings || {};
  const retention = settingsForm("保存记录数量", "聊天按完整轮次保留；后台任务按类别保留。清理只影响调试。");
  retention.classList.add("retention-form");
  retention.append(field("每类保留次数", "debug.retain_per_category", retentionDraft ?? settings.debug?.retain_per_category ?? 10, { type: "number", min: 1, max: 1000 }));
  retention.addEventListener("input", () => { retentionDraft = retention.querySelector("input").value; });
  retention.addEventListener("submit", async (event) => { event.preventDefault(); const submitted = retentionDraft; const result = await saveSettings(readFields(retention)); if (result !== false) { if (retentionDraft === submitted) retentionDraft = null; render(); } });
  retention.querySelector(".section-toolbar").append(button(debugCategory ? "清空此类调试记录" : "清空全部调试记录", () => confirmAction("清空调试记录", "清理选定类别的完整聊天轮次与后台调用；正式数据、宿主历史和防重记录保留。", () => action("debug_clear", debugCategory ? { category: debugCategory } : {}), true, "清空调试记录"), "danger"));
  root.append(retention);
  const records = rows("debug_records"), views = Array.isArray(snapshot.debug_views) ? snapshot.debug_views : legacyDebugViews(records);
  const categories = [...new Set(views.flatMap((view) => view.categories?.length ? view.categories : [view.task]).filter(Boolean))];
  const category = field("筛选任务类别", "category", debugCategory, { options: [{ value: "", label: "全部类别" }, ...categories.map((task) => ({ value: task, label: taskDisplay(task) }))] });
  category.querySelector("select").addEventListener("change", (event) => { debugCategory = event.target.value; debugRound = ""; render(); });
  const filtered = views.filter((view) => !debugCategory || (view.categories || [view.task]).includes(debugCategory));
  const selected = filtered.find((view) => view.id === debugRound || view.record_ids?.includes(debugRound)) || filtered[0];
  const missing = debugRound && !views.some((view) => view.id === debugRound || view.record_ids?.includes(debugRound));
  if (missing) root.append(el("p", "hint warning", "这次调用的调试记录已清理或尚未产生；正式档案仍保留。"));
  const round = field("选择调用轮次", "debug_round", selected?.id || "", { options: filtered.map((view) => ({ value: view.id, label: `${stamp(view.created_at || view.at)} · ${taskDisplay(view.task)} · ${view.title || scopeLabel(view.scope)} · ${view.id.slice(0, 8)}` })) });
  round.querySelector("select").addEventListener("change", (event) => { debugRound = event.target.value; render(); });
  const at = filtered.indexOf(selected);
  const prev = button("上一轮", () => { debugRound = filtered[at - 1].id; render(); }, "secondary");
  const next = button("下一轮", () => { debugRound = filtered[at + 1].id; render(); }, "secondary");
  prev.dataset.disabled = String(at <= 0); next.dataset.disabled = String(at < 0 || at >= filtered.length - 1);
  root.append(append(el("div", "compact-filter"), category, round, prev, next));
  if (snapshot.provider_capture_available === false) root.append(el("p", "hint warning", "当前 HTTP 捕获适配不可用；旧快照不冒充原文。"));
  root.append(selected ? snapshot.debug_lazy ? lazyDebugView(selected) : renderDebugView(selected, true) : empty("此类别暂时没有调用记录")); return root;
}

function showSourceIndex(id) {
  const info = snapshot.context_layout_catalog?.blocks.find((item) => item.id === id);
  if (!info) return;
  sourceReturn = { hash: location.hash, scroll: window.scrollY, block: id, task: layoutTask };
  $("#source-index-title").textContent = info.label;
  const body = $("#source-index-body"); body.replaceChildren();
  body.append(el("h3", "", "用途"), el("p", "", info.purpose || "此项由当前任务提供。"), el("h3", "", "资料从哪里来"), el("p", "", info.origin || "请查看该任务输入说明。"));
  if (info.host_help) body.append(el("p", "hint", info.host_help));
  const links = el("div", "source-targets");
  for (const target of info.targets || []) {
    if (!PAGES[target.page]?.tabs[target.tab]) continue;
    const params = { ...target.params };
    if (params.current_task) { delete params.current_task; if (snapshot.debug?.templates?.some((row) => row.task === layoutTask)) params.task = layoutTask; }
    links.append(button(target.path || target.label, () => { $("#source-index").close(); navigate(target.page, target.tab, params); }, "secondary"));
  }
  if (links.childElementCount) body.append(el("h3", "", "管理入口"), links);
  const tasks = [...new Set([...(info.templates || []), ...(id === "anchor.user" && snapshot.debug?.templates?.some((row) => row.task === layoutTask) ? [layoutTask] : [])])];
  if (tasks.length) {
    const choose = field("相关提示词", "source_template", tasks[0], { options: tasks.map((value) => ({ value, label: taskDisplay(value) })) });
    body.append(choose, button("打开所选模板", () => { $("#source-index").close(); navigate("context", "templates", { task: choose.querySelector("select").value }); }, "secondary"));
  }
  body.append(el("p", "hint", "这里说明资料块的用途和管理入口，不表示某次历史调用采用了哪条原始记录。"));
  $("#source-index").showModal();
}
$("#source-index-close").addEventListener("click", () => $("#source-index").close());
function renderContextLayout() {
  const root = el("div", "stack context-layout");
  const catalog = snapshot.context_layout_catalog;
  if (!catalog || !snapshot.settings?.context_layout) return empty("请更新并重载插件以取得上下文目录");
  const config = layoutDraft || snapshot.settings.context_layout;
  if (!catalog.tasks.some((item) => item.id === layoutTask)) layoutTask = catalog.tasks.find((item) => item.id === "chat.group")?.id || catalog.tasks[0]?.id;
  const current = config.order;
  const selected = new Set(config.tasks[layoutTask] || catalog.default_selections?.[layoutTask] || []);
  const blockNames = new Map(catalog.blocks.map((item) => [item.id, item]));
  const mark = () => { layoutDraft ||= clone(snapshot.settings.context_layout); return layoutDraft; };
  const heading = field("调整范围", "layout_task", layoutTask, { options: catalog.tasks.map((item) => ({ value: item.id, label: item.label })) });
  heading.querySelector("select").dataset.skip = "1";
  heading.querySelector("select").addEventListener("change", (event) => { layoutTask = event.target.value; render(); });
  const save = async () => { const value = clone(layoutDraft || config); const result = await saveSettings({ context_layout: value }); if (result !== false) { if (JSON.stringify(layoutDraft || config) === JSON.stringify(value)) layoutDraft = null; render(); } };
  const controls = append(el("div", "actions"), button("保存上下文设置", save, "primary"),
    button("恢复本任务默认勾选", () => { mark().tasks[layoutTask] = clone(catalog.default_selections?.[layoutTask] || []); render(); }, "secondary"),
    button("恢复默认排序", () => { mark().order = clone(config.baseline_order || catalog.default); render(); }, "secondary"));
  root.append(controls, heading);
  root.append(el("p", "layout-status", "仅切换本任务的勾选项，排序全局共用。所有资料行均可调整位置；未勾选的资料不参与本任务注入。"));
  root.append(append(el("details", "layout-help"), el("summary", "", "排序与生效说明"), el("p", "", "同一角色内从上到下就是实际注入顺序。在任意任务拖动左侧手柄、使用角色下拉框或上下按钮，都会修改所有任务共用的排序。定位行只读，资料可放到它的前后。日程完整与简版最多勾选一种，也可都不选。恢复操作先形成草稿，保存后下一轮生效；在途工具调用沿用快照。勾选不触发来源读取或其他任务，没有本次材料的行不注入，场合、日期、模块及用量限制继续生效，临时资料不入历史。")));
  const nodes = new Map(), lists = {}, ends = {};
  let drag = null;
  let pointer = null;
  let scrollFrame = null;
  const animateOrder = (layout) => {
    const positions = new Map([...nodes].map(([id, node]) => [id, node.getBoundingClientRect()]));
    nodes.forEach((node) => node.getAnimations().forEach((animation) => animation.cancel()));
    for (const role of ["system", "user"]) {
      let next = ends[role];
      for (const id of [...layout[role]].reverse()) if (nodes.has(id)) {
        const node = nodes.get(id);
        if (node.parentNode !== lists[role] || node.nextSibling !== next) lists[role].insertBefore(node, next);
        next = node;
      }
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (const [id, node] of nodes) {
      const from = positions.get(id), to = node.getBoundingClientRect();
      if (id !== drag?.id && (from.x !== to.x || from.y !== to.y)) node.animate([{ transform: `translate(${from.x - to.x}px, ${from.y - to.y}px)` }, { transform: "none" }], { duration: 150, easing: "ease-out" });
    }
  };
  const commit = (next, id) => {
    if (busy || next === current || JSON.stringify(next) === JSON.stringify(current)) return;
    mark().order = next;
    drag = null; cancelLayoutDrag = null; render();
    [...content.querySelectorAll("[data-block-id]")].find((node) => node.dataset.blockId === id)?.querySelector(".layout-up:not(:disabled),select,button")?.focus();
  };
  const preview = (id, role, target = null, after = false) => {
    if (!drag || busy || id !== drag.id) return;
    const next = reorderedLayout(current, id, role, target, after);
    if (JSON.stringify(next) !== JSON.stringify(drag.preview)) { drag.preview = next; animateOrder(next); }
  };
  const clearDrag = () => {
    cancelAnimationFrame(scrollFrame); scrollFrame = null;
    const active = pointer;
    drag = null; pointer = null; cancelLayoutDrag = null;
    if (active && root.hasPointerCapture(active.id)) root.releasePointerCapture(active.id);
    animateOrder(current);
    root.classList.remove("is-sorting");
    nodes.forEach((node) => node.classList.remove("is-dragging"));
    if (active) nodes.get(active.block)?.querySelector(".layout-source")?.focus({ preventScroll: true });
  };
  const pointTarget = (event) => {
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest(".layout-row,.layout-end");
    return target && root.contains(target) ? target : null;
  };
  const previewPoint = (point) => {
    const target = pointTarget(point); if (!target || !drag) return;
    if (target.dataset.endRole) preview(drag.id, target.dataset.endRole);
    else if (target.dataset.blockId !== drag.id) {
      const bounds = target.getBoundingClientRect();
      preview(drag.id, target.closest(".layout-lane").dataset.role, target.dataset.blockId, point.clientY > bounds.top + bounds.height / 2);
    }
  };
  const scrollDrag = () => {
    scrollFrame = null;
    if (!pointer || !drag) return;
    const direction = pointer.clientY < 80 ? -1 : pointer.clientY > innerHeight - 40 ? 1 : 0;
    if (!direction) return;
    window.scrollBy(0, direction * 14); previewPoint(pointer);
    scrollFrame = requestAnimationFrame(scrollDrag);
  };
  root.addEventListener("pointermove", (event) => {
    if (!pointer || pointer.id !== event.pointerId || busy) return;
    if (!drag && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) < 5) return;
    event.preventDefault();
    pointer.clientX = event.clientX; pointer.clientY = event.clientY;
    if (!drag) { drag = { id: pointer.block, preview: current }; root.classList.add("is-sorting"); nodes.get(drag.id).classList.add("is-dragging"); }
    previewPoint(event);
    if (!scrollFrame) scrollFrame = requestAnimationFrame(scrollDrag);
  });
  root.addEventListener("pointerup", (event) => {
    if (!pointer || pointer.id !== event.pointerId) return;
    const pending = drag, target = pointTarget(event);
    pointer = null;
    cancelAnimationFrame(scrollFrame); scrollFrame = null;
    if (root.hasPointerCapture(event.pointerId)) root.releasePointerCapture(event.pointerId);
    if (pending && target) commit(pending.preview, pending.id);
    if (drag || !pending) clearDrag();
  });
  root.addEventListener("pointercancel", clearDrag);
  root.addEventListener("lostpointercapture", () => { if (pointer) clearDrag(); });
  for (const role of ["system", "user"]) {
    const lane = el("section", "layout-lane"); lane.dataset.role = role;
    lane.append(el("h3", "", role === "system" ? "system · 系统上下文" : "user · 本轮用户上下文"));
    const list = el("ol", "layout-list"); lists[role] = list; list.setAttribute("aria-label", `${role} 注入顺序`);
    const end = el("li", "layout-end", "放到本组末尾"); end.dataset.endRole = role; ends[role] = end;
    const visible = current[role].filter((id) => blockNames.has(id));
    visible.forEach((id, index) => {
      const info = blockNames.get(id), row = el("li", `layout-row${info.anchor ? " layout-anchor" : ""}`); row.dataset.blockId = id; nodes.set(id, row);
      const handle = el("span", "layout-handle", info.anchor ? "▪" : "⠿"); handle.draggable = false; handle.title = info.anchor ? "只读定位行" : "拖动调整顺序";
      if (!info.anchor) handle.addEventListener("pointerdown", (event) => {
        if (busy || event.button !== 0) return;
        event.preventDefault(); handle.closest(".layout-row").querySelector(".layout-source")?.focus({ preventScroll: true });
        pointer = { id: event.pointerId, block: id, x: event.clientX, y: event.clientY }; cancelLayoutDrag = clearDrag; root.setPointerCapture(event.pointerId);
      });
      const name = el("strong", "layout-name", info.label); name.title = info.label;
      row.append(handle);
      if (!info.anchor) {
        const checkbox = el("input", "layout-enabled"); checkbox.type = "checkbox"; checkbox.checked = selected.has(id); checkbox.dataset.skip = "1";
        checkbox.setAttribute("aria-label", `本任务使用${info.label}`);
        row.classList.toggle("layout-unselected", !checkbox.checked);
        checkbox.addEventListener("change", () => {
          if (busy) return;
          const next = new Set(selected);
          if (checkbox.checked) { next.add(id); if (id === "schedule") next.delete("schedule.recent"); else if (id === "schedule.recent") next.delete("schedule"); }
          else next.delete(id);
          mark().tasks[layoutTask] = catalog.blocks.filter((item) => !item.anchor && next.has(item.id)).map((item) => item.id);
          render(); content.querySelector(`[data-block-id="${id}"] .layout-enabled`)?.focus({ preventScroll: true });
        });
        row.append(checkbox);
      }
      row.append(name, sourceIndexButton(id));
      if (!info.anchor) {
        const destination = field("注入角色", "layout_role", role, { options: ["system", "user"] });
        const select = destination.querySelector("select"); select.dataset.skip = "1"; select.setAttribute("aria-label", `${info.label}的注入角色`);
        select.addEventListener("change", () => commit(reorderedLayout(current, id, select.value), id));
        const up = button("↑", () => commit(reorderedLayout(current, id, role, visible[index - 1]), id), "secondary layout-up");
        const down = button("↓", () => commit(reorderedLayout(current, id, role, visible[index + 1], true), id), "secondary");
        up.setAttribute("aria-label", `上移${info.label}`); down.setAttribute("aria-label", `下移${info.label}`);
        up.dataset.disabled = String(index === 0); down.dataset.disabled = String(index === visible.length - 1);
        row.append(destination, up, down);
        handle.addEventListener("dragstart", (event) => { if (busy) { event.preventDefault(); return; } drag = { id, preview: current }; row.classList.add("is-dragging"); root.classList.add("is-sorting"); event.dataTransfer.setData("text/plain", id); event.dataTransfer.effectAllowed = "move"; });
        handle.addEventListener("dragend", () => { if (drag) { drag = null; animateOrder(current); } root.classList.remove("is-sorting"); row.classList.remove("is-dragging"); });
      }
      row.addEventListener("dragover", (event) => {
        if (!drag || busy) return;
        event.preventDefault(); event.stopPropagation();
        if (drag.id !== id) { const bounds = row.getBoundingClientRect(); preview(drag.id, role, id, event.clientY > bounds.top + bounds.height / 2); }
      });
      row.addEventListener("drop", (event) => { if (!drag) return; event.preventDefault(); event.stopPropagation(); const pending = drag; commit(pending.preview, pending.id); });
      list.append(row);
    });
    end.addEventListener("dragover", (event) => { if (drag && !busy) { event.preventDefault(); event.stopPropagation(); preview(drag.id, role); } });
    end.addEventListener("drop", (event) => { if (!drag) return; event.preventDefault(); event.stopPropagation(); const pending = drag; commit(reorderedLayout(current, pending.id, role), pending.id); });
    list.append(end); lane.append(list); root.append(lane);
  }
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
  const showPreview = () => { preview.replaceChildren(); if (pendingBackup) preview.append(el("p", "hint", `已读取 ${pendingBackup.name}`), details(pendingBackup.data, "预览备份内容")); };
  showPreview();
  file.addEventListener("change", async () => {
    pendingBackup = null; preview.replaceChildren();
    const selected = file.files?.[0]; if (!selected) return;
    try {
      if (selected.size > 20 * 1024 * 1024) throw new Error("备份文件超过 20 MB，请检查文件是否正确。");
      const imported = JSON.parse(await selected.text());
      if (!imported || typeof imported !== "object" || Array.isArray(imported)) throw new Error("备份必须是 JSON 对象。");
      pendingBackup = { data: imported, name: `${selected.name} · ${(selected.size / 1024).toFixed(1)} KB` }; showPreview();
    } catch (error) { notice(`读取备份失败：${error.message}`, true); }
  });
  const importAction = () => {
    if (!pendingBackup) { notice("请先选择有效的 JSON 备份文件。", true); return; }
    confirmAction("恢复备份", "恢复备份中的配置，补入本地缺失的业务记录，保留本地已有记录与发送凭据。恢复后全部模块关闭，可检查配置后逐项开启。请先导出当前数据。", () => request(async () => {
      const submitted = pendingBackup; const result = await bridge.apiPost("import", submitted.data); if (pendingBackup === submitted) pendingBackup = null;
      lastResult = { action: "import", result, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }) };
      await readState(); render();
    }, "备份已恢复"), true, "恢复备份");
  };
  append(root, append(el("div", "grid"), card("导出备份", "保存配置、生活、记忆和运行记录，供迁移或恢复。", append(el("div", "stack"), el("p", "muted", "备份可能含私人记忆与角色资料，请保存在可信的位置。插件数据不包含 AstrBot 原始聊天历史。"), append(el("div", "actions"), button("下载 JSON 备份", exportAction, "primary")))), card("从备份恢复", "先选择文件并预览，再确认恢复。", append(el("div", "stack"), append(el("div", "actions"), button("恢复所选备份", importAction, "secondary")), file, preview))));
  return root;
}


function renderSystem(tab) {
  if (tab === "backup") return renderData();
  if (tab === "maintenance") return append(el("div", "stack"), button("记忆提炼与遗忘设置", () => navigate("memory", "settings"), "secondary"), details(snapshot.diagnostics || [], "诊断信息"), details({ version: snapshot.version, counts: Object.fromEntries(["activities", "memories", "observations", "entries", "events", "deliveries", "usage"].map((key) => [key, key === "memories" ? snapshot.memory_count ?? 0 : rows(key).length])) }, "数据统计"), usageTable());
  const form = settingsForm(tab === "models" ? "保存模型分配" : "保存模块开关", "只保存当前面板；关闭模块保留已有数据。");
  if (tab === "modules") form.append(modulePanel(true));
  else {
    const providers = [{ value: "", label: "沿用默认模型" }, ...rows("providers").map((row) => ({ value: row.id, label: row.name || row.id }))];
    const fields = el("div", "form-grid");
    for (const [label, key] of [["默认模型", "default"], ["生活与日程", "life"], ["记忆提炼、检索与反馈", "memory"], ["主动社交", "social"], ["外部见闻", "exploration"], ["日记与笔记", "journal"]]) fields.append(field(label, `models.${key}`, snapshot.settings.models?.[key] || "", { options: providers }));
    form.append(fields);
  }
  return finishForm(form, "system." + tab);
}
function navigate(page, tab, params = {}) {
  const hash = routeHash(page, tab, params);
  if (location.hash === hash) { currentRoute = resolveRoute(hash, rememberedTabs); applyRouteSelection(); render(); focusTarget(); }
  else location.hash = hash;
}
function applyRouteSelection() {
  const { page, tab, params } = currentRoute;
  rememberedTabs[page] = tab;
  if (params.source !== undefined && page === "sources") selections.set("source." + tab, params.source);
  if (params.attribute !== undefined && page === "memory" && tab === "records") selections.set("memory.attribute", params.attribute);
  if (params.kind !== undefined && page === "memory" && tab === "journals") selections.set("journals.kind", params.kind);
  if (params.task && page === "context") {
    if (tab === "templates") { templateTask = params.task; selections.delete("template.version"); }
    else if (tab === "layout") layoutTask = params.task;
  }
  if (params.turn) { debugCategory = ""; debugRound = params.turn; }
}
function focusTarget() {
  const fieldName = currentRoute.params.field;
  if (!fieldName) return;
  const target = [...content.querySelectorAll("[name]")].find((node) => node.name === fieldName);
  if (target) { target.closest(".field")?.classList.add("index-target"); target.scrollIntoView({ block: "center" }); target.focus({ preventScroll: true }); }
}
function render() {
  const { page, tab } = currentRoute, config = PAGES[page];
  $("#page-title").textContent = config.label;
  document.title = `${config.label} · Living World`;
  document.querySelectorAll("#navigation a").forEach((link) => { if (link.dataset.view === page) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
  if (!snapshot) return;
  const tabs = el("div", "secondary-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", config.label + "功能");
  const ids = Object.keys(config.tabs);
  tabs.style.setProperty("--tab-count", ids.length);
  for (const [id, label] of Object.entries(config.tabs)) {
    const item = button(label, () => navigate(page, id), "secondary");
    item.setAttribute("role", "tab"); item.setAttribute("aria-selected", String(id === tab)); item.setAttribute("aria-controls", "section-panel"); item.id = `section-${page}-${id}`; item.tabIndex = id === tab ? 0 : -1;
    item.addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const next = ids[event.key === "Home" ? 0 : event.key === "End" ? ids.length - 1 : (ids.indexOf(id) + (event.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length]; navigate(page, next, { focus_tab: "1" }); });
    tabs.append(item);
  }
  const renderers = { overview: renderOverview, character: renderCharacter, schedule: renderSchedule, chat: renderWhitelist, sources: renderSources, memory: renderMemory, system: renderSystem, context: (part) => ({ layout: renderContextLayout, usage: renderContextUsage, templates: renderTemplates, calls: renderDebugRecords }[part]()) };
  const panel = renderers[page](tab); panel.id = "section-panel"; panel.classList.add("section-panel"); panel.dataset.page = page; panel.dataset.section = tab; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `section-${page}-${tab}`);
  content.replaceChildren(tabs);
  if (sourceReturn && location.hash !== sourceReturn.hash) content.append(button(resolveRoute(sourceReturn.hash).tab === "usage" ? "返回上下文用量" : "返回上下文列表", () => {
    const back = sourceReturn; layoutTask = back.task; sourceReturn = null;
    const show = () => { requestAnimationFrame(() => { window.scrollTo(0, back.scroll); [...content.querySelectorAll("[data-block-id]")].find((node) => node.dataset.blockId === back.block)?.querySelector(".layout-source")?.focus({ preventScroll: true }); }); };
    if (location.hash === back.hash) { render(); show(); } else { window.addEventListener("hashchange", show, { once: true }); location.hash = back.hash; }
  }, "secondary return-to-context"));
  content.append(panel);
  if (lastResult) content.append(details(lastResult.result, `最近操作：${lastResult.action} · ${lastResult.time}`));
  setBusy(busy);
}
window.addEventListener("hashchange", () => {
  memoryBrowser.captureScroll();
  cancelLayoutDrag?.();
  scrollPositions.set(currentRoute.page + ":" + currentRoute.tab, window.scrollY);
  currentRoute = resolveRoute(location.hash, rememberedTabs); applyRouteSelection(); render();
  requestAnimationFrame(() => { window.scrollTo(0, scrollPositions.get(currentRoute.page + ":" + currentRoute.tab) || 0); if (currentRoute.page === "memory" && currentRoute.tab === "records") memoryBrowser.restoreScroll(); focusTarget(); if (currentRoute.params.focus_tab) $("#section-" + currentRoute.page + "-" + currentRoute.tab)?.focus(); });
});
window.addEventListener("keydown", (event) => { if (event.key === "Escape" && cancelLayoutDrag) { event.preventDefault(); cancelLayoutDrag(); } });
window.addEventListener("beforeunload", (event) => { if (hasDrafts()) { event.preventDefault(); event.returnValue = ""; } });
$("#refresh").addEventListener("click", refresh);
try {
  if (!bridge) throw new Error("请从 AstrBot 插件详情中的 Pages 打开此页面，以连接插件后端。");
  await bridge.ready(); applyRouteSelection(); await refresh(); focusTarget();
  setInterval(async () => {
    if (busy || document.hidden || currentRoute.page !== "character" || currentRoute.tab !== "drives") return;
    setBusy(true);
    try { await readState(); updateDriveStatusCards(); } catch (error) { notice(`数值刷新失败：${error.message}`, true); } finally { setBusy(false); }
  }, 60000);
} catch (error) {
  $("#connection").textContent = "连接不可用"; content.replaceChildren(empty("暂时无法连接", error.message)); notice(error.message, true);
}
