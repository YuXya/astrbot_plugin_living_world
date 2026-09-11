// Profile-first administrator browsing. Reads never use the model or global state refresh.
const ATTRIBUTES = ["用户别名", "事实属性", "技能树", "关系图谱", "活跃项目"];
const SOURCE_NAMES = { chat: "聊天交流", fiction: "角色经历", event: "经历记录", news: "新闻", search: "搜索", bilibili: "B站", bilibili_watch: "B站观看", daily_digest: "AI日报", journal: "日记", note: "笔记", notes: "笔记", admin: "手动添加", explicit: "主动记忆" };
const STATES = [["active", "全部有效记忆"], ["stable", "稳定画像"], ["inferred", "有依据的推断"], ["protected", "重要或主动保护"], ["replaced", "已替换"], ["all", "包含已替换"]];
const copy = value => structuredClone(value);
const text = value => value === undefined || value === null || value === "" ? "未记录" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);

export function createMemoryBrowser(ui) {
  const { el, append, button, field, badge, empty, scopeLabel, scopeOptions } = ui;
  const root = el("div", "memory-browser stack");
  const states = new Map(), names = new Map(), namePending = new Set(), drafts = new Map(), saving = new Set();
  let activeKey = "", epoch = 0, editorKey = "";
  const editor = el("dialog", "memory-editor");
  const drawer = el("dialog", "memory-detail-drawer");
  drawer.id = "memory-detail-drawer";
  drawer.setAttribute("aria-labelledby", "memory-detail-title");
  const drawerTitle = el("h2", "", "记忆详情"); drawerTitle.id = "memory-detail-title";
  const drawerOwner = el("div", "memory-detail-owner");
  const drawerClose = button("关闭", () => closeMemory(), "secondary", true);
  drawerClose.autofocus = true;
  const drawerBody = el("div", "memory-detail-body");
  drawer.append(append(el("header", "memory-detail-header"), append(el("div"), drawerTitle, drawerOwner), drawerClose), drawerBody);
  let selectedMemory = null, drawerReturn = null;
  document.body.append(editor, drawer);
  drawer.addEventListener("close", () => {
    if (drawer.open) return;
    const target = drawerReturn;
    drawerReturn = null; selectedMemory = null; drawerBody.replaceChildren();
    if (!target) return;
    requestAnimationFrame(() => {
      if (drawer.open || !isActive(target.state)) return;
      const card = [...root.querySelectorAll(".memory-record-card")].find(node => node.dataset.memoryId === target.id);
      (card || root.querySelector(".memory-toolbar button"))?.focus({ preventScroll: true });
      window.scrollTo(0, target.scroll);
    });
  });
  let backdropPressed = false;
  const outsideDrawer = event => {
    const rect = drawer.getBoundingClientRect();
    return event.target === drawer && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
  };
  drawer.addEventListener("pointerdown", event => { backdropPressed = outsideDrawer(event); });
  drawer.addEventListener("click", event => { if (backdropPressed && outsideDrawer(event)) closeMemory(); backdropPressed = false; });
  window.addEventListener("hashchange", () => closeMemory(false));
  editor.addEventListener("close", () => { const state = states.get(activeKey); if (state && root.isConnected) draw(state); });
  const api = (action, data = {}) => ui.api(action, data);
  const sourceName = value => SOURCE_NAMES[value] || value || "未记录";
  const isActive = state => activeKey === state.key && root.isConnected;
  const ownerKind = profile => profile?.owner === "person" ? "QQ 人物" : profile?.current ? "当前自身画像" : "历史自身画像";
  const profileName = profile => (profile?.name_status === "manual" ? profile.name : names.get(profile?.id)?.name) || profile?.name || profile?.persona_name || "未获取昵称";
  const number = profile => String(profile?.person_id || "").replace(/^qq:/i, "");

  function stamp(value) {
    if (value === undefined || value === null || value === "") return "未记录";
    const date = new Date(typeof value === "number" ? value * (Math.abs(value) < 1e12 ? 1000 : 1) : value);
    if (!Number.isFinite(date.getTime())) return "未记录";
    try {
      return new Intl.DateTimeFormat("zh-CN", { timeZone: ui.settings().character.timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
    } catch { return "未记录"; }
  }

  function getState(key, profile = "") {
    if (!states.has(key)) states.set(key, {
      key, profile, query: "", kind: "", scope: "", source: "", attribute: "", state: "active",
      mode: profile ? "grouped" : "timeline", offset: 0, offsets: {}, scroll: 0,
      data: null, sequence: 0, loading: false, error: "", timer: null, opened: new Set(), extras: new Map(), views: {}, restore: false,
    });
    return states.get(key);
  }

  function errorBox(message, retry) {
    const box = append(el("div", "memory-error"), el("p", "danger-copy", message));
    box.setAttribute("role", "alert");
    if (retry) box.append(button("重新读取", retry, "secondary", true));
    return box;
  }

  function named(profile, className = "") {
    const node = el("span", className, profileName(profile)); node.dataset.memoryName = profile.id; return node;
  }

  function avatar(profile) {
    const node = el("span", "memory-avatar", [...profileName(profile)][0] || "人");
    node.dataset.memoryAvatar = profile.id; node.setAttribute("aria-hidden", "true"); return node;
  }

  function hydrate(profiles) {
    const ids = [...new Set(profiles.filter(p => p.owner === "person").map(p => p.id))]
      .filter(id => !namePending.has(id) && (!names.has(id) || names.get(id).until <= Date.now())).slice(0, 50);
    if (!ids.length) return;
    ids.forEach(id => namePending.add(id));
    const started = epoch;
    api("memory.names", { ids }).then(result => {
      if (epoch !== started) return;
      for (const item of result.items || []) names.set(item.id, { ...item, until: Date.now() + (["manual", "resolved"].includes(item.name_status) ? 3600000 : 60000) });
      for (const node of document.querySelectorAll("[data-memory-name]")) {
        const item = names.get(node.dataset.memoryName); if (item) node.textContent = item.name;
      }
      for (const node of document.querySelectorAll("[data-memory-avatar]")) {
        const item = names.get(node.dataset.memoryAvatar); if (item) node.textContent = [...(item.name || "人")][0];
      }
    }).catch(() => {
      if (epoch === started) ids.forEach(id => names.set(id, { name: profiles.find(p => p.id === id)?.name || "未获取昵称", until: Date.now() + 60000 }));
    }).finally(() => { if (epoch === started) ids.forEach(id => namePending.delete(id)); });
  }

  function rememberScroll() { const state = states.get(activeKey); if (state && root.isConnected) state.scroll = window.scrollY; }
  function go(params = {}) { rememberScroll(); closeMemory(false); ui.navigate(params); }
  function resetPages(state) { state.offset = 0; state.offsets = {}; state.opened.clear(); state.extras.clear(); state.views = {}; }

  function control(label, key, state, options, type = "select") {
    const node = field(label, "memory-browser-" + key, state[key], options ? { options: options.map(([value, label]) => ({ value, label })) } : { type: "search", placeholder: label });
    const input = node.querySelector("input,select");
    input.addEventListener(type === "search" ? "input" : "change", () => {
      if (key === "mode") {
        state.views[state.mode] = { data: state.data, offset: state.offset, offsets: state.offsets, scroll: window.scrollY };
        state.mode = input.value; state.sequence++; state.loading = false;
        Object.assign(state, state.views[state.mode] || { data: null, offset: 0, offsets: {}, scroll: 0 });
        state.restore = true;
        if (state.data && !state.data.needsReload) { draw(state); restore(state); } else load(state);
        return;
      }
      state[key] = input.value; resetPages(state); state.sequence++;
      state.loading = false; state.data = null;
      clearTimeout(state.timer);
      if (type === "search") state.timer = setTimeout(() => load(state), 300);
      else load(state);
    });
    return node;
  }

  async function load(state, more = "") {
    clearTimeout(state.timer);
    const sequence = ++state.sequence;
    const args = { query: state.query, offset: state.offset, kind: state.kind, profile_id: state.profile,
      mode: state.mode, scope: state.scope, source: state.source, state: state.state, attribute: state.attribute };
    if (more) { args.attribute = more; args.offsets = { [more]: state.offsets[more] || 0 }; }
    state.loading = true; state.error = "";
    if (isActive(state)) draw(state);
    try {
      const result = await api(state.key === "profiles" ? "memory.profiles" : "memory.records", args);
      if (sequence !== state.sequence) return;
      if (more && state.data?.groups) {
        const next = result.groups[more], previous = state.data.groups[more];
        next.items = [...new Map([...(previous?.items || []), ...next.items].map(row => [row.id, row])).values()];
        state.data.groups[more] = next;
      } else state.data = result;
      state.loading = false;
      if (result.memory_count !== undefined) ui.onCount(result.memory_count);
      if (state.mode !== "grouped" && state.offset > 0 && state.offset >= result.total) {
        state.offset = Math.max(0, Math.floor((result.total - 1) / result.limit) * result.limit); return load(state);
      }
      if (isActive(state)) { draw(state); restore(state); hydrate(state.key === "profiles" ? result.items : result.owners || []); }
      // Only explicitly visited pages are cached, so returning preserves expanded groups.
    } catch (error) {
      if (sequence !== state.sequence) return;
      state.loading = false; state.error = error.message || String(error);
      if (isActive(state)) draw(state);
    }
  }

  function restore(state) {
    if (state.restore) { state.restore = false; requestAnimationFrame(() => { if (isActive(state)) window.scrollTo(0, state.scroll); }); }
  }

  function pagination(state, total, size) {
    const box = el("div", "memory-pagination");
    const prev = button("上一页", () => { state.offset = Math.max(0, state.offset - size); load(state); }, "secondary", true);
    const next = button("下一页", () => { state.offset += size; load(state); }, "secondary", true);
    prev.disabled = state.loading || state.offset === 0; next.disabled = state.loading || state.offset + size >= total;
    prev.dataset.disabled = String(prev.disabled); next.dataset.disabled = String(next.disabled);
    return append(box, el("span", "muted", `共 ${total} 项 · 第 ${Math.floor(state.offset / size) + 1} / ${Math.max(1, Math.ceil(total / size))} 页`), prev, next);
  }

  function counts(profile) {
    return append(el("div", "memory-counts"),
      el("span", "", `有效记忆 ${profile.count}`), el("span", "", `稳定画像 ${profile.stable_count}`),
      el("span", "", `重要或主动保护 ${profile.protected_count}`), el("span", "", `有依据的推断 ${profile.inferred_count}`));
  }

  function attributeCounts(profile) {
    const attrs = el("div", "memory-tags");
    ATTRIBUTES.forEach((attr, index) => attrs.append(el("span", `memory-chip attr-${index}`, `${attr} ${profile.attributes[attr] || 0}`)));
    return attrs;
  }

  function profileCard(profile) {
    const node = button("", () => go({ profile: profile.id }), "secondary"); node.className = "memory-profile-card";
    node.append(append(el("div", "memory-person-head"), avatar(profile), append(el("div", "memory-person-meta"),
      named(profile, "memory-person-name"), el("span", "muted", profile.owner === "person" ? `QQ ${number(profile)}` : `人格：${profile.persona_name || "未记录"}`))));
    node.append(badge(ownerKind(profile)), counts(profile));
    return append(node, attributeCounts(profile), el("p", "memory-time", `最近记忆：${stamp(profile.latest_at)}`), el("span", "memory-open-hint", "查看完整画像 →"));
  }

  function profileHeader(profile) {
    const box = el("section", "card memory-profile-header");
    return append(box, append(el("div", "memory-person-head"), avatar(profile), append(el("div", "memory-person-meta"),
      named(profile, "memory-person-name"), el("span", "muted", profile.owner === "person" ? `QQ ${number(profile)}` : `人格：${profile.persona_name}`)), badge(ownerKind(profile))),
      counts(profile), attributeCounts(profile), el("p", "memory-time", `最近记忆：${stamp(profile.latest_at)} · 最近更新：${stamp(profile.updated_at)}`));
  }

  function draw(state) {
    const focus = root.contains(document.activeElement) ? document.activeElement : null;
    const focusName = focus?.name, start = focus?.selectionStart, end = focus?.selectionEnd;
    const toolbar = el("div", "memory-toolbar");
    if (state.key !== "profiles") toolbar.append(button("← 返回画像列表", () => go(), "secondary"));
    toolbar.append(button(state.key === "all" ? "多用户画像" : "全部记忆", () => go(state.key === "all" ? {} : { view: "all" }), "secondary"));
    const profile = state.data?.profile;
    if (!state.profile || profile?.current || profile?.owner === "person") toolbar.append(button("添加记忆", () => edit({}, profile), "primary"));
    if (drafts.size) toolbar.append(button(`编辑草稿（${drafts.size}）`, showDrafts, "secondary"));
    toolbar.append(button("刷新本页", () => { state.data = null; state.extras.clear(); state.views = {}; load(state); }, "secondary"));
    root.replaceChildren(toolbar);
    if (state.key === "profiles") {
      root.append(append(el("div", "memory-intro"), el("h2", "", "记忆里的每个人"), el("p", "muted", "先选择一个画像，查看它的记忆、依据和来处。自身档案随人格名称分别保留。")));
      root.append(append(el("div", "memory-filterbar"), control("搜索名字或 QQ 号", "query", state, null, "search"),
        control("档案类型", "kind", state, [["", "全部档案"], ["current", "当前自身画像"], ["person", "QQ 人物"], ["historical", "历史自身画像"]])));
    } else {
      if (profile) root.append(profileHeader(profile));
      else if (!state.profile) root.append(el("h2", "", "全部记忆"));
      const filters = state.data?.filters || { scope: [], source: [] };
      const bar = append(el("div", "memory-filterbar"), control("搜索结论、依据或标签", "query", state, null, "search"),
        control("画像属性", "attribute", state, [["", "全部属性"], ...ATTRIBUTES.map(a => [a, a])]),
        control("场合", "scope", state, [["", "全部场合"], ...filters.scope.map(s => [s, scopeLabel(s)])]),
        control("来源", "source", state, [["", "全部来源"], ...filters.source.map(s => [s, sourceName(s)])]),
        control("保留状态", "state", state, STATES));
      if (state.profile) bar.append(control("浏览方式", "mode", state, [["grouped", "五类属性分组"], ["timeline", "按发生时间"]]));
      root.append(bar, el("p", "memory-time", `时间按 ${ui.settings().character.timezone} 显示；五类属性用于整理资料，稳定特征另行标记。`));
    }
    if (state.error) root.append(errorBox(state.error, () => load(state)));
    if (state.loading) { const status = el("p", "memory-loading", "正在读取本页资料…"); status.setAttribute("role", "status"); root.append(status); }
    if (state.data) {
      if (state.key === "profiles") {
        const grid = el("div", "memory-profile-grid"); state.data.items.forEach(p => grid.append(profileCard(p)));
        root.append(state.data.items.length ? grid : empty("没有匹配的画像", "可调整筛选，或在当前人格下添加第一条记忆。"), pagination(state, state.data.total, 12));
      } else if (state.mode === "grouped") {
        for (const [attr, group] of Object.entries(state.data.groups || {})) {
          const section = el("section", "memory-attribute-section");
          section.append(append(el("header", "memory-group-heading"), el("h3", "", attr), badge(`${group.total} 条`)));
          group.items.forEach(row => section.append(memoryCard(row, state)));
          if (!group.items.length) section.append(el("p", "memory-group-empty", "暂无记录"));
          if (group.has_more) { const more = button(`继续加载${attr}`, () => { state.offsets[attr] = group.items.length; load(state, attr); }, "secondary"); more.disabled = state.loading; more.dataset.disabled = String(state.loading); section.append(more); }
          root.append(section);
        }
      } else {
        const list = el("div", "memory-record-list");
        state.data.items.forEach(row => list.append(memoryCard(row, state)));
        root.append(list);
        if (!state.data.items.length) root.append(empty("没有匹配的记忆", "请调整搜索或筛选条件。"));
        root.append(pagination(state, state.data.total, 20));
      }
    }
    if (focusName) {
      const replacement = [...root.querySelectorAll("input,select")].find(n => n.name === focusName);
      replacement?.focus({ preventScroll: true });
      if (typeof start === "number" && replacement?.setSelectionRange && replacement.type === "search") replacement.setSelectionRange(start, end);
    }
  }

  function flags(row) {
    return [row.active === false && "已替换", row.stable && "稳定画像", row.inferred && "有依据的推断", row.important && "重要保留", row.protected && "主动记忆保护"].filter(Boolean);
  }

  function terms(items) {
    const node = el("dl", "memory-facts");
    for (const [label, value] of items) append(node, el("dt", "", label), el("dd", "", text(value)));
    return node;
  }

  function lazy(label, key, state, fetch, display) {
    const node = append(el("details", "memory-extra"), el("summary", "", label));
    const body = el("div", "memory-extra-body"); node.append(body);
    let loaded = false, loading = false;
    async function read() {
      if (loading || loaded) return;
      loading = true; body.replaceChildren(el("p", "muted", "正在读取…"));
      let pending = state.extras.get(key);
      if (!pending) { pending = Promise.resolve().then(fetch); state.extras.set(key, pending); }
      try { const result = await pending; body.replaceChildren(display(result)); loaded = true; }
      catch (error) { if (state.extras.get(key) === pending) state.extras.delete(key); body.replaceChildren(errorBox(error.message || String(error), read)); }
      finally { loading = false; }
    }
    node.addEventListener("toggle", () => {
      if (node.open) { state.opened.add(key); read(); } else state.opened.delete(key);
    });
    if (state.opened.has(key)) { node.open = true; queueMicrotask(read); }
    return node;
  }

  function memoryOwner(row, state) {
    return state.data?.owners?.find(profile => profile.id === row.identity) || state.data?.profile;
  }

  function memoryCard(row, state) {
    const judgment = row.judgment || row.text || "未记录";
    const node = el("button", "memory-record-card"); node.type = "button"; node.dataset.memoryId = row.id;
    node.setAttribute("aria-haspopup", "dialog"); node.setAttribute("aria-controls", drawer.id);
    node.setAttribute("aria-label", `查看记忆详情：${judgment}`);
    node.addEventListener("click", event => {
      // Selecting text for copying should not open the drawer on pointer release.
      const selection = window.getSelection();
      if (event.detail > 0 && selection && !selection.isCollapsed && node.contains(selection.anchorNode) && node.contains(selection.focusNode)) return;
      openMemory(row, state);
    });
    const retained = row.important || row.protected;
    const star = el("span", "memory-record-star" + (retained ? " is-protected" : ""), retained ? "★" : "☆");
    star.title = row.important && row.protected ? "重要保留、主动记忆保护" : row.important ? "重要保留" : row.protected ? "主动记忆保护" : "未标记重要或主动保护";
    star.setAttribute("role", "img"); star.setAttribute("aria-label", star.title);
    const time = el("span", "memory-record-time", stamp(row.occurred_at)); time.title = "经历或获知时间";
    const top = append(el("span", "memory-record-top"), star, el("span", "memory-record-strength", `强度 ${text(row.strength)}`), time);
    if (state.mode !== "grouped") top.append(el("span", "memory-record-attribute", row.attribute || "未分类"));
    if (!state.profile) {
      const owner = memoryOwner(row, state);
      top.append(owner ? named(owner, "memory-record-owner") : el("span", "memory-record-owner", row.persona_name || row.person_id || "未记录归属"));
    }
    node.append(top, el("span", "memory-record-judgment", judgment));
    if (row.reasoning) node.append(el("span", "memory-record-reasoning", row.reasoning));
    if (row.tags?.length) {
      const tags = el("span", "memory-record-tags");
      row.tags.forEach(tag => tags.append(el("span", "memory-chip", tag)));
      node.append(tags);
    }
    return node;
  }

  function openMemory(row, state) {
    rememberScroll();
    drawerReturn = { id: row.id, state, scroll: window.scrollY };
    selectedMemory = { row, state, owner: memoryOwner(row, state) };
    drawMemoryDetail(); drawerBody.scrollTop = 0;
    if (!drawer.open) drawer.showModal();
    drawerClose.focus({ preventScroll: true });
  }

  function closeMemory(restoreFocus = true) {
    if (!restoreFocus) drawerReturn = null;
    selectedMemory = null;
    if (drawer.open) drawer.close();
  }

  function drawMemoryDetail() {
    if (!selectedMemory) return;
    const { row, state, owner } = selectedMemory;
    const scroll = drawerBody.scrollTop;
    const focusedAction = drawerBody.contains(document.activeElement) ? document.activeElement.dataset.memoryAction : "";
    drawerOwner.replaceChildren(owner ? named(owner) : el("span", "", row.persona_name || row.person_id || "未记录归属"));
    if (owner) drawerOwner.append(el("span", "muted", owner.owner === "person" ? `QQ ${number(owner)}` : ownerKind(owner)));
    drawerBody.replaceChildren(memoryDetail(row, state));
    drawerBody.scrollTop = scroll;
    if (focusedAction && !editor.open) drawerBody.querySelector(`[data-memory-action="${focusedAction}"]`)?.focus({ preventScroll: true });
  }

  function memoryDetail(row, state) {
    const node = el("article", "memory-item memory-detail-content");
    const owner = memoryOwner(row, state);
    const badges = append(el("div", "memory-tags"), badge(row.attribute || "未分类"), ...flags(row).map(f => badge(f)));
    if (owner && !state.profile) { const link = button(profileName(owner), () => go({ profile: owner.id }), "secondary", true); link.dataset.memoryName = owner.id; badges.prepend(link); }
    node.append(badges, el("p", "memory-judgment", row.judgment || row.text || "未记录"),
      append(el("div", "memory-evidence"), el("span", "memory-label", "事实依据"), el("p", "", row.reasoning || "未记录")));
    const tags = el("div", "memory-tags");
    (row.tags || []).forEach(tag => tags.append(el("span", "memory-chip", tag)));
    if (!tags.childElementCount) tags.append(el("span", "muted", "无检索标签"));
    const c = ui.settings().memory;
    const retention = row.important || row.protected ? "免于遗忘" : row.useful_score >= c.long_threshold ? "长期保留" : row.useful_score >= c.medium_threshold ? "中档" : "普通记忆";
    node.append(tags, terms([["经历或获知", stamp(row.occurred_at)], ["来源", sourceName(row.source)], ["场合", row.scope ? scopeLabel(row.scope) : "未记录"], ["强度", row.strength], ["有用分", row.useful_score], ["保留阶段", row.active === false ? "已替换，停止召回" : retention]]));
    const actions = append(el("div", "actions"), button("编辑", () => edit(row, owner), "secondary", true),
      button(row.important ? "取消重要" : "标记重要", event => mark(row, event.currentTarget), "secondary", true),
      button("删除", () => ui.confirmDelete(row, () => remove(row)), "danger", true));
    [...actions.children].forEach((control, index) => { control.dataset.memoryAction = ["edit", "important", "delete"][index]; });
    node.append(actions);
    node.append(lazy("版本与来源关联", row.id + ":detail", state, () => api("memory.detail", { id: row.id }), result => {
      const item = result.record;
      return terms([["创建时间", stamp(item.created_at)], ["更新时间", stamp(item.updated_at)], ["版本", item.version], ["记忆编号", item.id], ["来源关联", result.source_keys], ["替换为", result.replaced_by]]);
    }));
    node.append(lazy("变更记录", row.id + ":history", state, () => api("memory.history", { id: row.id }), result => history(result, row, state)));
    node.append(lazy("查看来源", row.id + ":sources", state, () => api("memory.sources", { id: row.id }), result => sourceList(result, row, state)));
    return node;
  }

  function history(result, current, state) {
    const box = el("div", "stack"); let newer = result.current || current;
    const fields = { text: "结论", reasoning: "事实依据", attribute: "画像属性", tags: "标签", stable: "稳定画像", inferred: "有依据的推断", important: "重要保留", active: "有效状态" };
    for (const entry of result.records || []) {
      const old = entry.record || entry, changed = Object.keys(fields).filter(k => JSON.stringify(old[k]) !== JSON.stringify(newer[k]));
      const section = append(el("section", "memory-version"), el("h4", "", `版本 ${entry.version} → ${newer.version || "当前"}`), el("p", "memory-time", `新版本更新时间：${stamp(newer.updated_at)}`));
      for (const key of changed) section.append(append(el("div", "memory-diff"), el("strong", "", fields[key]), el("p", "", `原：${text(old[key])}`), el("p", "", `新：${text(newer[key])}`)));
      if (!changed.length) section.append(el("p", "muted", "所展示字段未变化"));
      section.append(append(el("details"), el("summary", "", "查看此版本结论与依据"), el("p", "memory-judgment", old.judgment || old.text || "未记录"), el("p", "memory-evidence", old.reasoning || "未记录")));
      box.append(section); newer = old;
    }
    if (result.has_more) {
      const offset = result.offset + result.limit, previous = newer;
      box.append(lazy("继续读取更早版本", current.id + ":history:" + offset, state,
        () => api("memory.history", { id: current.id, offset }), next => history({ ...next, current: previous }, current, state)));
    }
    return box.childElementCount ? box : el("p", "muted", "尚无历史版本");
  }

  function sourceList(result, row, state) {
    const box = el("div", "stack");
    if (result.notice) box.append(el("p", "muted", result.notice));
    for (const item of result.items || []) {
      const label = { event: "经历原记录", observation: "见闻原记录", journal: "日记或笔记原文", chat: "聊天来源" }[item.kind] || "来源记录";
      if (!item.available) { box.append(el("p", "muted", `${label}：${item.notice}`)); continue; }
      box.append(lazy(label, row.id + ":source:" + item.key, state, () => api("memory.sources", { id: row.id, source_key: item.key }), response => {
        const source = response.record, content = el("div", "stack");
        if (source.title) content.append(el("h4", "", source.title));
        content.append(terms([["场合", source.scope ? scopeLabel(source.scope) : "未记录"], ["经历或获知", stamp(source.occurred_at)], ["创建时间", stamp(source.created_at)], ["所属日期", source.day], ["类型", sourceName(source.module || source.kind)], ["状态", source.status]]));
        for (const [key, label] of [["text", "原记录正文"], ["raw_text", "来源原文"], ["factual_summary", "事实摘要"], ["impression", "角色感想"]]) {
          if (source[key]) content.append(append(el("section", "memory-source-text"), el("h4", "", label), el("p", "", text(source[key]))));
        }
        return content;
      }));
    }
    return box;
  }

  function afterWrite(receipt) {
    ui.onCount(receipt.memory_count);
    const identity = receipt.profile?.id, old = receipt.previous, row = receipt.record;
    const id = row?.id || receipt.deleted_id;
    const matches = (item, state, attribute = "") => item && (!state.profile || item.identity === state.profile)
      && (!attribute || item.attribute === attribute) && (!state.attribute || item.attribute === state.attribute)
      && (!state.scope || item.scope === state.scope) && (!state.source || item.source === state.source)
      && (!state.query.trim() || [item.text, item.reasoning, ...(item.tags || [])].join(" ").toLowerCase().includes(state.query.trim().toLowerCase()))
      && (state.state === "all" || (state.state === "replaced" ? item.active === false : item.active !== false
        && (state.state === "active" || (state.state === "protected" ? item.important || item.protected : item[state.state]))));
    const updatePage = (page, state, attribute = "") => {
      const had = matches(old, state, attribute), has = matches(row, state, attribute);
      const present = page.items.some(item => item.id === id), capacity = Math.max(page.items.length, page.limit);
      const change = Number(!!has) - Number(!!had);
      page.total = Math.max(0, page.total + change);
      page.items = page.items.filter(item => item.id !== id);
      if (has && (present || attribute || page.offset === 0)) page.items.push(row);
      page.items.sort((a, b) => (Date.parse(b.occurred_at) || -Infinity) - (Date.parse(a.occurred_at) || -Infinity) || a.id.localeCompare(b.id));
      page.items = page.items.slice(0, capacity);
      page.has_more = attribute ? page.items.length < page.total : page.offset + page.limit < page.total;
      // A removal shifts later page boundaries; refresh only that affected page.
      if (!attribute && change) page.needsReload = true;
    };
    for (const state of states.values()) {
      if (state.key === "profiles" || state.key === "all" || !identity || state.profile === identity) {
        state.sequence++; state.loading = false;
        for (const key of state.extras.keys()) if (key.startsWith(id + ":")) state.extras.delete(key);
        if (state.key === "profiles") {
          if (!state.data) continue;
          const index = state.data.items.findIndex(item => item.id === identity);
          if (index >= 0) state.data.items[index] = receipt.profile;
          else state.data = null;
          continue;
        }
        const visited = new Set();
        for (const data of [state.data, ...Object.values(state.views).map(view => view.data)]) {
          if (!data || visited.has(data)) continue;
          visited.add(data);
          if (state.profile === identity) data.profile = receipt.profile;
          if (receipt.profile) data.owners = (data.owners || []).filter(p => p.id !== identity).concat(receipt.profile);
          if (data.groups) {
            data.total = Math.max(0, data.total + Number(!!matches(row, state)) - Number(!!matches(old, state)));
            for (const [attribute, group] of Object.entries(data.groups)) updatePage(group, state, attribute);
          } else updatePage(data, state);
          for (const field of ["scope", "source"]) if (row?.[field] && !data.filters[field].includes(row[field])) data.filters[field].push(row[field]);
        }
      }
    }
    if (selectedMemory?.row.id === id) {
      if (row) { selectedMemory.row = row; selectedMemory.owner = receipt.profile || selectedMemory.owner; drawMemoryDetail(); }
      else closeMemory();
    }
    const state = states.get(activeKey);
    if (state && root.isConnected) { if (state.data && !state.data.needsReload) draw(state); else load(state); }
  }

  async function mark(row, control) {
    control.disabled = true;
    try { const result = await api("memory.update", { id: row.id, expected_version: row.version, patch: { important: !row.important } }); afterWrite(result); ui.notice("重要标记已保存"); }
    catch (error) { ui.notice(error.message, true); }
    finally { control.disabled = false; }
  }

  async function remove(row) {
    if (saving.has(row.id)) return false;
    saving.add(row.id);
    try { const result = await api("memory.delete", { id: row.id }); drafts.delete(row.id); afterWrite(result); ui.notice("已删除记忆及历史版本，原来源保留"); return true; }
    catch (error) { ui.notice(error.message, true); return false; }
    finally { saving.delete(row.id); }
  }

  function formValue(form) {
    return Object.fromEntries([...form.querySelectorAll("[name]")].map(input => [input.name, input.type === "checkbox" ? input.checked : input.value]));
  }

  function showDrafts() {
    const body = append(el("div", "stack"), append(el("div", "dialog-header"), el("h2", "", "未保存的记忆草稿"), button("关闭", () => editor.close())));
    for (const [key, draft] of drafts) {
      body.append(append(el("section", "memory-version"), el("p", "memory-judgment", draft.value.text || "尚未填写结论"),
        append(el("div", "actions"), button("继续编辑", () => edit(draft.row, draft.profile)),
          button("丢弃草稿", () => { if (!saving.has(key)) { drafts.delete(key); showDrafts(); } }, "danger", true))));
    }
    if (!drafts.size) body.append(el("p", "muted", "没有未保存的草稿"));
    editor.replaceChildren(body); editorKey = ""; if (!editor.open) editor.showModal();
  }

  function edit(row, profile) {
    let key = row.id || "new:" + (profile?.id || "current");
    editorKey = key;
    const saved = drafts.get(key);
    const value = saved?.value || { text: row.judgment || row.text || "", reasoning: row.reasoning || "", tags: (row.tags || []).join("\n"), attribute: row.attribute || "事实属性", scope: "global", person_id: "", stable: !!row.stable, inferred: !!row.inferred, important: !!row.important };
    let expected = saved?.version ?? row.version;
    const form = el("form", "stack"), message = el("p", "memory-editor-message"); message.setAttribute("role", "status");
    const close = button("关闭并保留草稿", () => editor.close(), "secondary");
    form.append(append(el("div", "dialog-header"), el("h2", "", row.id ? "编辑记忆" : "添加记忆"), close));
    form.append(field("记忆结论", "text", value.text, { type: "textarea", rows: 5, required: true }), field("事实依据", "reasoning", value.reasoning, { type: "textarea", rows: 4, hint: "填写来源证据，不填写模型思考过程。" }));
    form.append(append(el("div", "form-grid"), field("画像属性", "attribute", value.attribute, { options: ATTRIBUTES }), field("标签（每行一个）", "tags", value.tags, { type: "textarea", rows: 3 })));
    for (const [id, label] of [["stable", "属于稳定画像"], ["inferred", "这是有依据的推断"], ["important", "重要记忆，免于遗忘"]]) {
      const check = el("input"); check.type = "checkbox"; check.name = id; check.checked = value[id]; form.append(append(el("label", "inline-check"), check, el("span", "", label)));
    }
    if (row.id) form.append(el("p", "hint", `画像：${profile ? profileName(profile) : row.persona_name || row.person_id} · ${scopeLabel(row.scope)}。保留原归属和场合。`));
    else {
      if (profile) form.append(el("p", "hint", `记入画像：${profileName(profile)}`));
      else form.append(field("QQ 身份", "person_id", value.person_id, { hint: "留空记入当前人格的自身画像。" }));
      form.append(field("所属场合", "scope", value.scope, { options: scopeOptions() }));
    }
    form.querySelector('[name="text"]').maxLength = 8000; form.querySelector('[name="reasoning"]').maxLength = 8000;
    const saveDraft = () => drafts.set(key, { value: formValue(form), version: expected, row, profile });
    form.addEventListener("input", saveDraft); form.addEventListener("change", saveDraft);
    const footer = el("div", "dialog-actions");
    if (row.id) footer.append(button("查看最新版本", async () => {
      try {
        const result = await api("memory.detail", { id: row.id }); expected = result.record.version; saveDraft();
        message.replaceChildren(el("strong", "", `最新版本 ${expected}，你的草稿已保留；核对后可保存。`), el("p", "memory-judgment", result.record.text), el("p", "memory-evidence", result.record.reasoning || "未记录依据"));
      } catch (error) { message.textContent = error.message; }
    }, "secondary"));
    const submit = button("保存记忆", () => form.requestSubmit(), "primary"); footer.append(submit); form.append(message, footer);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (submit.disabled || saving.has(key)) return;
      saveDraft(); const sent = copy(drafts.get(key)), { scope, person_id, tags, ...patch } = sent.value;
      const savingKey = key; saving.add(savingKey); submit.disabled = true; message.textContent = "正在保存…";
      try {
        const result = await api("memory.update", { id: row.id || "", ...(row.id ? { expected_version: sent.version } : { profile_id: profile?.id || "", person_id: person_id || "", scope }), patch: { ...patch, tags: tags.split(/\r?\n/).map(t => t.trim()).filter(Boolean) } });
        const latest = drafts.get(key);
        if (JSON.stringify(latest?.value) === JSON.stringify(sent.value)) { drafts.delete(key); if (editorKey === key) editor.close(); }
        else if (latest) {
          expected = result.record.version;
          const wasNew = !row.id, previousKey = key; row = result.record; key = row.id;
          drafts.delete(previousKey); drafts.set(key, { ...latest, version: expected, row, profile });
          if (editorKey === previousKey) {
            editorKey = key;
            if (wasNew && editor.open) edit(row, profile);
            else message.textContent = "提交的内容已保存，继续编辑的草稿仍保留。";
          }
        }
        afterWrite(result);
        ui.notice("记忆已保存");
      } catch (error) { message.textContent = error.message; }
      finally { saving.delete(savingKey); submit.disabled = false; }
    });
    editor.replaceChildren(form); if (!editor.open) editor.showModal();
  }

  return {
    render(params = {}) {
      const key = params.profile ? "profile:" + params.profile : params.view === "all" || params.attribute ? "all" : "profiles";
      const changed = activeKey !== key;
      if (changed) closeMemory(false);
      activeKey = key; const state = getState(key, params.profile || "");
      if (params.attribute && ATTRIBUTES.includes(params.attribute) && state.routeAttribute !== params.attribute) {
        state.routeAttribute = params.attribute; state.attribute = params.attribute; state.data = null; resetPages(state);
      }
      if (changed || !root.isConnected) state.restore = true;
      draw(state);
      if ((!state.data || state.data.needsReload) && !state.loading) queueMicrotask(() => load(state));
      else if (state.data) hydrate(key === "profiles" ? state.data.items : state.data.owners || []);
      return root;
    },
    captureScroll: rememberScroll,
    restoreScroll() { const state = states.get(activeKey); if (state?.data) { state.restore = true; restore(state); } },
    invalidate() {
      closeMemory(false);
      epoch++; names.clear(); namePending.clear();
      for (const state of states.values()) { clearTimeout(state.timer); state.sequence++; state.data = null; state.loading = false; state.extras.clear(); state.views = {}; }
    },
    refresh() { this.invalidate(); const state = states.get(activeKey); if (state) return load(state); },
    hasDrafts: () => drafts.size > 0,
  };
}
