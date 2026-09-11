// Lazy extraction progress. Loading or expanding records never starts a model call.
export function createMemoryProgress(ui) {
  const { el, append, button, field } = ui;
  const labels = { pending: "待处理", processing: "处理中", waiting_retry: "等待重试", partial: "部分完成", failed: "失败待处理", completed: "已完成", no_new: "已检查无新增", historical: "已处理（历史标记）" };
  const root = el("div", "memory-progress stack");
  const opened = new Set(), details = new Map(), detailPending = new Map(), writing = new Set();
  let page = 1, status = "", data = null, sequence = 0, detailGeneration = 0, loading = false, error = "";
  function stamp(value) {
    if (value === undefined || value === null || value === "") return "未记录";
    const date = new Date(typeof value === "number" ? value * 1000 : value);
    if (!Number.isFinite(date.getTime())) return "未记录";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: ui.settings().character.timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(date);
  }
  async function load() {
    const token = ++sequence;
    loading = true; error = ""; draw();
    try {
      const result = await ui.api("memory.progress", { page, status });
      if (token !== sequence) return;
      data = result; page = result.page;
    } catch (failure) { if (token === sequence) error = failure.message; }
    finally { if (token === sequence) { loading = false; draw(); } }
  }
  async function run(action, id = "") {
    if (writing.has(id)) return;
    writing.add(id); draw();
    try {
      await ui.api(action, { id });
      details.clear(); detailGeneration++;
      ui.notice(action === "memory.retry" ? "已安排失败项重试；本轮最多调用两次模型，已保存项不重做" : "已唤醒待处理队列，达到重试上限的批次仍保持停止");
      await load();
    } catch (failure) { ui.notice(failure.message, true); }
    finally { writing.delete(id); draw(); }
  }
  function showDetail(box, value) {
    box.replaceChildren();
    if (value.record) {
      box.append(el("pre", "memory-text", JSON.stringify(value.record, null, 2)));
      return;
    }
    for (const entry of value.entries || []) {
      box.append(append(el("div", "memory-error"), el("strong", "", `${entry.id} · ${entry.status === "saved" ? "已保存" : "未通过"}`),
        entry.error ? el("p", "danger-copy", entry.error) : null,
        el("pre", "memory-text", JSON.stringify(entry.candidate, null, 2))));
    }
    for (const material of value.materials || []) {
      box.append(append(el("div", "stack"), el("strong", "", `来源 ${material.source_id || ""}：${material.source || "未记录"} · ${stamp(material.occurred_at)}`),
        el("p", "memory-text", material.text)));
    }
    if (!(value.materials || []).length) box.append(el("p", "hint", "本批临时正文已清理；来源原记录继续保留。"));
    const ids = [...new Set((value.receipts || []).flatMap(item => item?.memory_ids || []))];
    box.append(el("p", "memory-text", `关联记忆：${ids.length ? ids.join("、") : "无新增"}`));
  }
  async function expand(id, box) {
    if (details.has(id)) { showDetail(box, details.get(id)); return; }
    box.replaceChildren(el("p", "muted", "正在读取本条详情…"));
    const generation = detailGeneration, key = `${generation}:${id}`;
    try {
      if (!detailPending.has(key)) detailPending.set(key, ui.api("memory.progress_detail", { id }));
      const result = await detailPending.get(key);
      if (generation !== detailGeneration) return;
      details.set(id, result);
      if (details.size > 40) details.delete(details.keys().next().value);
      if (box.isConnected) showDetail(box, result);
    } catch (failure) {
      if (box.isConnected) box.replaceChildren(el("p", "danger-copy", failure.message), button("重新读取", () => expand(id, box)));
    } finally { detailPending.delete(key); }
  }
  function draw() {
    root.replaceChildren();
    const controls = el("div", "actions");
    const filter = field("处理状态", "extraction-status", status, { options: [{ value: "", label: "全部状态" }, ...Object.entries(labels).map(([value, label]) => ({ value, label }))] });
    filter.querySelector("select").addEventListener("change", event => { status = event.target.value; page = 1; data = null; load(); });
    const refresh = button("刷新提炼进度", () => { details.clear(); detailGeneration++; load(); }); refresh.disabled = loading;
    const process = button("处理待提炼材料", () => run("memory.process")); process.disabled = writing.has("");
    controls.append(filter, refresh, process); root.append(controls);
    root.append(el("p", "hint", "首次失败后至少隔 60 秒自动重试一次，仍失败则停止。刷新和查看详情不调用模型；手动重试会重新开放一次初始调用和一次自动重试。"));
    if (error) root.append(el("p", "danger-copy", error));
    if (loading) root.append(el("p", "muted", "正在读取进度…"));
    if (!data) return;
    const counts = data.counts || {};
    root.append(el("p", "hint", `待处理 ${counts.pending || 0} · 等待重试 ${counts.waiting_retry || 0} · 部分完成 ${counts.partial || 0} · 失败待处理 ${counts.failed || 0}${counts.processing ? " · 队列正在处理" : ""}`));
    for (const row of data.items || []) {
      const card = el("article", "memory-record stack");
      const source = (row.sources || []).join("、") || "来源未记录";
      card.append(el("strong", "", `${row.persona_name || "记忆材料"} · ${source}`),
        el("p", "", `${row.status_label || labels[row.status]} · 已保存 ${row.saved_count || 0} 条${row.failed_count ? ` · 未通过 ${row.failed_count} 条` : ""} · 处理尝试 ${row.total_attempts ?? "未记录"} 次`),
        el("p", "muted", `${stamp(row.created_at)} · ${ui.scopeLabel(row.scope || "global")}`));
      if (row.error) card.append(el("p", "danger-copy", row.error));
      if (row.retry_at) card.append(el("p", "hint", `最早再次处理：${stamp(row.retry_at)}`));
      if (row.can_retry) {
        const retry = button("只重试未完成项", () => run("memory.retry", row.id));
        retry.disabled = writing.has(row.id); card.append(retry);
      }
      const disclosure = el("details", "memory-extra"), box = el("div", "stack");
      disclosure.append(el("summary", "", "查看材料与处理结果"), box);
      disclosure.open = opened.has(row.id);
      disclosure.addEventListener("toggle", () => {
        if (disclosure.open) { opened.add(row.id); expand(row.id, box); }
        else opened.delete(row.id);
      });
      card.append(disclosure); root.append(card);
      if (disclosure.open && details.has(row.id)) showDetail(box, details.get(row.id));
    }
    if (!(data.items || []).length) root.append(el("p", "muted", "暂无符合条件的提炼记录"));
    const pages = Math.max(1, Math.ceil(data.total / 20));
    const previous = button("上一页", () => { page--; load(); }); previous.disabled = loading || page <= 1;
    const next = button("下一页", () => { page++; load(); }); next.disabled = loading || page >= pages;
    root.append(append(el("div", "actions"), previous, el("span", "", `第 ${page} / ${pages} 页 · ${data.total} 条`), next));
  }
  return { render() { draw(); if (!data && !loading) queueMicrotask(load); return root; } };
}
