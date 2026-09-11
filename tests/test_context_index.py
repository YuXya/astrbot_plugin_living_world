"""Administration descriptions cannot change model material or layout semantics."""

import json

from living_world.context_index import INDEX
from living_world.layout import BLOCK_NAMES, DEFAULT_LAYOUT, TASK_NAMES, assemble, block, catalog


def test_index_covers_every_block_and_only_existing_templates():
    assert set(INDEX) == set(BLOCK_NAMES)
    for item in catalog()["blocks"]:
        assert item["purpose"] and item["origin"]
        assert item["targets"] or item["host_help"]
        assert set(item["templates"]) <= set(TASK_NAMES)


def test_catalog_is_detached_and_model_assembly_does_not_contain_index():
    before = assemble(DEFAULT_LAYOUT, [block("memory", "记忆", "资料原文", "实际来源")])
    original = INDEX["memory"]["purpose"]
    index = catalog()
    news = next(item for item in index["blocks"] if item["id"] == "memory")
    news["purpose"] = "ADMIN_ONLY"
    news["targets"][0]["params"]["source"] = "changed"
    assert INDEX["memory"]["purpose"] == original
    assert "source" not in INDEX["memory"]["targets"][0]["params"]
    assert assemble(DEFAULT_LAYOUT, [block("memory", "记忆", "资料原文", "实际来源")]) == before
    encoded = json.dumps(before, ensure_ascii=False)
    assert original not in encoded
    assert not any(
        key in encoded for key in ['"targets"', '"purpose"', '"templates"', '"host_help"']
    )
