"""Memory service tests with a storage and language-model double."""

from __future__ import annotations

import asyncio
import copy
import json
import unittest
from datetime import UTC, datetime, timedelta

from living_world.memory import MemoryService


class Store:
    def __init__(self):
        self.data = {}

    def get(self, namespace, key, default=None):
        return copy.deepcopy(self.data.get(namespace, {}).get(key, default))

    def put(self, namespace, key, value):
        self.data.setdefault(namespace, {})[key] = copy.deepcopy(value)

    def list(self, namespace):
        return copy.deepcopy(list(self.data.get(namespace, {}).values()))

    def delete(self, namespace, key):
        self.data.get(namespace, {}).pop(key, None)


class Runtime:
    def __init__(self):
        self.store = Store()
        self.settings = {}
        self.on = True
        self.response = '{"memories": []}'
        self.calls = []
        self.disable_during_generation = False

    def enabled(self, module):
        return module == "memory" and self.on

    async def generate(self, module, prompt, scope="global"):
        self.calls.append((module, prompt, scope))
        if self.disable_during_generation:
            self.on = False
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.memory = MemoryService(self.runtime)

    def test_private_memory_is_not_visible_to_other_scopes(self):
        private = self.memory.remember("明天一起学习数学", scope="qq:private:1", person_id="qq:1")
        public = self.memory.remember("数学课忘带了笔", scope="global")
        self.assertEqual(
            [row["id"] for row in self.memory.recall("数学", scope="qq:group:2", person_id="qq:1")],
            [public["id"]],
        )
        self.assertEqual(
            {
                row["id"]
                for row in self.memory.recall("数学", scope="qq:private:1", person_id="qq:1")
            },
            {private["id"], public["id"]},
        )
        self.assertNotIn(private["id"], [row["id"] for row in self.memory.recall(scope="global")])

    def test_profile_from_scoped_direct_remember_stays_scoped(self):
        record = self.memory.remember(
            "喜欢摄影", scope="qq:private:1", person_id="qq:1", profile=True
        )
        self.assertEqual(record["scope"], "qq:private:1")
        self.assertEqual(self.memory.recall(scope="qq:group:2", person_id="qq:1"), [])
        self.assertEqual(self.memory.recall(scope="qq:private:1", person_id="qq:2"), [])
        with self.assertRaises(ValueError):
            self.memory.remember("未经证明的个人画像", person_id="qq:1", profile=True)

    def test_person_filter_does_not_pick_up_another_person(self):
        self.memory.remember("甲喜欢数学", scope="qq:group:1", person_id="qq:1")
        wanted = self.memory.remember("乙喜欢数学", scope="qq:group:1", person_id="qq:2")
        self.assertEqual(
            [row["id"] for row in self.memory.recall("数学", scope="qq:group:1", person_id="qq:2")],
            [wanted["id"]],
        )

    def test_unowned_global_memories_are_available_to_any_person(self):
        wanted = self.memory.remember("今日天气晴朗", kind="knowledge", source="weather")
        self.assertEqual(
            self.memory.recall("天气", scope="qq:group:1", person_id="qq:1")[0]["id"], wanted["id"]
        )

    def test_chinese_search_and_unrelated_query(self):
        wanted = self.memory.remember("下午数学课上学习了几何")
        self.memory.remember("晚上阅读关于海洋生物的书")
        self.assertEqual(self.memory.recall("数学")[0]["id"], wanted["id"])
        self.assertEqual(self.memory.recall("火箭发动机"), [])

    def test_recall_reinforces_the_returned_memory_only(self):
        target = self.memory.remember("learned algebra")
        untouched = self.memory.remember("listened to music")
        recalled = self.memory.recall("algebra")[0]
        self.assertGreater(recalled["strength"], target["strength"])
        self.assertEqual(recalled["access_count"], 1)
        self.assertEqual(self.runtime.store.get("memories", untouched["id"])["access_count"], 0)

    def test_duplicate_text_reuses_a_record_but_respects_scope(self):
        first = self.memory.remember("Enjoy  Music", scope="qq:group:1")
        again = self.memory.remember("enjoy music", scope="qq:group:1", important=True)
        other = self.memory.remember("enjoy music", scope="qq:group:2")
        self.assertEqual(first["id"], again["id"])
        self.assertNotEqual(first["id"], other["id"])
        self.assertTrue(again["important"])
        self.assertEqual(len(self.runtime.store.list("memories")), 2)

    def test_key_upsert_replaces_old_content_without_cross_scope_writes(self):
        old = self.memory.remember("tea", key="drink", scope="qq:group:1")
        new = self.memory.remember("coffee", key="drink", scope="qq:group:1")
        self.assertEqual(old["id"], new["id"])
        self.assertEqual(self.memory.recall("tea", scope="qq:group:1"), [])
        with self.assertRaises(ValueError):
            self.memory.remember("secret", key="drink", scope="qq:private:1")

    def test_update_replaces_old_content_and_prevents_scope_widening(self):
        row = self.memory.remember("tea", scope="qq:private:1")
        updated = self.memory.update(row["id"], {"text": "coffee", "important": True})
        self.assertTrue(updated["important"])
        self.assertEqual(self.memory.recall("tea", scope="qq:private:1"), [])
        self.assertEqual(self.memory.recall("coffee", scope="qq:private:1")[0]["id"], row["id"])
        with self.assertRaises(ValueError):
            self.memory.update(row["id"], {"scope": "global"})

    def test_merge_keeps_restrictive_scope_and_important_flag(self):
        public = self.memory.remember("math class", important=True, source="life")
        private = self.memory.remember(
            "personal promise", scope="qq:private:1", person_id="qq:1", source="chat"
        )
        merged = self.memory.merge([public["id"], private["id"]], "math class promise")
        self.assertEqual(merged["scope"], "qq:private:1")
        self.assertEqual(merged["person_id"], "qq:1")
        self.assertTrue(merged["important"])
        self.assertEqual(set(merged["sources"]), {"life", "chat"})
        self.assertEqual(len(self.runtime.store.list("memories")), 1)
        self.assertEqual(self.memory.recall(scope="qq:group:1", person_id="qq:1"), [])

    def test_merge_rejects_unrelated_scopes_and_people(self):
        first = self.memory.remember("first", scope="qq:private:1", person_id="qq:1")
        second = self.memory.remember("second", scope="qq:group:1", person_id="qq:1")
        with self.assertRaises(ValueError):
            self.memory.merge([first["id"], second["id"]], "combined")
        third = self.memory.remember("third", scope="qq:group:1", person_id="qq:2")
        with self.assertRaises(ValueError):
            self.memory.merge([second["id"], third["id"]], "combined")
        self.assertEqual(len(self.runtime.store.list("memories")), 3)

    def test_forgotten_memory_is_archived_important_memory_is_retained(self):
        now = datetime.now(UTC)
        ordinary = self.memory.remember("forgettable detail")
        important = self.memory.remember("important anniversary", important=True)
        for row in (ordinary, important):
            for field in ("created_at", "updated_at", "last_accessed_at", "last_decay_at"):
                row[field] = (now - timedelta(days=365)).isoformat()
            self.runtime.store.put("memories", row["id"], row)
        result = self.memory.maintain(now)
        self.assertEqual(result, {"decayed": 1, "forgotten": 1, "retained": 1})
        self.assertFalse(self.runtime.store.get("memories", ordinary["id"])["active"])
        self.assertEqual(self.memory.recall("forgettable"), [])
        self.assertEqual(self.memory.recall("anniversary")[0]["id"], important["id"])
        self.assertEqual(len(self.runtime.store.list("memories")), 2)

    def test_decay_is_idempotent_for_the_same_time_and_can_be_restored(self):
        row = self.memory.remember("detail")
        now = datetime.now(UTC) + timedelta(days=30)
        self.memory.maintain(now)
        first = self.runtime.store.get("memories", row["id"])["strength"]
        self.memory.maintain(now.isoformat())
        self.assertAlmostEqual(self.runtime.store.get("memories", row["id"])["strength"], first)
        self.memory.update(row["id"], {"active": False})
        self.assertEqual(self.memory.recall(), [])
        self.memory.update(row["id"], {"active": True})
        self.assertEqual(self.memory.recall()[0]["id"], row["id"])

    def test_disabled_business_calls_preserve_data_and_admin_still_works(self):
        first = self.memory.remember("first")
        second = self.memory.remember("second")
        before = copy.deepcopy(self.runtime.store.data)
        self.runtime.on = False
        self.assertEqual(self.memory.remember("third"), {})
        self.assertEqual(self.memory.recall(), [])
        self.assertEqual(self.memory.merge([first["id"], second["id"]], "combined"), {})
        self.assertEqual(
            self.memory.maintain(datetime.now(UTC) + timedelta(days=999)),
            {"decayed": 0, "forgotten": 0, "retained": 0},
        )
        self.assertEqual(self.runtime.store.data, before)
        self.assertEqual(self.memory.update(first["id"], {"text": "edited"})["text"], "edited")
        self.memory.delete(second["id"])
        self.assertIsNone(self.runtime.store.get("memories", second["id"]))

    def test_returned_records_do_not_expose_live_store_references(self):
        row = self.memory.remember("unchanged")
        row["text"] = "changed outside service"
        self.assertEqual(self.memory.recall()[0]["text"], "unchanged")

    def test_disabled_sources_are_hidden_from_direct_recall_without_reinforcement(self):
        disabled = {"news", "weather", "bilibili", "life", "journal", "notes"}
        self.runtime._source_enabled = lambda source: (
            ("life" if source == "fiction" else source.split(":", 1)[0]) not in disabled
        )
        hidden = [
            self.memory.remember(f"来源内容 {source}", source=source)
            for source in ("news", "weather:actual", "bilibili", "fiction", "journal", "notes")
        ]
        kept = self.memory.remember("聊天约定", source="chat")
        self.assertEqual([row["id"] for row in self.memory.recall()], [kept["id"]])
        self.assertTrue(
            all(
                self.runtime.store.get("memories", row["id"])["access_count"] == 0 for row in hidden
            )
        )
        disabled.clear()
        # Legacy journals remain archived until a brief is generated.
        self.assertEqual(len(self.memory.recall()), 5)

    def test_merged_and_derived_memories_keep_source_disabled_boundaries(self):
        self.runtime._source_enabled = lambda source: source != "news"
        news = self.memory.remember("新闻中的新发现", source="news")
        chat = self.memory.remember("交流中的感受", source="chat")
        merged = self.memory.merge([news["id"], chat["id"]], "关于新闻的交流感受")
        derived = self.memory.remember(
            "新闻与生活日记",
            source="journal",
            sources=[{"source": "news", "scope": "global"}, {"source": "fiction"}],
        )
        self.assertEqual(self.memory.recall(), [])
        self.assertIsNotNone(self.runtime.store.get("memories", merged["id"]))
        self.assertIsNotNone(self.runtime.store.get("memories", derived["id"]))
        twice_merged = self.memory.merge([merged["id"], derived["id"]], "合并后的记录仍含新闻")
        self.assertIn("news", twice_merged["sources"])
        self.assertEqual(self.memory.recall(), [])


class ReflectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.memory = MemoryService(self.runtime)

    def response(self, *records):
        self.runtime.response = json.dumps({"memories": records}, ensure_ascii=False)

    async def test_evidence_backed_name_follows_same_person_only(self):
        self.response(
            {
                "text": "称呼小明",
                "kind": "knowledge",
                "profile_attribute": "name",
                "value": "小明",
                "evidence": "我叫小明",
            }
        )
        result = await self.memory.reflect(
            "我叫小明。", scope="qq:private:1", person_id="qq:1", source="chat"
        )
        self.assertEqual(result[0]["scope"], "global")
        self.assertEqual(result[0]["origin_scope"], "qq:private:1")
        self.assertEqual(result[0]["source"], "chat")
        self.assertTrue(result[0]["profile"])
        self.assertEqual(
            self.memory.recall(scope="qq:group:2", person_id="qq:1")[0]["text"], "称呼：小明"
        )
        self.assertEqual(self.memory.recall(scope="qq:group:2", person_id="qq:2"), [])
        self.assertEqual(self.memory.recall(scope="global"), [])

    async def test_name_correction_replaces_previous_profile(self):
        for name in ("小明", "小花"):
            statement = f"我叫{name}"
            self.response({"profile_attribute": "name", "value": name, "evidence": statement})
            await self.memory.reflect(statement, scope="qq:private:1", person_id="qq:1")
        profiles = self.memory.recall(scope="qq:group:1", person_id="qq:1")
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["text"], "称呼：小花")

    async def test_global_profile_evidence_does_not_include_surrounding_private_prose(self):
        statement = "我叫小明，领导批评让我哭了一晚"
        self.response(
            {
                "text": "称呼小明",
                "profile_attribute": "name",
                "value": "小明",
                "evidence": statement,
            }
        )
        result = await self.memory.reflect(statement, scope="qq:private:1", person_id="qq:1")
        self.assertEqual(result[0]["profile_evidence"], "我叫小明")
        recalled = self.memory.recall(scope="qq:group:1", person_id="qq:1")
        self.assertNotIn("领导", json.dumps(recalled, ensure_ascii=False))

    async def test_interest_and_relationship_use_short_verified_values(self):
        self.response(
            {
                "text": "喜欢摄影",
                "profile_attribute": "interest",
                "value": "摄影",
                "evidence": "我平时喜欢摄影",
            },
            {
                "text": "我们是朋友",
                "profile_attribute": "relationship",
                "value": "朋友",
                "evidence": "我们是朋友",
            },
        )
        result = await self.memory.reflect(
            "我平时喜欢摄影，我们是朋友。", scope="qq:private:1", person_id="qq:1"
        )
        self.assertEqual({row["text"] for row in result}, {"稳定兴趣：摄影", "关系：朋友"})
        self.assertTrue(all(row["scope"] == "global" for row in result))

    async def test_scoped_emotions_and_promises_cannot_be_promoted_by_model(self):
        statement = "我明天要去医院，希望你陪我"
        self.response(
            {
                "text": "明天陪他去医院的约定",
                "kind": "emotional",
                "evidence": statement,
                "scope": "global",
                "person_id": "someone-else",
                "profile": True,
                "profile_attribute": "interest",
                "value": "医院",
            }
        )
        result = await self.memory.reflect(statement, scope="qq:private:1", person_id="qq:1")
        self.assertEqual(result[0]["scope"], "qq:private:1")
        self.assertEqual(result[0]["person_id"], "qq:1")
        self.assertFalse(result[0]["profile"])
        self.assertEqual(self.memory.recall(scope="qq:group:1", person_id="qq:1"), [])

    async def test_request_to_keep_a_name_private_prevents_promotion(self):
        self.response(
            {
                "text": "称呼小明",
                "profile_attribute": "name",
                "value": "小明",
                "evidence": "我叫小明",
            }
        )
        result = await self.memory.reflect(
            "我叫小明，但别告诉别人。", scope="qq:private:1", person_id="qq:1"
        )
        self.assertEqual(result[0]["scope"], "qq:private:1")
        self.assertEqual(self.memory.recall(scope="qq:group:1", person_id="qq:1"), [])

    async def test_unverified_or_missing_evidence_is_not_saved(self):
        self.response({"text": "made up", "evidence": "not in input"}, {"text": "no evidence"})
        self.assertEqual(await self.memory.reflect("actual input", scope="qq:private:1"), [])

    async def test_profile_value_must_occur_in_explicit_self_statement(self):
        self.response(
            {
                "text": "读过摄影入门",
                "profile_attribute": "interest",
                "value": "摄影",
                "evidence": "读过摄影入门",
            }
        )
        result = await self.memory.reflect("读过摄影入门", scope="qq:private:1", person_id="qq:1")
        self.assertEqual(result[0]["scope"], "qq:private:1")
        self.assertFalse(result[0]["profile"])

    async def test_malformed_extraction_does_not_crash_or_invent_records(self):
        for response in ("not json", "{}", "null", "[]", '{"memories": "invalid"}'):
            self.runtime.response = response
            self.assertEqual(await self.memory.reflect("some text", scope="qq:private:1"), [])
        self.response({"profile_attribute": [], "text": "some text", "evidence": "some text"})
        result = await self.memory.reflect("some text", scope="qq:private:1", person_id="qq:1")
        self.assertEqual(result[0]["scope"], "qq:private:1")

    async def test_existing_scoped_memory_can_be_corrected(self):
        previous = self.memory.remember("meet on Monday", scope="qq:private:1", person_id="qq:1")
        self.response(
            {"text": "meet on Tuesday", "evidence": "Tuesday instead", "replace_id": previous["id"]}
        )
        result = await self.memory.reflect(
            "Tuesday instead", scope="qq:private:1", person_id="qq:1"
        )
        self.assertEqual(result[0]["id"], previous["id"])
        self.assertEqual(self.memory.recall("Monday", scope="qq:private:1", person_id="qq:1"), [])
        self.assertEqual(len(self.runtime.store.list("memories")), 1)

    async def test_replace_id_cannot_edit_a_different_scope(self):
        previous = self.memory.remember("private fact", scope="qq:private:1", person_id="qq:1")
        self.response(
            {"text": "new public fact", "evidence": "new public fact", "replace_id": previous["id"]}
        )
        result = await self.memory.reflect("new public fact", scope="qq:group:1", person_id="qq:1")
        self.assertNotEqual(result[0]["id"], previous["id"])
        self.assertEqual(self.runtime.store.get("memories", previous["id"])["text"], "private fact")

    async def test_disabled_module_never_calls_model_and_midflight_disable_discards_result(self):
        self.runtime.on = False
        self.assertEqual(await self.memory.reflect("some text", scope="qq:private:1"), [])
        self.assertEqual(self.runtime.calls, [])
        self.runtime.on = True
        self.runtime.disable_during_generation = True
        self.response({"text": "some text", "evidence": "some text"})
        self.assertEqual(await self.memory.reflect("some text", scope="qq:private:1"), [])
        self.assertEqual(self.runtime.store.list("memories"), [])

    async def test_async_waiting_reflection_cannot_write_after_disable(self):
        entered, released = asyncio.Event(), asyncio.Event()

        async def paused_generate(*args, **kwargs):
            entered.set()
            await released.wait()
            return json.dumps({"memories": [{"text": "private fact", "evidence": "private fact"}]})

        self.runtime.generate = paused_generate
        task = asyncio.create_task(self.memory.reflect("private fact", scope="qq:private:1"))
        await asyncio.wait_for(entered.wait(), 1)
        self.runtime.on = False
        released.set()
        self.assertEqual(await task, [])
        self.assertEqual(self.runtime.store.list("memories"), [])

    async def test_admin_correction_during_extraction_is_not_overwritten(self):
        row = self.memory.remember("meet on Monday", scope="qq:private:1", person_id="qq:1")

        async def concurrent_generate(*args, **kwargs):
            self.memory.update(row["id"], {"text": "meet on Friday"})
            return json.dumps(
                {
                    "memories": [
                        {
                            "text": "meet on Tuesday",
                            "evidence": "Tuesday instead",
                            "replace_id": row["id"],
                        }
                    ]
                }
            )

        self.runtime.generate = concurrent_generate
        self.assertEqual(
            await self.memory.reflect("Tuesday instead", scope="qq:private:1", person_id="qq:1"), []
        )
        self.assertEqual(self.runtime.store.get("memories", row["id"])["text"], "meet on Friday")

    async def test_admin_delete_during_extraction_is_not_resurrected(self):
        row = self.memory.remember("meet on Monday", scope="qq:private:1", person_id="qq:1")

        async def concurrent_generate(*args, **kwargs):
            self.memory.delete(row["id"])
            return json.dumps(
                {
                    "memories": [
                        {
                            "text": "meet on Tuesday",
                            "evidence": "Tuesday instead",
                            "replace_id": row["id"],
                        }
                    ]
                }
            )

        self.runtime.generate = concurrent_generate
        self.assertEqual(
            await self.memory.reflect("Tuesday instead", scope="qq:private:1", person_id="qq:1"), []
        )
        self.assertEqual(self.runtime.store.list("memories"), [])

    async def test_model_failure_is_nonfatal_and_repeated_reflection_deduplicates(self):
        self.runtime.response = RuntimeError("provider unavailable")
        with self.assertLogs("living_world.memory", level="WARNING"):
            self.assertEqual(await self.memory.reflect("some text", scope="qq:private:1"), [])
        self.response({"text": "some text", "evidence": "some text"})
        first = await self.memory.reflect("some text", scope="qq:private:1")
        second = await self.memory.reflect("some text", scope="qq:private:1")
        self.assertEqual(first[0]["id"], second[0]["id"])
        self.assertEqual(len(self.runtime.store.list("memories")), 1)


if __name__ == "__main__":
    unittest.main()
