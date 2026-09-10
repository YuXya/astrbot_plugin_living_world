const assert = require("node:assert/strict");
module.exports = async (page) => {
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
      await page.locator('[name="debug_round"]').selectOption(`format-${index}`);
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
    await page.locator('[name="debug_round"]').selectOption("format-0");
    const changedBody = page.locator('.debug-round[data-turn="format-0"]');
    await changedBody.getByRole("button", { name: "格式化显示", exact: true }).click();
    assert.equal(await foldToggle(rootFolds(changedBody).first()).getAttribute("aria-expanded"), "false", "Raw mode preserves folding state");
    await page.evaluate(() => { window.fixture.debug_views[0].calls[0].response_body = '{"updated":{"value":2}}'; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    assert.equal(await changedBody.locator('.debug-json-toggle[aria-expanded="true"]').count(), 2, "Changed body resets folding for the same call");
    await page.evaluate(() => { window.fixture.debug_views = window.savedFormattingViews; delete window.savedFormattingViews; location.hash = "overview"; });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();

};
