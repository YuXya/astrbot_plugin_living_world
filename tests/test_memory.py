"""Unified memory contracts, using a transactional store and deterministic models."""

import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta

from living_world.memory import ATTRIBUTES, MemoryService
from living_world.memory_config import memory_settings
from living_world.store import Store


class Runtime:
    def __init__(self, store=None):
        self.store = store or Store(":memory:")
        self.settings = {"persona_id": "可可", "character": {"timezone": "Asia/Shanghai"}}
        self.on = True
        self.disabled = set()
        self.calls = []
        self.response = {"memories": []}
        self.hook = None

    def enabled(self, module):
        return self.on and module not in self.disabled

    def _source_enabled(self, source):
        return source.split(":")[0] not in self.disabled

    async def complete(self, task, module, prompt, data, scope):
        self.calls.append((task, data, scope))
        if self.hook:
            return await self.hook(task, data)
        if isinstance(self.response, Exception):
            raise self.response
        return json.dumps(self.response, ensure_ascii=False)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.memory = MemoryService(self.runtime)

    def tearDown(self):
        self.runtime.store.close()

    def run_async(self, awaitable):
        return asyncio.run(awaitable)

    def candidate(self, text, **extra):
        return {
            "judgment": text,
            "evidence": text,
            "attribute": "事实属性",
            "owner": "self",
            **extra,
        }

    def test_five_attributes_no_old_classification(self):
        self.assertEqual(len(ATTRIBUTES), 5)
        for attribute in ATTRIBUTES:
            row = self.memory.remember(attribute, attribute=attribute)
            self.assertNotIn("kind", row)
            self.assertEqual(row["schema_version"], 2)
        with self.assertRaises(ValueError):
            self.memory.remember("事件", attribute="事件")

    def test_self_rename_and_return_other_people_survive(self):
        own = self.memory.remember("学会水彩", stable=True)
        person = self.memory.remember("小明喜欢举例", person_id="qq:1", stable=True)
        self.runtime.settings["persona_id"] = "新名字"
        self.assertEqual(self.memory.recall(person_id="qq:1"), [person])
        second = self.memory.remember("刚认识新同学")
        self.assertNotEqual(own["identity"], second["identity"])
        self.runtime.settings["persona_id"] = "可可"
        self.assertEqual(
            {r["id"] for r in self.memory.recall(person_id="qq:1")}, {own["id"], person["id"]}
        )
        self.assertEqual(len(self.memory.profiles()), 3)

    def test_scope_and_module_isolation(self):
        self.memory.remember("私人约定", scope="private:1", person_id="qq:1")
        public = self.memory.remember("搜索海洋知识", source="search")
        self.assertEqual(self.memory.recall(scope="group:2", person_id="qq:1"), [public])
        self.runtime.disabled.add("search")
        self.assertEqual(self.memory.recall(scope="group:2", person_id="qq:1"), [])

    def test_recent_uses_actual_time_across_days_not_update_time(self):
        old = self.memory.remember("上周练字", occurred_at="2026-09-01T09:00:00+08:00")
        recent = self.memory.remember("昨天买书", occurred_at="2026-09-10T18:00:00+08:00")
        self.memory.remember("没有可靠时间", occurred_at="")
        self.memory.update(old["id"], {"text": "上周练字很认真"})
        view = self.run_async(self.memory.select_context("global", selection=["memory.recent"]))
        self.assertEqual([r["id"] for r in view["recent_memories"]], [recent["id"], old["id"]])
        self.assertEqual(self.runtime.calls, [])

    def test_recent_priority_and_unselected_does_not_consume_quota(self):
        recent = self.memory.remember(
            "喜欢水彩", stable=True, occurred_at="2026-09-10T18:00:00+08:00"
        )
        other = self.memory.remember(
            "喜欢摄影", stable=True, occurred_at="2026-09-09T18:00:00+08:00"
        )
        usage = {"limits": {"memory.recent": 1, "memory.self": 1, "memory.related": 0}}
        view = self.run_async(self.memory.select_context("global", usage=usage))
        self.assertEqual(view["recent_memories"], [recent])
        self.assertEqual(view["memories"], [other])
        alone = self.run_async(
            self.memory.select_context("global", usage=usage, selection=["memory"])
        )
        self.assertEqual(alone["memories"], [recent])

    def test_group_people_current_speaker_first_and_limit(self):
        for identifier in ("qq:1", "qq:2", "qq:3"):
            self.memory.remember(identifier + "偏好举例", person_id=identifier, stable=True)
        view = self.run_async(
            self.memory.select_context(
                "group:1",
                person_id="qq:3",
                people=[{"person_id": "qq:1"}, "qq:2"],
                usage={"people_limit": 2, "limits": {"memory.related": 0}},
                selection=["memory"],
            )
        )
        self.assertEqual([row["person_id"] for row in view["memories"]], ["qq:3", "qq:1"])

    def test_bm25_searches_tags_and_conclusions(self):
        wanted = self.memory.remember("团子是家里的猫", tags=["宠物"])
        self.memory.remember("学会蓝色湿画法", tags=["水彩"])
        self.assertEqual(self.memory.recall("宠物"), [wanted])
        self.assertEqual(self.memory.recall("家里的猫"), [wanted])
        self.assertEqual(self.memory.recall("量子计算"), [])

    def test_model_expansion_connects_synonyms_without_writes(self):
        wanted = self.memory.remember("养的猫叫团子", tags=["宠物"])
        self.runtime.response = {"keywords": ["宠物", "猫", "名字"]}
        before = self.runtime.store.export()
        view = self.run_async(
            self.memory.select_context("global", query="毛孩子叫什么", selection=["memory"])
        )
        self.assertEqual(view["memories"], [wanted])
        self.assertEqual(self.runtime.store.export(), before)
        self.assertEqual(self.runtime.calls[0][0], "memory.query")

    def test_expansion_failure_and_timeout_fall_back(self):
        wanted = self.memory.remember("我在学习 Python")
        self.runtime.response = RuntimeError("unavailable")
        view = self.run_async(
            self.memory.select_context("global", query="Python", selection=["memory"])
        )
        self.assertEqual(view["memories"], [wanted])

        async def slow(task, data):
            await asyncio.sleep(5)

        self.runtime.hook = slow
        self.runtime.settings["memory"] = {"query_timeout_seconds": 0.1}
        view = self.run_async(
            self.memory.select_context("global", query="Python", selection=["memory"])
        )
        self.assertEqual(view["memories"], [wanted])

    def test_internal_task_and_recent_only_do_not_expand(self):
        self.memory.remember("Python", occurred_at="2026-09-10T18:00:00+08:00")
        self.run_async(self.memory.select_context("global", query="Python", task="memory.reflect"))
        self.run_async(
            self.memory.select_context("global", query="Python", selection=["memory.recent"])
        )
        self.assertEqual(self.runtime.calls, [])

    def test_date_uses_role_timezone_excludes_undated_profiles_and_other_people(self):
        wanted = self.memory.remember("凌晨读书", occurred_at="2026-09-10T17:00:00+00:00")
        self.memory.remember("长期喜好", stable=True, occurred_at="")
        self.memory.remember("小明读书", person_id="qq:1", occurred_at="2026-09-10T17:00:00+00:00")
        view = self.run_async(
            self.memory.select_context(
                "global", date="2026-09-11", self_only=True, person_id="qq:1", semantic=False
            )
        )
        self.assertEqual(view["recent_memories"], [wanted])
        self.assertEqual(view["memories"], [])

    def test_chat_five_rounds_final_replies_only(self):
        for n in range(4):
            self.memory.enqueue_chat(
                "我在练琴", "陪你练习" + str(n), scope="private:1", round_id=str(n)
            )
        self.assertEqual(self.run_async(self.memory.process_pending())["processed"], 0)
        self.memory.enqueue_chat("我在练琴", "陪你练习4", scope="private:1", round_id="4")
        self.runtime.response = {"memories": [self.candidate("我在练琴")]}
        result = self.run_async(self.memory.process_pending())
        self.assertEqual(result["processed"], 5)
        self.assertEqual(len(self.runtime.store.list("memory_jobs")), 0)
        self.assertEqual(len(self.runtime.store.list("memories")), 1)
        self.assertNotIn("text", self.runtime.store.list("memory_materials")[0])

    def test_idle_flush_survives_restart_and_scopes_remain_separate(self):
        job = self.memory.enqueue_chat("练琴", "好的", scope="private:1", round_id="first")
        job["created_at"] = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
        self.runtime.store.put("memory_jobs", job["id"], job)
        self.memory.enqueue_chat("吃饭", "好的", scope="group:2", round_id="second")
        restarted = MemoryService(self.runtime)
        self.assertEqual(self.run_async(restarted.process_pending())["processed"], 1)
        self.assertEqual(self.runtime.store.list("memory_jobs")[0]["scope"], "group:2")

    def test_immediate_material_idempotence_and_failure_retry(self):
        first = self.memory.enqueue_material(
            "学会 Python", key="observation:1", occurred_at="2026-09-01T00:00:00Z"
        )
        again = self.memory.enqueue_material("不同呈现格式", key="observation:1")
        self.assertEqual(first["id"], again["id"])
        self.runtime.response = "invalid"
        self.assertEqual(self.run_async(self.memory.process_pending())["failed"], 1)
        row = self.runtime.store.list("memory_jobs")[0]
        self.assertEqual(row["text"], "学会 Python")
        self.assertEqual(row["attempts"], 1)
        row["retry_at"] = 0
        self.runtime.store.put("memory_jobs", row["id"], row)
        self.runtime.response = {"memories": [self.candidate("学会 Python")]}
        self.assertEqual(self.run_async(self.memory.process_pending())["processed"], 1)
        self.assertEqual(self.memory.recall()[0]["occurred_at"], "2026-09-01T00:00:00.000000+00:00")

    def test_transaction_rejects_mixed_invalid_update_without_replacing_old(self):
        old = self.memory.remember("喜欢茶", scope="private:1")
        self.memory.enqueue_material("喜欢咖啡", scope="private:1", key="e:1")
        self.runtime.response = {
            "memories": [
                self.candidate("喜欢咖啡", replace_id=old["id"]),
                self.candidate("不存在的证据"),
            ]
        }
        self.run_async(self.memory.process_pending())
        self.assertEqual(self.memory.recall(scope="private:1")[0]["text"], "喜欢茶")
        self.assertEqual(self.memory.versions(old["id"]), [])

    def test_unknown_identity_and_scope_escalation_are_rejected(self):
        self.memory.enqueue_material("甲喜欢摄影", scope="private:1", person_id="qq:1")
        self.runtime.response = {
            "memories": [self.candidate("甲喜欢摄影", owner="person", person_id="qq:2")]
        }
        self.run_async(self.memory.process_pending())
        self.assertEqual(self.memory.recall(person_id="qq:2"), [])
        row = self.memory.remember("私人内容", scope="private:1")
        with self.assertRaises(ValueError):
            self.memory.update(row["id"], {"scope": "global"})

    def test_useful_feedback_once_only_for_actual_adopted_memory(self):
        used = self.memory.remember("猫叫团子")
        unused = self.memory.remember("喜欢喝茶")
        feedback = self.memory.record_feedback(
            "r1",
            [{"memory_ids": [used["id"]], "memories": [used]}],
            "猫叫团子",
            scope="global",
            task="social.message",
        )
        self.runtime.response = {
            "feedback": [
                {"round_id": "r1", "memory_id": used["id"], "useful": True},
                {"round_id": "r1", "memory_id": unused["id"], "useful": True},
            ]
        }
        self.run_async(self.memory.process_pending())
        self.run_async(self.memory.process_pending())
        self.assertEqual(self.runtime.store.get("memories", used["id"])["useful_score"], 2.5)
        self.assertEqual(self.runtime.store.get("memories", unused["id"])["useful_score"], 0)
        self.assertIsNotNone(self.runtime.store.get("memory_feedback_done", feedback["id"]))

    def test_feedback_missing_failed_or_changed_version_never_reinforces(self):
        used = self.memory.remember("旧结论")
        self.memory.record_feedback(
            "r1", [{"memory_ids": [used["id"]], "memories": [used]}], "回答", task="life.detail"
        )
        self.memory.update(used["id"], {"text": "新结论"})
        self.runtime.response = {
            "feedback": [{"round_id": "r1", "memory_id": used["id"], "useful": True}]
        }
        self.run_async(self.memory.process_pending())
        self.assertEqual(self.runtime.store.get("memories", used["id"])["useful_score"], 0)
        self.memory.record_feedback(
            "r2", [{"memory_ids": [used["id"]]}], "回答", task="life.detail"
        )
        self.runtime.response = RuntimeError("offline")
        self.run_async(self.memory.process_pending())
        self.assertEqual(self.runtime.store.get("memories", used["id"])["strength"], 10)

    def test_recall_and_injection_do_not_reinforce(self):
        row = self.memory.remember("猫叫团子")
        self.memory.recall("团子", reinforce=True)
        self.memory.reinforce_sources([{"memory_ids": [row["id"]]}], "global")
        self.assertEqual(self.runtime.store.get("memories", row["id"]), row)

    def test_optional_decay_online_only_and_all_three_tiers(self):
        low = self.memory.remember("普通")
        medium = self.memory.remember("中档")
        medium["useful_score"] = 3
        self.runtime.store.put("memories", medium["id"], medium)
        high = self.memory.remember("长期")
        high["useful_score"] = 10
        self.runtime.store.put("memories", high["id"], high)
        protected = self.memory.remember("主动", protected=True)
        important = self.memory.remember("重要", important=True)
        self.memory.maintain(self.memory._clock + 259200)
        self.assertEqual(self.runtime.store.get("memories", low["id"])["strength"], 10)
        self.runtime.settings["memory"] = {"forgetting_enabled": True}
        self.memory.rebase_clock()
        self.memory.maintain(self.memory._clock + 259200)
        self.assertEqual(self.runtime.store.get("memories", low["id"])["strength"], 9)
        for row in (medium, high, protected, important):
            self.assertEqual(self.runtime.store.get("memories", row["id"])["strength"], 10)
        restarted = MemoryService(self.runtime)
        restarted.maintain(restarted._clock)
        self.assertEqual(self.runtime.store.get("memories", low["id"])["strength"], 9)

    def test_useless_feedback_only_decays_medium_when_enabled(self):
        self.runtime.settings["memory"] = {"forgetting_enabled": True}
        for tier in (0, 3, 10):
            row = self.memory.remember(str(tier))
            row["useful_score"] = tier
            self.runtime.store.put("memories", row["id"], row)
            feedback = self.memory.record_feedback(
                str(tier), [{"memory_ids": [row["id"]]}], "回答", task="social.message"
            )
            self.memory._apply_feedback(
                {"feedback": [{"round_id": str(tier), "memory_id": row["id"], "useful": False}]},
                [feedback],
            )
            expected = 9 if tier == 3 else 10
            self.assertEqual(self.runtime.store.get("memories", row["id"])["strength"], expected)

    def test_forgetting_deletes_versions_and_prevents_resurrection(self):
        self.runtime.settings["memory"] = {"forgetting_enabled": True}
        row = self.memory.remember("旧内容", key="fixed", source_keys=["event:1"])
        row = self.memory.update(row["id"], {"text": "新内容"})
        row["strength"] = 1
        self.runtime.store.put("memories", row["id"], row)
        self.memory.rebase_clock()
        self.memory.maintain(self.memory._clock + 259200)
        self.assertIsNone(self.runtime.store.get("memories", row["id"]))
        self.assertEqual(self.memory.versions(row["id"]), [])
        self.assertEqual(self.memory.remember("新内容", key="fixed"), {})

    def test_migration_preserves_time_and_archives_once_and_waits_for_persona(self):
        self.runtime.settings["persona_id"] = ""
        self.runtime.store.put(
            "events",
            "e1",
            {
                "id": "e1",
                "text": "去年练琴",
                "scope": "global",
                "source": "fiction",
                "created_at": 1700000000,
            },
        )
        self.runtime.store.put(
            "memories",
            "old",
            {"id": "old", "text": "去年练琴", "source_event_id": "e1", "scope": "global"},
        )
        status = self.memory.migrate()
        self.assertTrue(status["waiting_persona"])
        self.assertEqual(status["pending"], 1)
        self.assertEqual(self.run_async(self.memory.process_pending())["processed"], 0)
        self.runtime.settings["persona_id"] = "可可"
        self.runtime.response = {"memories": [self.candidate("去年练琴")]}
        self.assertEqual(self.run_async(self.memory.process_pending())["processed"], 1)
        self.assertEqual(self.memory.recall()[0]["occurred_at"], "2023-11-14T22:13:20.000000+00:00")
        self.memory.migrate()
        self.assertEqual(self.memory.migration_status()["completed"], 1)
        self.assertIsNotNone(self.runtime.store.get("memory_legacy", "legacy-memory:old"))

    def test_migration_pause_and_source_deletion(self):
        self.runtime.store.put("journals", "j1", {"id": "j1", "text": "日记", "kind": "journal"})
        self.memory.migrate()
        self.memory.pause_migration()
        self.assertEqual(self.run_async(self.memory.process_pending())["processed"], 0)
        self.memory.resume_migration()
        row = self.memory.remember("日记里的事实", source_keys=["journal:j1"])
        self.memory.delete_source("journal:j1")
        self.assertIsNone(self.runtime.store.get("memories", row["id"]))
        self.assertEqual(self.runtime.store.list("memory_jobs"), [])
        self.assertEqual(self.memory.enqueue_material("日记", key="journal:j1"), {})

    def test_settings_validate_ranges_and_default_forgetting_off(self):
        self.assertFalse(memory_settings({"half_life_days": 30})["forgetting_enabled"])
        for config in (
            {"query_timeout_seconds": 16},
            {"chat_batch_rounds": 0},
            {"long_threshold": 2, "medium_threshold": 3},
            {"initial_strength": float("nan")},
        ):
            with self.assertRaises(ValueError):
                memory_settings(config)

    def test_stale_inflight_correction_cannot_overwrite_admin(self):
        old = self.memory.remember("喜欢茶", scope="p")
        self.memory.enqueue_material("喜欢咖啡", key="e", scope="p")

        async def change(task, data):
            self.memory.update(old["id"], {"text": "管理员修正"})
            return json.dumps(
                {"memories": [self.candidate("喜欢咖啡", replace_id=old["id"])]}, ensure_ascii=False
            )

        self.runtime.hook = change
        self.run_async(self.memory.process_pending())
        self.assertEqual(self.runtime.store.get("memories", old["id"])["text"], "管理员修正")

    def test_clock_disabled_interval_is_discarded(self):
        row = self.memory.remember("普通")
        self.runtime.settings["memory"] = {"forgetting_enabled": True}
        self.memory.rebase_clock()
        self.runtime.on = False
        self.memory.maintain(self.memory._clock + 500000)
        self.runtime.on = True
        self.memory.maintain(self.memory._clock + 500000)
        self.assertEqual(self.runtime.store.get("memories", row["id"])["strength"], 10)

    def test_qq_ids_canonicalize_without_guessing_other_identifiers(self):
        raw = self.memory.remember("偏好举例", person_id="42", stable=True)
        prefixed = self.memory.remember("偏好举例", person_id="qq:42", stable=True)
        self.assertEqual(raw["id"], prefixed["id"])
        self.assertEqual(raw["person_id"], "qq:42")
        selection = self.run_async(
            self.memory.select_context(
                "global",
                person_id="42",
                selection=["memory"],
                usage={"limits": {"memory.related": 0}},
            )
        )
        self.assertEqual(selection["memories"][0]["person_id"], "qq:42")
        other = self.memory.remember("未知标识仍原样保留", person_id="import:unknown")
        self.assertEqual(other["person_id"], "import:unknown")
        job = self.memory.enqueue_chat("我喜欢举例", "记住了", scope="p", person_id="42")
        self.assertEqual(job["person_id"], "qq:42")

    def test_inferences_require_factual_basis_on_creation_and_edit(self):
        with self.assertRaises(ValueError):
            self.memory.remember("可能喜欢水彩", inferred=True)
        row = self.memory.remember("可能喜欢水彩", inferred=True, reasoning="多次主动学习水彩")
        with self.assertRaises(ValueError):
            self.memory.update(row["id"], {"reasoning": ""})
        self.assertEqual(self.runtime.store.get("memories", row["id"]), row)

    def test_recall_can_find_absent_people_only_within_allowed_scope(self):
        visible = self.memory.remember("小明正在学 Python", person_id="42", scope="group:1")
        self.memory.remember("小明的私密约定", person_id="42", scope="private:42")
        self.assertEqual(self.memory.recall("小明", scope="group:1", person_id="qq:99"), [visible])
        self.assertEqual(self.memory.recall("小明", scope="group:2", person_id="qq:99"), [])

    def test_migration_does_not_capture_new_persona_sources_on_each_worker(self):
        self.memory.migrate()
        self.runtime.settings["persona_id"] = "新名字"
        self.runtime.store.put(
            "events", "new", {"id": "new", "text": "新名字的经历", "source": "fiction"}
        )
        self.memory.migrate()
        self.assertEqual(self.runtime.store.list("memory_jobs"), [])
        self.assertIsNone(self.runtime.store.get("memory_legacy", "events:new"))
        job = self.memory.enqueue_material("新名字的经历", key="event:new")
        self.assertEqual(job["persona_name"], "新名字")
        self.assertFalse(job["migration"])

    def test_merge_preserves_source_modules_and_deletion_removes_all_versions(self):
        first = self.memory.remember("新闻读到天文学", source="news")
        second = self.memory.remember("搜索读到天文学", source="search", protected=True)
        merged = self.memory.merge([first["id"], second["id"]], "两份来源谈到了天文学")
        self.assertTrue(merged["protected"])
        self.runtime.disabled.add("search")
        self.assertEqual(self.memory.recall("天文学"), [])
        self.memory.delete(merged["id"])
        self.assertEqual(self.runtime.store.list("memories"), [])
        self.assertEqual(self.runtime.store.list("memory_versions"), [])


if __name__ == "__main__":
    unittest.main()
