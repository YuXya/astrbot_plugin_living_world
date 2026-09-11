"""Durable extraction batches, bounded retries and lazy administrative progress."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time

from .layout import resolve_selection

logger = logging.getLogger(__name__)
STATUSES = {
    "pending": "待处理",
    "processing": "处理中",
    "waiting_retry": "等待重试",
    "partial": "部分完成",
    "failed": "失败待处理",
    "completed": "已完成",
    "no_new": "已检查无新增",
}
TERMINAL = {"completed", "no_new"}
FIELDS = (
    "id",
    "status",
    "created_at",
    "updated_at",
    "completed_at",
    "scope",
    "persona_name",
    "attempts",
    "total_attempts",
    "retry_at",
    "error",
    "saved_count",
    "failed_count",
    "source_count",
    "sources",
    "job_ids",
    "memory_ids",
    "needs_apply",
    "retry_blocked",
)


class ExtractionQueue:
    def __init__(self, memory):
        self.memory = memory
        self.runtime = memory.runtime
        self.store = memory.runtime.store

    def _project(self, namespace, fields):
        return self.store.project(namespace, fields)

    def _put(self, batch):
        batch["updated_at"] = time.time()
        self.store.put("memory_batches", batch["id"], batch)

    def create(self, jobs):
        from .memory import _digest

        identity = "batch:" + _digest([job["id"] for job in jobs])
        with self.memory._transaction():
            existing = self.store.get("memory_batches", identity)
            if existing:
                return existing
            if any(self.store.get("memory_jobs", job["id"], {}).get("batch_id") for job in jobs):
                return None
            batch = {
                "id": identity,
                "job_ids": [job["id"] for job in jobs],
                "status": "pending",
                "scope": jobs[0]["scope"],
                "persona_name": jobs[0]["persona_name"],
                "created_at": time.time(),
                "attempts": max(int(job.get("attempts", 0)) for job in jobs),
                "total_attempts": max(int(job.get("attempts", 0)) for job in jobs),
                "retry_at": 0,
                "saved_count": 0,
                "failed_count": 0,
                "error": "",
                "source_count": len(jobs),
                "sources": list(dict.fromkeys(job.get("source", "") for job in jobs)),
                "memory_ids": [],
            }
            if batch["attempts"] >= 2:
                batch.update(status="failed", error="已达到自动重试上限，请手动重试")
            self._put(batch)
            for job in jobs:
                job = {**job, "batch_id": identity}
                self.store.put("memory_jobs", job["id"], job)
        return batch

    def _jobs(self, batch):
        jobs = [self.store.get("memory_jobs", key) for key in batch["job_ids"]]
        if not all(jobs):
            raise ValueError("关联来源已删除，不能继续提炼")
        if any(
            job["scope"] != batch["scope"] or job["persona_name"] != batch["persona_name"]
            for job in jobs
        ):
            raise ValueError("批次的场合或人格归属不一致")
        return jobs

    def _enabled(self, jobs):
        return self.runtime.enabled("memory") and all(
            self.memory._sources_enabled(job) for job in jobs
        )

    def _material(self, batch, jobs):
        materials, background, seen = [], [], set()
        for index, job in enumerate(jobs):
            text = job["text"]
            if job.get("kind") == "chat":
                try:
                    text = json.loads(text)
                except ValueError:
                    pass
            materials.append(
                {
                    "source_id": f"m{index + 1}",
                    "source": job.get("source", ""),
                    "person_id": job.get("person_id", ""),
                    "people": job.get("people", []),
                    "occurred_at": job.get("occurred_at") or "",
                    "content": text,
                }
            )
            if isinstance(text, dict):
                for role, key in (("user", "user"), ("assistant", "reply")):
                    seen.add((role, str(text.get(key, ""))))
        identities = set()
        for job in jobs:
            for message in job.get("background", []):
                if not isinstance(message, dict):
                    continue
                role, content = message.get("role", ""), message.get("content", "")
                if role not in {"user", "assistant"} or not content:
                    continue
                identity = message.get("message_id") or message.get("id")
                pair = (
                    role,
                    json.dumps(content, ensure_ascii=False)
                    if not isinstance(content, str)
                    else content,
                )
                if (identity and str(identity) in identities) or pair in seen:
                    continue
                if identity:
                    identities.add(str(identity))
                seen.add(pair)
                background.append({"role": role, "content": content})
        # Background is supplemental, never a source for a newly attributed fact.
        retained, size = [], 0
        for message in reversed(background[-10:]):
            cost = len(json.dumps(message, ensure_ascii=False))
            if size + cost > 12000:
                continue
            retained.append(message)
            size += cost
        return {
            "persona_name": batch["persona_name"],
            "people": list(
                dict.fromkeys(
                    person
                    for job in jobs
                    for person in self.memory._people(
                        [job.get("person_id", ""), *job.get("people", [])]
                    )
                )
            ),
            "materials": materials,
            "background": list(reversed(retained)),
            "max_memories": self.memory.config["reflection_limit"],
        }

    def _data(self, batch, jobs):
        from .memory import _digest

        material = self._material(batch, jobs)
        selected = resolve_selection(self.runtime.settings, "memory.reflect")
        if "known" not in batch and "memory" in selected:
            batch["known"] = self.memory.recall(
                "\n".join(job["text"] for job in jobs),
                scope=batch["scope"],
                people=material["people"],
                persona_name=batch["persona_name"],
                limit=30,
            )
        known = []
        if "memory" in selected:
            for row in batch.get("known", []):
                current = self.store.get(self.memory.namespace, row["id"])
                if (
                    current
                    and current.get("active", True)
                    and current.get("version") == row.get("version")
                    and self.memory._sources_enabled(current)
                ):
                    known.append(row)
            batch["known"] = known
        batch["allowed_memory_ids"] = [row["id"] for row in known]
        round_ids = {job.get("round_id") for job in jobs if job.get("round_id")}
        if "feedback_ids" not in batch:
            batch["feedback_ids"] = [
                row["id"]
                for row in self._project("memory_feedback", ("id", "scope", "round_id"))
                if row.get("scope") == batch["scope"] and row.get("round_id") in round_ids
            ]
        feedback = [self.store.get("memory_feedback", key) for key in batch["feedback_ids"]]
        feedback = [row for row in feedback if row]
        references = {f"{row['id']}:{row.get('version', 1)}": row for row in known}
        rounds = []
        for row in feedback:
            refs = []
            for memory in row.get("memories", []):
                ref = f"{memory['id']}:{memory.get('version', 1)}"
                refs.append(ref)
                if ref not in references:
                    references[ref] = memory
            job_index = next(
                (index for index, job in enumerate(jobs) if job.get("round_id") == row["round_id"]),
                None,
            )
            if job_index is not None:
                rounds.append(
                    {
                        "round_id": row["round_id"],
                        "source_id": f"m{job_index + 1}",
                        "memory_refs": refs,
                    }
                )
        material["rounds"] = rounds
        material["feedback_memories"] = [
            {
                "ref": ref,
                "id": row["id"],
                "version": row.get("version", 1),
                "judgment": row.get("judgment", row.get("text", "")),
            }
            for ref, row in references.items()
            if row not in known
        ]
        if "entries" in batch:
            material["repairs"] = [
                {
                    "candidate_id": item["id"],
                    "candidate": item["candidate"],
                    "error": item.get("error", "未通过校验"),
                }
                for item in batch["entries"]
                if item["status"] != "saved"
            ]
        batch["material_digest"] = _digest(material["materials"])
        return {
            "material": material,
            "known": known,
            "recent_memories": [],
            "_memory_processing": {"batch_id": batch["id"], "attempt": batch["total_attempts"]},
        }

    def _receive(self, batch, payload):
        candidates = payload.get("memories")
        if not isinstance(candidates, list):
            raise ValueError("输出缺少 memories 数组")
        if len(candidates) > self.memory.config["reflection_limit"]:
            raise ValueError("返回记忆数量超过本次提炼上限")
        if "entries" not in batch:
            batch["entries"] = [
                {"id": f"c{index + 1}", "candidate": candidate, "status": "pending"}
                for index, candidate in enumerate(candidates)
            ]
        else:
            pending = {item["id"]: item for item in batch["entries"] if item["status"] != "saved"}
            returned = {}
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("candidate_id") in pending:
                    identity = candidate["candidate_id"]
                    returned.setdefault(identity, []).append(candidate)
            for identity, item in pending.items():
                if len(returned.get(identity, [])) == 1:
                    item.update(candidate=returned[identity][0], status="pending", error="")
                else:
                    item.update(status="failed", error="修复结果未返回该条目或重复返回同一编号")
        batch["received"] = True
        batch["needs_apply"] = True
        batch["feedback_payload"] = {"feedback": payload.get("feedback", [])}
        self._put(batch)

    def _apply(self, batch, jobs):
        known = [
            row
            for row in batch.get("known", [])
            if row["id"] in batch.get("allowed_memory_ids", [])
        ]
        for index, item in enumerate(batch["entries"]):
            if item["status"] != "pending":
                continue
            try:
                updated = copy.deepcopy(batch)
                with self.memory._transaction():
                    self._jobs(batch)
                    plan = self.memory._plan_extracted(item["candidate"], jobs, known)
                    row = self.memory._save_extracted(plan, batch["persona_name"])
                    result = updated["entries"][index]
                    result.update(
                        status="saved",
                        error="",
                        memory_id=row.get("id", ""),
                        source_ids=[job["id"] for job in plan["supporting"]],
                    )
                    # The candidate receipt and the memory version are committed together.
                    updated["memory_ids"] = list(
                        dict.fromkeys([*updated["memory_ids"], *([row["id"]] if row else [])])
                    )
                    updated["saved_count"] = len(updated["memory_ids"])
                    for job in plan["supporting"]:
                        marker = self.store.get("memory_materials", job["id"], {})
                        marker.update(
                            id=job["id"],
                            key=job["key"],
                            batch_id=batch["id"],
                            digest=job.get("digest", batch["material_digest"]),
                            status="partial",
                            memory_ids=list(
                                dict.fromkeys(
                                    marker.get("memory_ids", []) + ([row["id"]] if row else [])
                                )
                            ),
                        )
                        self.store.put("memory_materials", job["id"], marker)
                    self._put(updated)
                batch = updated
            except Exception as exc:
                batch["entries"][index].update(status="failed", error=str(exc)[:500])
                self._put(batch)
        # Feedback failure cannot roll back accepted memories, or trigger extraction again.
        feedback = []
        try:
            feedback = [
                self.store.get("memory_feedback", key) for key in batch.get("feedback_ids", [])
            ]
            feedback = [row for row in feedback if row]
            with self.memory._transaction():
                self.memory._apply_feedback(batch.get("feedback_payload", {}), feedback)
                self.memory._finish_feedback(feedback)
        except Exception:
            logger.warning("Memory feedback unavailable after extraction", exc_info=True)
            try:
                self.memory._finish_feedback(feedback)
            except Exception:
                logger.warning("Memory feedback receipt unavailable", exc_info=True)
        batch.pop("feedback_payload", None)
        batch["needs_apply"] = False
        batch["failed_count"] = sum(item["status"] != "saved" for item in batch["entries"])
        if batch["failed_count"]:
            error = next(item["error"] for item in batch["entries"] if item["status"] != "saved")
            self._fail(batch, error)
        else:
            self._finish(batch, jobs)
        return batch

    def _finish(self, batch, jobs):
        from .memory import _digest, _now, _stamp

        finished = copy.deepcopy(batch)
        finished.update(
            status="completed" if batch["memory_ids"] else "no_new",
            error="",
            retry_at=0,
            completed_at=time.time(),
            failed_count=0,
        )
        for field in (
            "entries",
            "known",
            "feedback_ids",
            "feedback_payload",
            "received",
            "allowed_memory_ids",
            "needs_apply",
        ):
            finished.pop(field, None)
        with self.memory._transaction():
            self._jobs(batch)
            for job in jobs:
                marker = self.store.get("memory_materials", job["id"], {})
                ids = marker.get("memory_ids", [])
                self.store.put(
                    "memory_materials",
                    job["id"],
                    {
                        "id": job["id"],
                        "key": job["key"],
                        "batch_id": batch["id"],
                        "digest": job.get("digest", _digest(job["text"])),
                        "memory_ids": ids,
                        "status": "completed" if ids else "no_new",
                        "completed_at": _stamp(_now()),
                    },
                )
                self.store.delete("memory_jobs", job["id"])
            self._put(finished)
        batch.clear()
        batch.update(finished)

    def _fail(self, batch, error):
        retryable = batch["attempts"] < 2 and not batch.get("retry_blocked")
        batch.update(
            status="partial"
            if batch.get("saved_count")
            else ("waiting_retry" if retryable else "failed"),
            error=str(error)[:500],
            retry_at=time.time() + 60 if retryable else 0,
        )
        self._put(batch)

    def _audit(self, batch):
        try:
            self.runtime.debug.memory_result(
                batch["id"], batch["total_attempts"], self.summary(batch)
            )
        except Exception:
            logger.debug("Memory processing diagnostic unavailable", exc_info=True)

    async def attempt(self, batch):
        from .memory import _payload

        try:
            try:
                jobs = self._jobs(batch)
            except ValueError as exc:
                batch["retry_blocked"] = True
                self._fail(batch, str(exc))
                self._audit(batch)
                return True
            if not self._enabled(jobs):
                return False
            if batch.get("needs_apply"):
                self._audit(self._apply(batch, jobs))
                return True
            if batch["status"] == "processing":
                self._fail(batch, "上次提炼被中断，未确认结果；已保留处理次数")
                self._audit(batch)
                return True
            if batch["attempts"] >= 2 or batch.get("retry_at", 0) > time.time():
                return False
            batch.update(
                status="processing",
                attempts=batch["attempts"] + 1,
                total_attempts=batch["total_attempts"] + 1,
                retry_at=0,
            )
            self._put(batch)
            if "task.material" not in resolve_selection(self.runtime.settings, "memory.reflect"):
                raise ValueError("本任务未勾选本次任务材料，请调整勾选后手动重试")
            data = self._data(batch, jobs)
            self._put(batch)
            payload = _payload(await self.memory._complete("memory.reflect", data, batch["scope"]))
            self._receive(batch, payload)
            if self._enabled(jobs):
                batch = self._apply(batch, jobs)
        except asyncio.CancelledError:
            current = self.store.get("memory_batches", batch["id"], batch)
            self._fail(current, "提炼被中断，处理次数已保留")
            self._audit(current)
            raise
        except Exception as exc:
            batch = self.store.get("memory_batches", batch["id"], batch)
            self._fail(batch, str(exc) or type(exc).__name__)
            logger.warning("Memory batch requires retry or manual attention: %s", batch["id"])
        self._audit(batch)
        return True

    async def process(self):
        for jobs in self.memory._eligible_batches()[:20]:
            self.create(jobs)
        rows = sorted(self._project("memory_batches", FIELDS), key=lambda row: row["created_at"])
        count = 0
        for row in rows:
            if (
                row["status"] in TERMINAL
                or row.get("retry_blocked")
                or (
                    not row.get("needs_apply")
                    and row["status"] != "processing"
                    and (row.get("attempts", 0) >= 2 or row.get("retry_at", 0) > time.time())
                )
            ):
                continue
            if await self.attempt(self.store.get("memory_batches", row["id"])):
                count += 1
            if count >= 20:
                break
        return {"processed": count, **self.status()}

    def retry(self, identity):
        with self.memory._transaction():
            batch = self.store.get("memory_batches", identity)
            if not batch or not self.summary(batch)["can_retry"]:
                raise ValueError("该批次不处于可手动重试状态，请刷新进度")
            self._jobs(batch)
            batch.update(attempts=0, retry_at=0, status="pending", error="")
            batch.pop("known", None)
            batch.pop("allowed_memory_ids", None)
            self._put(batch)
        self.runtime.kick_memory()
        return self.summary(batch)

    @staticmethod
    def summary(row):
        result = {key: row.get(key) for key in FIELDS if key in row}
        result.update(
            status_label=STATUSES.get(row.get("status"), "已处理（历史标记）"),
            can_retry=row.get("status") in {"failed", "partial"}
            and row.get("attempts", 0) >= 2
            and not row.get("retry_at")
            and not row.get("retry_blocked"),
        )
        return result

    def status(self):
        rows = self._project("memory_batches", FIELDS)
        jobs = self._project("memory_jobs", ("id", "batch_id"))
        counts = {key: sum(row.get("status") == key for row in rows) for key in STATUSES}
        counts["pending"] += sum(not job.get("batch_id") for job in jobs)
        return {
            **counts,
            "processing": self.memory._processing,
            "feedback": len(self._project("memory_feedback", ("id",))),
        }

    def page(self, data):
        page = max(1, int(data.get("page", 1)))
        rows = [self.summary(row) for row in self._project("memory_batches", FIELDS)]
        for job in self._project(
            "memory_jobs", ("id", "batch_id", "created_at", "scope", "source", "persona_name")
        ):
            if not job.get("batch_id"):
                rows.append(
                    {
                        **job,
                        "status": "pending",
                        "status_label": "待处理",
                        "can_retry": False,
                        "source_count": 1,
                        "sources": [job.get("source")],
                        "total_attempts": 0,
                        "saved_count": 0,
                    }
                )
        # Historical receipts are displayed as recorded, never retrospectively diagnosed.
        for marker in self._project(
            "memory_materials", ("id", "batch_id", "completed_at", "memory_ids")
        ):
            if not marker.get("batch_id"):
                rows.append(
                    {
                        **marker,
                        "status": "historical",
                        "status_label": "已处理（历史标记）",
                        "can_retry": False,
                        "created_at": marker.get("completed_at"),
                        "saved_count": len(marker.get("memory_ids", [])),
                    }
                )

        def timestamp(row):
            from .memory import _now

            try:
                return _now(row.get("created_at", 0)).timestamp()
            except (ValueError, TypeError):
                return 0

        rows.sort(key=timestamp, reverse=True)
        if data.get("status"):
            rows = [row for row in rows if row["status"] == data["status"]]
        total = len(rows)
        page = min(page, max(1, (total + 19) // 20))
        return {
            "items": rows[(page - 1) * 20 : page * 20],
            "page": page,
            "total": total,
            "page_size": 20,
            "counts": self.status(),
        }

    def detail(self, identity):
        batch = self.store.get("memory_batches", identity)
        if batch:
            materials = []
            for index, key in enumerate(batch["job_ids"]):
                if row := self.store.get("memory_jobs", key):
                    materials.append({**row, "source_id": f"m{index + 1}"})
            return {
                "summary": self.summary(batch),
                "entries": batch.get("entries", []),
                "materials": materials,
                "receipts": [self.store.get("memory_materials", key) for key in batch["job_ids"]],
            }
        row = self.store.get("memory_jobs", identity) or self.store.get(
            "memory_materials", identity
        )
        if not row:
            raise ValueError("提炼记录已不存在")
        return {"record": row}
