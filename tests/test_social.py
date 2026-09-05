"""Transport-free social behavior and persistence tests."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import random
import unittest
from datetime import datetime, timedelta, timezone

from living_world.config import DEFAULTS
from living_world.social import SocialService, destination
from living_world.store import Store

GROUP = "qq:GroupMessage:100"
FRIEND = "qq:FriendMessage:200"


class Host:
    def __init__(self):
        self.sent = []
        self.histories = []
        self.group_history_on = True
        self.host_interjection_on = False
        self.send_result = True
        self.send_error = None
        self.send_entered = asyncio.Event()
        self.send_gate = None

    def group_history_enabled(self, scope):
        return self.group_history_on

    def host_interjection_enabled(self, scope):
        return self.host_interjection_on

    async def history(self, scope):
        self.histories.append(scope)
        return f"只属于 {scope} 的近期话题"

    async def send(self, scope, text):
        self.sent.append((scope, text))
        self.send_entered.set()
        if self.send_gate:
            await self.send_gate.wait()
        if self.send_error:
            raise self.send_error
        return self.send_result


class Runtime:
    def __init__(self):
        self.store = Store(":memory:")
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings["modules"].update({"proactive": True, "interjection": True})
        self.settings["sessions"] = [{"umo": GROUP, "weight": 1}, {"umo": FRIEND, "weight": 1}]
        self.settings["social"].update(
            {
                "quiet_start": "00:00",
                "quiet_end": "00:00",
                "cooldown_minutes": 0,
                "daily_limit": 100,
            }
        )
        self.host = Host()
        self.allowed = True
        self.model_calls = []
        self.context_calls = []
        self.recorded_events = []
        self.decision = {"should_reply": True}
        self.same_text = None
        self.generate_error = None
        self.after_generate = None
        self.during_scope_allowed = None

    def enabled(self, module):
        return self.settings["modules"].get(module, False)

    async def scope_allowed(self, scope):
        if self.during_scope_allowed:
            self.during_scope_allowed()
        return self.allowed

    async def context_text(self, scope, person_id="", query=""):
        self.context_calls.append((scope, person_id, query))
        return f"仅供 {scope} 使用的记忆"

    async def generate(self, module, prompt, scope="global"):
        self.model_calls.append((module, prompt, scope))
        if self.generate_error:
            raise self.generate_error
        if self.after_generate:
            outcome = self.after_generate(module, prompt, scope)
            if inspect.isawaitable(outcome):
                await outcome
        if prompt.startswith("判断角色"):
            return json.dumps(self.decision)
        return self.same_text or f"发给 {scope} 的话题消息 {len(self.model_calls)}"

    def record_event(self, text, **kwargs):
        self.recorded_events.append({"text": text, **kwargs})


class SocialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.now = datetime(2026, 9, 5, 12, tzinfo=timezone(timedelta(hours=8)))
        self.social = self.service()

    def service(self):
        service = SocialService(self.runtime)
        service._now = lambda: self.now
        service.rng = random.Random(42)
        return service

    def tearDown(self):
        self.runtime.store.close()

    def test_weighted_selection_gives_low_weight_a_chance(self):
        candidates = [
            {"scope": GROUP, "destination": GROUP, "weight": 99},
            {"scope": FRIEND, "destination": FRIEND, "weight": 1},
        ]
        selected = [self.social._draw(candidates, 1)[0]["scope"] for _ in range(1000)]
        self.assertGreater(selected.count(FRIEND), 0)
        self.assertGreater(selected.count(GROUP), selected.count(FRIEND) * 20)

    async def test_no_duplicate_actual_groups_even_with_session_aliases(self):
        self.runtime.settings["sessions"] = [
            {"umo": "qq:GroupMessage:1_100", "weight": 1},
            {"umo": "qq:GroupMessage:2_100", "weight": 9},
            {"umo": "qq:GroupMessage:100", "weight": 1},
            {"umo": FRIEND, "weight": 1},
        ]
        self.runtime.settings["social"]["target_count"] = 20
        result = await self.social.send("数学课无聊，找群聊天", action_id="class")
        targets = [destination(scope) for scope, _ in self.runtime.host.sent]
        self.assertEqual(result["status"], "success")
        self.assertEqual(set(targets), {GROUP, FRIEND})
        self.assertEqual(len(targets), 2)
        self.assertTrue(
            all(
                scope in {row["umo"] for row in self.runtime.settings["sessions"]}
                for scope, _ in self.runtime.host.sent
            )
        )

    async def test_user_group_session_keeps_original_transport_scope_and_user_identity(self):
        scope = "qq:GroupMessage:123_456"
        self.runtime.settings["sessions"] = [{"umo": scope, "weight": 1}]
        result = await self.social.send(target_scope=scope, action_id="unique-session")
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.runtime.host.sent[0][0], scope)
        self.assertEqual(self.runtime.context_calls[0][1], "qq:123")
        self.assertEqual(result["deliveries"][0]["destination"], "qq:GroupMessage:456")

    async def test_zero_weight_and_disabled_sessions_are_excluded(self):
        self.runtime.settings["sessions"] = [
            {"umo": GROUP, "weight": 0},
            {"umo": FRIEND, "weight": 1, "enabled": False},
        ]
        self.assertEqual((await self.social.send())["reason"], "no_eligible_targets")
        self.assertEqual(self.runtime.model_calls, [])

    async def test_scoped_contact_uses_only_its_own_target_and_context(self):
        self.runtime.settings["social"]["target_count"] = 20
        result = await self.social.send("a private promise", scope=FRIEND, action_id="private")
        self.assertEqual([scope for scope, _ in self.runtime.host.sent], [FRIEND])
        self.assertEqual(self.runtime.host.histories, [FRIEND])
        self.assertEqual(self.runtime.context_calls[0][1], "qq:200")
        self.assertEqual(result["deliveries"][0]["scope"], FRIEND)
        blocked = await self.social.send("private details", scope=FRIEND, target_scope=GROUP)
        self.assertEqual(blocked["reason"], "source_scope_mismatch")
        self.assertEqual(len(self.runtime.host.sent), 1)

    async def test_each_target_gets_its_own_context_and_global_result_omits_messages(self):
        self.runtime.settings["social"]["target_count"] = 2
        result = await self.social.send("share today's class", action_id="share")
        self.assertEqual(len(self.runtime.model_calls), 2)
        for _, prompt, scope in self.runtime.model_calls:
            self.assertIn(f"只属于 {scope}", prompt)
            other = GROUP if scope == FRIEND else FRIEND
            self.assertNotIn(f"只属于 {other}", prompt)
        self.assertNotIn("发给", json.dumps(result, ensure_ascii=False))
        self.assertTrue(
            all(row["text"].startswith("发给") for row in self.runtime.store.list("deliveries"))
        )

    async def test_persona_mismatch_prevents_model_or_transport_call(self):
        self.runtime.allowed = False
        result = await self.social.send(target_scope=GROUP)
        self.assertEqual(result["reason"], "persona_or_session_mismatch")
        self.assertEqual(self.runtime.model_calls, [])
        self.assertEqual(self.runtime.host.sent, [])

    async def test_pause_during_generation_discards_generated_message(self):
        self.runtime.after_generate = lambda *_: self.runtime.settings["modules"].update(
            proactive=False
        )
        result = await self.social.send(target_scope=GROUP, action_id="paused")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "module_disabled")
        self.assertEqual(self.runtime.host.sent, [])

    async def test_whitelist_removal_during_generation_prevents_send(self):
        self.runtime.after_generate = lambda *_: self.runtime.settings.update(sessions=[])
        result = await self.social.send(target_scope=GROUP)
        self.assertEqual(result["reason"], "not_whitelisted")
        self.assertEqual(self.runtime.host.sent, [])

    async def test_persona_change_during_generation_prevents_send(self):
        self.runtime.after_generate = lambda *_: setattr(self.runtime, "allowed", False)
        result = await self.social.send(target_scope=GROUP)
        self.assertEqual(result["reason"], "persona_or_session_mismatch")
        self.assertEqual(self.runtime.host.sent, [])

    async def test_disable_while_resolving_persona_prevents_generation(self):
        self.runtime.during_scope_allowed = lambda: self.runtime.settings["modules"].update(
            proactive=False
        )
        result = await self.social.send(target_scope=GROUP)
        self.assertEqual(result["reason"], "module_disabled")
        self.assertEqual(self.runtime.model_calls, [])

    async def test_cooldown_and_daily_limit_are_shared_by_group_aliases(self):
        alias_a, alias_b = "qq:GroupMessage:1_100", "qq:GroupMessage:2_100"
        self.runtime.settings["sessions"] = [{"umo": alias_a}, {"umo": alias_b}]
        self.runtime.settings["social"]["cooldown_minutes"] = 60
        await self.social.send(target_scope=alias_a, action_id="one")
        result = await self.social.send(target_scope=alias_b, action_id="two")
        self.assertEqual(result["reason"], "cooldown")
        self.now += timedelta(minutes=61)
        self.runtime.settings["social"]["daily_limit"] = 1
        result = await self.social.send(target_scope=alias_b, action_id="three")
        self.assertEqual(result["reason"], "daily_limit")
        self.assertEqual(len(self.runtime.host.sent), 1)

    async def test_quiet_hours_cross_midnight_and_end_at_configured_boundary(self):
        self.runtime.settings["social"].update(quiet_start="23:00", quiet_end="08:00")
        self.now = self.now.replace(hour=1)
        self.assertEqual((await self.social.send(target_scope=GROUP))["reason"], "quiet_hours")
        self.now = self.now.replace(hour=8)
        self.assertEqual((await self.social.send(target_scope=GROUP))["status"], "success")

    async def test_same_action_cannot_send_again_after_service_restart(self):
        first = await self.social.send(target_scope=GROUP, action_id="persistent-action")
        result = await self.service().send(target_scope=FRIEND, action_id="persistent-action")
        self.assertEqual(first["status"], "success")
        self.assertEqual(result["reason"], "already_attempted")
        self.assertEqual(len(self.runtime.host.sent), 1)

    async def test_content_deduplication_is_per_destination_per_day(self):
        self.runtime.same_text = "数学课好无聊，想来聊几句"
        await self.social.send(target_scope=GROUP, action_id="first")
        duplicate = await self.social.send(target_scope=GROUP, action_id="second")
        self.assertEqual(duplicate["reason"], "duplicate_content")
        other = await self.social.send(target_scope=FRIEND, action_id="third")
        self.assertEqual(other["status"], "success")
        self.now += timedelta(days=1)
        next_day = await self.social.send(target_scope=GROUP, action_id="fourth")
        self.assertEqual(next_day["status"], "success")
        self.assertEqual(len(self.runtime.host.sent), 3)

    async def test_pending_claim_precedes_transport_and_cancel_is_not_replayed(self):
        self.runtime.host.send_gate = asyncio.Event()
        task = asyncio.create_task(self.social.send(target_scope=GROUP, action_id="cancelled"))
        await asyncio.wait_for(self.runtime.host.send_entered.wait(), 1)
        self.assertEqual(self.runtime.store.list("deliveries")[0]["status"], "pending")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.runtime.store.list("deliveries")[0]["status"], "unknown")
        result = await self.service().send(target_scope=GROUP, action_id="cancelled")
        self.assertEqual(result["reason"], "already_attempted")
        self.runtime.settings["social"]["daily_limit"] = 1
        result = await self.service().send(target_scope=GROUP, action_id="new-attempt")
        self.assertEqual(result["reason"], "daily_limit")
        self.assertEqual(len(self.runtime.host.sent), 1)

    async def test_transport_rejection_is_failed_and_same_action_never_retries(self):
        self.runtime.host.send_result = False
        result = await self.social.send(target_scope=GROUP, action_id="rejected")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.runtime.store.list("deliveries")[0]["status"], "failed")
        await self.service().send(target_scope=GROUP, action_id="rejected")
        self.assertEqual(len(self.runtime.host.sent), 1)

    async def test_transport_exception_is_unknown_and_counts_towards_limit(self):
        self.runtime.host.send_error = OSError("connection closed")
        self.runtime.settings["social"]["daily_limit"] = 1
        result = await self.social.send(target_scope=GROUP, action_id="unknown")
        self.assertEqual(result["status"], "failed")
        self.assertIn("发送结果未确认", result["text"])
        self.assertEqual(self.runtime.store.list("deliveries")[0]["status"], "unknown")
        self.assertEqual(
            (await self.social.send(target_scope=GROUP, action_id="next"))["reason"], "daily_limit"
        )

    async def test_model_failure_does_not_send_or_retry_action(self):
        self.runtime.generate_error = RuntimeError("model down")
        result = await self.social.send(target_scope=GROUP, action_id="model-error")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.runtime.host.sent, [])
        await self.service().send(target_scope=GROUP, action_id="model-error")
        self.assertEqual(len(self.runtime.model_calls), 1)

    async def test_concurrent_contacts_share_one_budget(self):
        self.runtime.settings["social"]["daily_limit"] = 1
        results = await asyncio.gather(
            self.social.send(target_scope=GROUP, action_id="one"),
            self.social.send(target_scope=GROUP, action_id="two"),
        )
        self.assertEqual([result["status"] for result in results].count("success"), 1)
        self.assertEqual(len(self.runtime.host.sent), 1)

    async def test_host_interjection_prevents_plugin_model_decision(self):
        self.runtime.host.host_interjection_on = True
        result = await self.social.interject(GROUP, "聊聊数学", person_id="qq:1", event_id="one")
        self.assertEqual(result["reason"], "host_interjection_enabled")
        self.assertEqual(self.runtime.model_calls, [])

    async def test_host_interjection_enabled_during_decision_prevents_send(self):
        self.runtime.after_generate = lambda *_: setattr(
            self.runtime.host, "host_interjection_on", True
        )
        result = await self.social.interject(GROUP, "聊聊数学", event_id="one")
        self.assertEqual(result["reason"], "host_interjection_enabled")
        self.assertEqual(len(self.runtime.model_calls), 1)
        self.assertEqual(self.runtime.host.sent, [])

    async def test_interjection_decisions_are_rate_limited_across_restart(self):
        self.runtime.decision = {"should_reply": False}
        first = await self.social.interject(GROUP, "第一条", event_id="one")
        self.assertEqual(first["reason"], "no_relevant_contribution")
        second = await self.service().interject(GROUP, "第二条", event_id="two")
        self.assertEqual(second["reason"], "interjection_interval")
        self.assertEqual(len(self.runtime.model_calls), 1)
        self.now += timedelta(minutes=31)
        await self.social.interject(GROUP, "第三条", event_id="three")
        self.assertEqual(len(self.runtime.model_calls), 2)

    async def test_interjection_records_actual_scoped_send_without_claiming_response(self):
        result = await self.social.interject(
            GROUP, "讨论几何作业", person_id="qq:123", event_id="homework"
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(all(call[1] == "qq:123" for call in self.runtime.context_calls))
        event = self.runtime.recorded_events[0]
        self.assertEqual(
            (event["scope"], event["source"], event["kind"]), (GROUP, "action", "social")
        )
        self.assertIn("尚未收到回应", event["text"])

    async def test_interjection_remains_independent_from_proactive_switch(self):
        self.runtime.settings["modules"]["proactive"] = False
        self.assertEqual((await self.social.send(target_scope=GROUP))["reason"], "module_disabled")
        self.assertEqual(
            (await self.social.interject(GROUP, "几何作业", event_id="one"))["status"], "success"
        )

    async def test_interjection_requires_group_history_and_real_boolean(self):
        self.runtime.host.group_history_on = False
        result = await self.social.interject(GROUP, "hello")
        self.assertEqual(result["reason"], "group_history_disabled")
        self.assertEqual(self.runtime.model_calls, [])
        self.runtime.host.group_history_on = True
        self.runtime.decision = {"should_reply": "true"}
        result = await self.social.interject(GROUP, "hello")
        self.assertEqual(result["reason"], "no_relevant_contribution")
        self.assertEqual(self.runtime.host.sent, [])

    async def test_busy_social_service_drops_interjection_instead_of_queuing(self):
        self.runtime.host.send_gate = asyncio.Event()
        task = asyncio.create_task(self.social.send(target_scope=FRIEND, action_id="busy"))
        await asyncio.wait_for(self.runtime.host.send_entered.wait(), 1)
        self.assertEqual((await self.social.interject(GROUP, "hello"))["reason"], "social_busy")
        self.runtime.host.send_gate.set()
        await task


class RuntimeSocialBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_runtime_keeps_private_generated_content_out_of_global_results(self):
        from living_world.runtime import Runtime as ActualRuntime

        class IntegrationHost(Host):
            async def persona(self, persona_id):
                return "学生角色"

            async def session_persona(self, scope):
                return "student"

            async def generate(self, provider, prompt, system, scope):
                return "只在私聊中谈到的生日惊喜", {}

        host = IntegrationHost()
        runtime = ActualRuntime(":memory:", host)
        try:
            await runtime.update_settings(
                {
                    "persona_id": "student",
                    "sessions": [{"umo": FRIEND, "weight": 1}, {"umo": GROUP, "weight": 0}],
                    "modules": {"proactive": True},
                    "social": {"quiet_start": "00:00", "quiet_end": "00:00"},
                }
            )
            result = await runtime.execute_action(
                "social", {"reason": "想找人聊聊"}, "global", "private-boundary"
            )
            self.assertEqual(result["status"], "success")
            self.assertNotIn("生日惊喜", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("生日惊喜", await runtime.context_text("global"))
            self.assertNotIn("生日惊喜", await runtime.context_text(GROUP))
            self.assertIn("生日惊喜", await runtime.context_text(FRIEND))
            self.assertEqual({row["scope"] for row in runtime.store.list("events")}, {FRIEND})
        finally:
            await runtime.stop()


if __name__ == "__main__":
    unittest.main()
