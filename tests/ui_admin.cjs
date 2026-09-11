/* Administration behavior against the same backend fixtures as the API viewer. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
module.exports = async (page, go, contract) => {
  const field = (name) => page.locator(`[name="${name}"]`);
  const click = (name) => page.getByRole("button", { name, exact: true }).click();
  const refresh = () => click("刷新数据");
  const save = async (label) => { await click(label); await page.getByText("设置已保存并应用，已有记录继续保留", { exact: true }).waitFor(); };
  const posted = () => page.evaluate(() => window.calls.findLast((call) => call.endpoint === "settings" && call.method === "POST").body);
  const screenshotDir = path.resolve(__dirname, "../cache/ui-refactor"); fs.mkdirSync(screenshotDir, { recursive: true });
  const shot = async (name) => { await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: path.join(screenshotDir, name + ".png"), fullPage: true }); };
  const overflow = async (label) => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, label);
  await page.evaluate(() => {
    window.fixture.debug.templates.push(...window.fixture.context_layout_catalog.tasks.filter((row) => !row.id.startsWith("chat.")).map((row) => ({ task: row.id, label: row.label, template: "模板：" + row.id, default_template: "默认：" + row.id })));
    window.fixture.settings.memory ||= {};
    const layout = window.fixture.settings.context_layout;
    layout.baseline_order.user = ["time", ...layout.baseline_order.user.filter(id => id !== "time")];
    layout.order = structuredClone(layout.baseline_order);
  }); await refresh();
  const menus = {
    overview: ["current", "recent"], context: ["layout", "usage", "templates", "trial", "calls"],
    character: ["profile", "state", "drives", "events"], schedule: ["timeline", "settings", "archives"],
    chat: ["targets", "reply", "limits", "deliveries"], sources: ["settings", "records", "manual", "runs"],
    memory: ["records", "settings", "journals"], system: ["models", "modules", "backup", "maintenance"],
  };
  assert.equal(await page.locator("#navigation a").count(), 8);
  assert.equal(await page.evaluate(() => window.calls.some((c) => c.method === "POST")), false);
  await go("context", "layout");
  assert.equal(await field("layout_task").inputValue(), "chat.group");
  assert.equal(await field("layout_task").locator('option[value=""]').count(), 0);
  assert.equal(await page.locator(".layout-row").count(), contract.context_layout_catalog.blocks.length);
  assert.equal(await page.locator(".layout-anchor input[type=checkbox]").count(), 0);
  const cleanOrder = await page.locator('.layout-row').evaluateAll(nodes => nodes.map(node => node.dataset.blockId));
  const unsaved = () => page.evaluate(() => { const e = new Event("beforeunload", { cancelable: true }); dispatchEvent(e); return e.defaultPrevented; });
  assert.equal(await unsaved(), false);
  const handle = page.locator('[data-block-id="time"] .layout-handle');
  await handle.scrollIntoViewIfNeeded();
  let sourceBox = await handle.boundingBox();
  await page.mouse.move(sourceBox.x + 5, sourceBox.y + 10); await page.mouse.down(); await page.mouse.up();
  assert.equal(await unsaved(), false, "Releasing without a drag does not dirty the page");
  for (const cancel of [true, false]) {
    sourceBox = await handle.boundingBox();
    const target = await page.locator('[data-block-id="memory.recent"]').boundingBox();
    await page.mouse.move(sourceBox.x + 5, sourceBox.y + 10); await page.mouse.down();
    await page.mouse.move(target.x + 70, target.y + 2, { steps: 6 });
    if (cancel) await page.keyboard.press("Escape");
    else { const invalid = await page.locator('.layout-lane').first().locator('h3').boundingBox(); await page.mouse.move(invalid.x + 5, invalid.y + 3); }
    await page.mouse.up();
    assert.deepEqual(await page.locator('.layout-row').evaluateAll(nodes => nodes.map(node => node.dataset.blockId)), cleanOrder);
    assert.equal(await unsaved(), false, "Cancelled or invalid pointer drop leaves no layout draft");
  }
  for (const width of [1600]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1050 });
    for (const [name, tabs] of Object.entries(menus)) {
      for (const tab of tabs) {
        await go(name, tab);
        assert.equal(await page.locator(".secondary-tabs [role=tab]").count(), tabs.length);
        assert.equal(await page.locator(".secondary-tabs [aria-selected=true]").count(), 1);
        assert.equal(await page.locator("#section-panel").count(), 1);
        const boxes = await page.locator(".secondary-tabs .button").evaluateAll((rows) => rows.map((n) => n.getBoundingClientRect().toJSON()));
        assert.ok(new Set(boxes.map((box) => box.y)).size <= (width === 390 ? 2 : 1));
        assert.ok(boxes.every((b) => b.width > 0 && b.x >= 0 && b.right <= width));
        await overflow(`${name}/${tab} fits ${width}px`);
      }
    }
  }
  await page.setViewportSize({ width: 1600, height: 1050 });
  for (const [old, name, tab] of [["settings", "character", "profile"], ["drives", "character", "drives"], ["whitelist", "chat", "targets"], ["social", "chat", "targets"], ["journal", "sources", "records"], ["debug", "context", "calls"], ["data", "system", "backup"]]) {
    await page.evaluate((old) => { location.hash = old; }, old);
    await page.locator(`#section-panel[data-page="${name}"][data-section="${tab}"]`).waitFor();
  }
  await go("character", "profile"); await field("character.profile").fill("角色资料草稿 <img src=x onerror=window.profileXss=1>");
  await go("chat", "reply"); await field("reply.group_prompt").fill("回复草稿"); await field("reply.private_prompt").fill("私聊草稿"); await field("reply.proactive_prompt").fill("主动聊天草稿"); await field("social.interjection_interval_minutes").fill("45");
  for (const key of ["group_prompt", "private_prompt", "proactive_prompt"]) assert.equal(await field(`reply.${key}`).getAttribute("maxlength"), "8000");
  await go("chat", "limits"); await field("social.cooldown_minutes").fill("75");
  await save("保存发送限制");
  let patch = await posted(); assert.deepEqual(Object.keys(patch), ["social"]); assert.equal(Object.hasOwn(patch.social, "interjection_interval_minutes"), false);
  await go("chat", "reply"); assert.equal(await field("reply.group_prompt").inputValue(), "回复草稿");
  await page.evaluate(() => { window.failNext = true; }); await click("保存回复与插话设置");
  await page.getByText("测试来源暂时不可用", { exact: true }).waitFor(); await refresh();
  assert.equal(await field("reply.group_prompt").inputValue(), "回复草稿");
  assert.equal(await field("reply.private_prompt").inputValue(), "私聊草稿");
  assert.equal(await field("reply.proactive_prompt").inputValue(), "主动聊天草稿");
  await save("保存回复与插话设置"); patch = await posted();
  assert.deepEqual(patch.reply, { group_prompt: "回复草稿", private_prompt: "私聊草稿", proactive_prompt: "主动聊天草稿" });
  assert.deepEqual(Object.keys(patch.social), ["interjection_interval_minutes"]);
  assert.equal(await page.evaluate(() => window.fixture.settings.social.cooldown_minutes), 75);
  for (const [key, resetLabel] of [["group_prompt", "恢复群聊默认文案"], ["private_prompt", "恢复私聊默认文案"], ["proactive_prompt", "恢复主动聊天默认文案"]]) {
    const before = await posted();
    await click(resetLabel);
    assert.equal(await field(`reply.${key}`).inputValue(), contract.group_reply_default);
    assert.deepEqual(await posted(), before, "Restore only edits the draft");
    const others = ["group_prompt", "private_prompt", "proactive_prompt"].filter(item => item !== key);
    const otherValues = await Promise.all(others.map(item => field(`reply.${item}`).inputValue()));
    await field(`reply.${key}`).fill(""); await save("保存回复与插话设置");
    assert.equal((await posted()).reply[key], "", "An empty requirement stays empty");
    assert.deepEqual(await Promise.all(others.map(item => field(`reply.${item}`).inputValue())), otherValues);
  }
  await go("character", "profile"); assert.match(await field("character.profile").inputValue(), /^角色资料草稿/);
  await save("保存角色与世界"); assert.deepEqual(Object.keys(await posted()).sort(), ["character", "persona_id"]);
  assert.equal(await page.evaluate(() => window.profileXss), undefined);
  await go("chat", "targets"); assert.equal(await field("number").inputValue(), "42");
  await field("display_name").fill("小林");
  await field("weight").fill("1.5"); await field("targets.selection").selectOption("qq:GroupMessage:42_100");
  await field("display_name").fill("读书群");
  await field("weight").fill("2.5"); await click("新增"); await field("type").selectOption("GroupMessage"); await field("number").fill("998877"); await field("weight").fill("0.5");
  await go("sources", "settings"); await go("chat", "targets"); assert.equal(await field("number").inputValue(), "998877");
  await refresh(); assert.equal(await field("number").inputValue(), "998877");
  await save("保存聊天白名单"); const sessions = (await posted()).sessions;
  assert.equal(await field("number").inputValue(), "998877", "Saving keeps the edited object selected");
  assert.deepEqual(sessions.map((row) => row.umo), ["qq:FriendMessage:42", "qq:GroupMessage:42_100", "qq:GroupMessage:998877"]);
  assert.deepEqual(sessions.map((row) => row.weight), [1.5, 2.5, 0.5]); assert.equal(sessions[0].retained, "keep");
  assert.deepEqual(sessions.map((row) => row.display_name), ["小林", "读书群", ""]);
  assert.equal(await field("social.target_count").count(), 0);
  await field("targets.selection").selectOption("qq:FriendMessage:42"); await click("检查会话与历史");
  await page.getByText("会话检查完成；没有调用模型或发送消息", { exact: true }).waitFor();
  await go("chat", "limits");
  await page.getByText("每轮抽选 1 个目标", { exact: false }).waitFor();
  assert.equal(await field("social.target_count").count(), 0);
  await go("sources", "settings"); await field("source.settings").selectOption("news");
  await field("name").fill("第一个来源草稿"); await field("source-array.news.selection").selectOption("rss-1"); await field("name").fill("第二个来源草稿");
  await field("source.settings").selectOption("weather"); await field("weather.location").fill("天津");
  await save("保存来源设置"); assert.deepEqual(Object.keys(await posted()), ["weather"]);
  await field("source.settings").selectOption("news"); assert.equal(await field("name").inputValue(), "第二个来源草稿");
  await refresh(); await save("保存新闻源设置");
  assert.deepEqual((await posted()).news.sources.slice(0, 2).map((r) => r.name), ["第一个来源草稿", "第二个来源草稿"]);
  assert.equal(await page.evaluate(() => window.fixture.settings.weather.location), "天津");
  await field("source.settings").selectOption("daily_digest"); await field("name").fill("第一个日报草稿");
  await field("source-array.daily_digest.selection").selectOption("juya"); await field("keywords").fill("AI 新闻");
  await save("保存日报设置"); assert.equal((await posted()).daily_digest.sources[0].name, "第一个日报草稿");
  assert.equal((await posted()).daily_digest.sources[1].keywords, "AI 新闻");
  await click("新增"); await field("name").fill("新日报"); await field("uid").fill("12345"); await click("删除所选");
  assert.equal(await field("source-array.daily_digest.selection").locator("option").count(), 2);
  await save("保存日报设置");
  await go("sources", "manual"); await field("source.manual").selectOption("search"); await field("query").fill("原样保留的搜索草稿");
  await field("source.manual").selectOption("bilibili"); await field("bilibili.operation").selectOption("bilibili_watch"); await field("query").fill("BV123456");
  await field("source.manual").selectOption("search"); assert.equal(await field("query").inputValue(), "原样保留的搜索草稿");
  await click("搜索并保存见闻"); assert.equal(await page.evaluate(() => window.calls.findLast((c) => c.body?.action === "explore").body.query), "原样保留的搜索草稿");
  await go("context", "templates", { task: "memory.reflect" }); await field("template").fill("未保存的记忆模板");
  assert.match(await field("template.task").locator('option:checked').textContent(), /memory.reflect（提炼与整理记忆）/);
  await field("template.task").selectOption("life.detail"); await field("template").fill("细化模板草稿");
  await field("template.version").selectOption("0"); assert.match(await page.locator(".template-preview").textContent(), /旧版指令/);
  assert.equal(await page.locator("#section-panel textarea").count(), 0);
  await field("template.version").selectOption("current"); assert.equal(await field("template").inputValue(), "细化模板草稿");
  await click("保存此任务模板"); await page.getByText("操作已完成，请查看执行结果", { exact: true }).waitFor();
  await go("context", "trial"); await field("task").selectOption("memory.reflect"); await field("request_json").fill('{"prompt":"PRIVATE_DRAFT"}');
  await field("task").selectOption("life.detail"); await field("request_json").fill('{"prompt":"DETAIL_DRAFT"}');
  await go("context", "calls"); await field("debug.retain_per_category").fill("23");
  await go("context", "templates", { task: "memory.reflect" }); assert.equal(await field("template").inputValue(), "未保存的记忆模板");
  await refresh(); assert.equal(await field("template").inputValue(), "未保存的记忆模板");
  await go("context", "trial"); await field("task").selectOption("memory.reflect"); assert.equal(await field("request_json").inputValue(), '{"prompt":"PRIVATE_DRAFT"}');
  await go("context", "calls"); assert.equal(await field("debug.retain_per_category").inputValue(), "23"); await save("保存记录数量");
  await go("context", "layout"); await field("layout_task").selectOption("chat.group");
  const row = (id) => page.locator(`.layout-row[data-block-id="${id}"]`);
  const ids = (role) => page.locator(`.layout-lane[data-role="${role}"] .layout-row`).evaluateAll((nodes) => nodes.map((n) => n.dataset.blockId));
  assert.equal(await row("time").evaluate((n) => n.getBoundingClientRect().height), 32);
  assert.equal(await row("anchor.user").evaluate((n) => n.getBoundingClientRect().height), 32);
  assert.equal(await page.locator('.layout-list').first().evaluate((n) => getComputedStyle(n).rowGap), "4px");
  await shot("context-desktop");
  // Synthetic event paths exercise bubbling, invalid drops and exact end targets.
  const drag = async (from, to, edge = "before", cancel = false) => page.evaluate(({from, to, edge, cancel}) => {
    const source = document.querySelector(`[data-block-id="${from}"] .layout-handle`);
    const target = to.startsWith("end:") ? document.querySelector(`[data-end-role="${to.slice(4)}"]`) : to === "invalid" ? document.querySelector(".layout-lane") : document.querySelector(`[data-block-id="${to}"]`);
    const transfer = new DataTransfer();
    source.dispatchEvent(new DragEvent("dragstart", { dataTransfer: transfer, bubbles: true, cancelable: true }));
    const rect = target.getBoundingClientRect();
    target.dispatchEvent(new DragEvent("dragover", { dataTransfer: transfer, bubbles: true, cancelable: true, clientY: edge === "before" ? rect.top + 1 : rect.bottom - 1 }));
    const animations = document.getAnimations().length;
    if (!cancel) target.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true, cancelable: true }));
    source.dispatchEvent(new DragEvent("dragend", { dataTransfer: transfer, bubbles: true })); return animations;
  }, {from, to, edge, cancel});
  process.stdout.write("Synthetic dragging…\n");
  const baseUser = await ids("user");
  await drag("time", "time"); assert.deepEqual(await ids("user"), baseUser);
  await drag("time", "invalid"); assert.deepEqual(await ids("user"), baseUser);
  await drag("time", "memory.recent", "after", true); assert.deepEqual(await ids("user"), baseUser);
  assert.ok(await drag("memory.recent", "memory") > 0, "Other rows animate into place");
  assert.equal((await ids("user")).indexOf("memory.recent") + 1, (await ids("user")).indexOf("memory"));
  await drag("weather", "anchor.system"); assert.equal((await ids("system"))[0], "weather");
  await drag("weather", "end:user"); assert.equal((await ids("user")).at(-1), "weather");
  await row("weather").locator("select").selectOption("system");
  await row("group_reply").locator("select").selectOption("system");
  await row("group_reply").getByRole("button", { name: "上移回复与插话：本轮群聊回复要求", exact: true }).focus(); await page.keyboard.press("Enter");
  assert.ok((await ids("system")).indexOf("group_reply") < (await ids("system")).indexOf("weather"));
  const enabled = id => row(id).locator(".layout-enabled");
  const selectedIds = () => page.locator(".layout-row").evaluateAll(nodes => nodes.filter(node => node.querySelector(".layout-enabled:checked")).map(node => node.dataset.blockId).sort());
  assert.equal(await enabled("task.material").isChecked(), false);
  await row("task.material").locator("select").selectOption("system");
  assert.ok((await ids("system")).includes("task.material"), "Unchecked rows remain movable");
  await row("task.material").locator("select").selectOption("user");
  assert.equal(await enabled("task.material").isChecked(), false, "Moving a row does not select it");
  const globalOrder = { system: await ids("system"), user: await ids("user") };
  for (const task of contract.context_layout_catalog.tasks) {
    await field("layout_task").selectOption(task.id);
    assert.equal(await page.locator(".layout-row").count(), contract.context_layout_catalog.blocks.length, "Every task shows the full catalog");
    assert.equal(await row("time").locator("select").isDisabled(), false);
    assert.deepEqual({ system: await ids("system"), user: await ids("user") }, globalOrder, "Task selection never changes global order");
    assert.deepEqual(await selectedIds(), [...contract.context_layout_catalog.default_selections[task.id]].sort());
  }
  await field("layout_task").selectOption("chat.group");
  assert.equal(await enabled("schedule.recent").isChecked(), true);
  assert.equal(await enabled("schedule").isChecked(), false);
  await enabled("schedule").check(); assert.equal(await enabled("schedule.recent").isChecked(), false);
  await enabled("schedule").uncheck(); assert.equal(await enabled("schedule.recent").isChecked(), false);
  await enabled("schedule.recent").check();
  await enabled("private_reply").check(); await enabled("group_reply").uncheck();
  await enabled("task.thoughts").check();
  const groupSelection = await selectedIds();
  await field("layout_task").selectOption("chat.private");
  assert.equal(await enabled("private_reply").isChecked(), true);
  assert.equal(await enabled("task.thoughts").isChecked(), false);
  await row("weather").locator("select").selectOption("user");
  await field("layout_task").selectOption("chat.group"); assert.ok((await ids("user")).includes("weather"));
  assert.deepEqual(await selectedIds(), groupSelection, "Task selections persist independently through task switches");
  await page.evaluate(() => { window.failNext = true; }); await click("保存上下文设置");
  await page.getByText("测试来源暂时不可用", { exact: true }).waitFor(); await refresh();
  assert.deepEqual(await selectedIds(), groupSelection); assert.ok((await ids("user")).includes("weather"));
  await save("保存上下文设置"); const saved = (await posted()).context_layout;
  assert.equal(saved.version, 4); assert.equal(Object.hasOwn(saved, "default"), false);
  assert.ok(saved.order.user.includes("weather")); assert.deepEqual([...saved.tasks["chat.group"]].sort(), groupSelection);
  assert.ok(saved.tasks["chat.private"].includes("private_reply")); assert.ok(!saved.tasks["chat.private"].includes("task.thoughts"));
  assert.ok(Object.values(saved.tasks).every(selection => Array.isArray(selection) && selection.every(id => !id.startsWith("anchor."))));
  await refresh(); assert.deepEqual(await selectedIds(), groupSelection);
  await click("恢复本任务默认勾选");
  assert.deepEqual(await selectedIds(), [...contract.context_layout_catalog.default_selections["chat.group"]].sort());
  assert.deepEqual((await posted()).context_layout, saved, "Restoring task defaults only forms a draft");
  assert.ok((await ids("user")).includes("weather"));
  await save("保存上下文设置");
  await click("恢复默认排序"); assert.deepEqual(await ids("user"), saved.baseline_order.user);
  assert.deepEqual((await posted()).context_layout.order, saved.order, "Restoring order only forms a draft");
  await save("保存上下文设置");
  assert.deepEqual(await ids("user"), baseUser);
  process.stdout.write("Mouse dragging…\n");
  // Real browser dragging catches native event and DOM-move interactions.
  await row("time").evaluate(node => window.scrollTo(0, window.scrollY + node.getBoundingClientRect().top - 140));
  await row("memory.recent").locator(".layout-handle").dragTo(row("time"), { targetPosition: { x: 80, y: 2 } });
  assert.ok((await ids("user")).indexOf("memory.recent") < (await ids("user")).indexOf("time"));
  await row("time").locator(".layout-handle").dragTo(row("time"), { targetPosition: { x: 80, y: 16 } });
  const nativeOrder = await ids("user"); assert.notEqual(nativeOrder.at(-1), "time");
  await save("保存上下文设置");
  await click("恢复默认排序"); await save("保存上下文设置");
  process.stdout.write("Source targets…\n");
  // Every catalog entry resolves through stable IDs and every management link is reachable.
  for (const block of contract.context_layout_catalog.blocks) {
    await go("context", "layout"); await field("layout_task").selectOption("chat.group");
    await row(block.id).getByRole("button", { name: /来源$/ }).click();
    assert.equal(await page.locator("#source-index-title").textContent(), block.label);
    assert.ok((await page.locator("#source-index-body").textContent()).includes(block.purpose));
    await click("关闭来源说明");
    for (const target of block.targets) {
      await row(block.id).getByRole("button", { name: /来源$/ }).click();
      await page.locator("#source-index").getByRole("button", { name: target.path || target.label, exact: true }).click();
      await page.locator(`#section-panel[data-page="${target.page}"][data-section="${target.tab}"]`).waitFor();
      if (target.params.field) {
        await page.locator(".index-target").waitFor();
        assert.equal(await field(target.params.field).evaluate((n) => document.activeElement === n), true);
      }
      if (target.params.attribute) assert.equal(await field("memory.attribute").inputValue(), target.params.attribute);
      if (target.params.kind) assert.equal(await field("journals.kind").inputValue(), target.params.kind);
      if (target.params.source) assert.equal(await field("source." + target.tab).inputValue(), target.params.source);
      await click("返回上下文列表"); await row(block.id).waitFor();
      assert.equal(await field("layout_task").inputValue(), "chat.group");
    }
  }
  await enabled("private_reply").check(); const sourceSelection = await selectedIds();
  const sourceOrder = { system: await ids("system"), user: await ids("user") };
  await row("group_reply").scrollIntoViewIfNeeded(); const originalScroll = await page.evaluate(() => scrollY);
  await row("group_reply").getByRole("button", { name: /来源$/ }).click();
  await click(contract.context_layout_catalog.blocks.find(b => b.id === "group_reply").targets[0].path); await field("reply.group_prompt").fill("索引跳转后草稿"); await click("返回上下文列表");
  await row("group_reply").waitFor(); await page.waitForTimeout(180);
  assert.deepEqual(await selectedIds(), sourceSelection, "Source navigation preserves the selection draft");
  assert.deepEqual({ system: await ids("system"), user: await ids("user") }, sourceOrder);
  assert.ok(Math.abs(await page.evaluate(() => scrollY) - originalScroll) < 3, "Index return restores scroll");
  await row("group_reply").getByRole("button", { name: /来源$/ }).click(); await click(contract.context_layout_catalog.blocks.find(b => b.id === "group_reply").targets[0].path);
  assert.equal(await field("reply.group_prompt").inputValue(), "索引跳转后草稿"); await click("返回上下文列表");
  await row("memory").getByRole("button", { name: /来源$/ }).click();
  await shot("source-desktop"); await field("source_template").selectOption("memory.reflect"); await click("打开所选模板");
  assert.equal(await field("template.task").inputValue(), "memory.reflect");
  assert.equal(await field("template").inputValue(), "未保存的记忆模板");
  await click("返回上下文列表"); await row("memory").waitFor();
  await page.emulateMedia({ reducedMotion: "reduce" }); assert.equal(await drag("memory.recent", "memory"), 0); await page.emulateMedia({ reducedMotion: "no-preference" });
  await go("system", "backup");
  await page.getByLabel("选择 Living World 备份文件").setInputFiles({ name: "fixture.json", mimeType: "application/json", buffer: Buffer.from('{"version":1,"settings":{}}') });
  await page.getByText("已读取 fixture.json", { exact: false }).waitFor(); await go("system", "models"); await go("system", "backup");
  await page.getByText("已读取 fixture.json", { exact: false }).waitFor();
  await click("恢复所选备份"); await page.locator("#editor[open]").waitFor(); await page.locator("#editor-cancel").click();
  assert.equal(await page.evaluate(() => window.calls.some((c) => c.endpoint === "import")), false);
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  assert.equal(await page.evaluate(() => { const event = new Event("beforeunload", { cancelable: true }); dispatchEvent(event); return event.defaultPrevented; }), true);
  process.stdout.write("\nArchive controls…\n");
  await require("./ui_admin_records.cjs")(page, go, field, click, save, shot);
};
