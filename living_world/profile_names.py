"""Resolve administrator profile labels through the originating QQ connection."""

import asyncio
import copy
import re
from time import monotonic

from .social import destination


def qq_number(value):
    match = re.fullmatch(r"(?:qq:)?(\d+)", str(value or ""), re.IGNORECASE)
    return str(int(match[1])) if match else ""


def profile_labels(runtime, profiles, memories):
    """Resolve local labels immediately and return only uncached lookup descriptors."""
    result = copy.deepcopy(profiles)
    origins = {}
    for memory in memories:
        number = qq_number(memory.get("person_id"))
        try:
            scope = destination(memory.get("scope", ""))
        except (ValueError, AttributeError):
            continue
        if number:
            origins.setdefault(number, []).append(scope)
    configured = []
    for session in runtime.settings.get("sessions", []):
        try:
            configured.append(
                (destination(session.get("umo", "")), session.get("display_name", ""))
            )
        except (ValueError, AttributeError):
            continue
    connections = {scope.split(":", 1)[0] for scope, _ in configured}
    pending = []
    for profile in result:
        if profile.get("owner") != "person":
            continue
        number = qq_number(profile.get("person_id"))
        saved = str(profile.get("name") or "").strip()
        # Old profile records used the identity itself as a placeholder name.
        profile.update(
            name=saved if saved and qq_number(saved) != number else "未获取昵称",
            name_status="unavailable",
        )
        if not number:
            continue
        private = [
            (scope, name)
            for scope, name in configured
            if scope.endswith(":FriendMessage:" + number)
        ]
        manual = next(
            (name.strip() for _, name in private if isinstance(name, str) and name.strip()), ""
        )
        if manual:
            profile.update(name=manual, name_status="manual")
            continue
        scopes = origins.get(number) or [scope for scope, _ in private]
        connection = (
            scopes[0].split(":", 1)[0]
            if scopes
            else next(iter(connections))
            if len(connections) == 1
            else ""
        )
        if connection:
            target = f"{connection}:FriendMessage:{number}"
            cached = runtime.social._target_names.get(target)
            if cached and cached[0] > monotonic():
                if cached[1]:
                    profile.update(name=cached[1], name_status="resolved")
            else:
                pending.append((profile, target, scopes[0] if scopes else target))
    return result, pending


async def named_profiles(runtime, profiles, memories):
    """Decorate read-only labels with a bounded, connection-specific lookup."""
    result, pending = profile_labels(runtime, profiles, memories)
    semaphore = asyncio.Semaphore(6)
    started = set()

    async def resolve(profile, target, source_scope):
        async with semaphore:
            started.add(target)
            name = await runtime.social._target_name(target, source_scope=source_scope)
            if name:
                profile.update(name=name, name_status="resolved")

    tasks = {asyncio.create_task(resolve(*item)): item[1] for item in pending}
    if tasks:
        # A large profile bank or a disconnected bot must not stall the page serially.
        try:
            _, pending = await asyncio.wait(tasks, timeout=2)
            for task in pending:
                target = tasks[task]
                cached = runtime.social._target_names.get(target)
                if target in started and (not cached or cached[0] <= monotonic()):
                    runtime.social._target_names[target] = (monotonic() + 60, "")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return result
