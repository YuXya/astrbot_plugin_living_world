// Stable routes shared by menus, legacy links and context source targets.
export const PAGES = {
  overview: { label: "今日概览", tabs: { current: "当前概览", recent: "近期动态" } },
  context: { label: "上下文与提示词", tabs: { layout: "上下文排序", usage: "上下文用量", templates: "提示词模板", calls: "调用记录" } },
  character: { label: "角色与状态", tabs: { profile: "角色与世界", state: "生活状态", drives: "内在状态", events: "经历记录" } },
  schedule: { label: "日程与行动", tabs: { timeline: "日程与执行", settings: "生成与细化设置", archives: "日程档案" } },
  chat: { label: "聊天与对象", tabs: { targets: "聊天白名单", reply: "回复与插话", deliveries: "发送记录" } },
  sources: { label: "见闻与来源", tabs: { settings: "来源设置", records: "近期见闻", manual: "手动读取", runs: "日报执行" } },
  memory: { label: "记忆与日记", tabs: { records: "记忆与画像", settings: "提炼与遗忘", journals: "日记与笔记" } },
  system: { label: "系统与数据", tabs: { models: "模型分配", modules: "模块开关", backup: "备份恢复", maintenance: "维护与诊断" } },
};
const ALIASES = {
  settings: ["character", "profile"], drives: ["character", "drives"],
  whitelist: ["chat", "targets"], social: ["chat", "targets"],
  journal: ["sources", "records"], debug: ["context", "calls"], data: ["system", "backup"],
};
export function resolveRoute(hash, remembered = {}) {
  const [raw, query = ""] = hash.replace(/^#/, "").split("?");
  const params = Object.fromEntries(new URLSearchParams(query));
  if (raw === "memory" && params.tab === "usage") { delete params.tab; return { page: "context", tab: "usage", params }; }
  const [page, aliasTab] = Object.hasOwn(ALIASES, raw) ? ALIASES[raw] : [Object.hasOwn(PAGES, raw) ? raw : "overview", null];
  const candidate = params.tab || aliasTab || remembered[page];
  const tab = Object.hasOwn(PAGES[page].tabs, candidate) ? candidate : Object.keys(PAGES[page].tabs)[0];
  delete params.tab;
  return { page, tab, params };
}
export function routeHash(page, tab, params = {}) {
  return `#${page}?${new URLSearchParams({ tab, ...params })}`;
}
export function reorderedLayout(layout, id, role, target = null, after = false) {
  if (!id || id.startsWith("anchor.") || !["system", "user"].includes(role) || id === target) return layout;
  if (!Object.values(layout).some((lane) => lane.includes(id))) return layout;
  if (target !== null && !layout[role].includes(target)) return layout;
  const next = Object.fromEntries(Object.entries(layout).map(([name, lane]) => [name, lane.filter((item) => item !== id)]));
  const index = target === null ? next[role].length : next[role].indexOf(target) + Number(after);
  next[role].splice(index, 0, id);
  return JSON.stringify(next) === JSON.stringify(layout) ? layout : next;
}
