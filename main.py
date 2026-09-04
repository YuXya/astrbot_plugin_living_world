"""Minimal AstrBot entry point for the Living World planning scaffold."""

from collections.abc import AsyncGenerator

from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star


class Main(Star):
    """Expose only a health command during the planning-only release."""

    def __init__(self, context: Context) -> None:
        """Initialize the loadable plugin scaffold.

        Args:
            context: AstrBot plugin context supplied by the runtime.
        """
        super().__init__(context)

    @filter.command("living_world")
    async def living_world(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """Report that the planning scaffold loaded successfully.

        Args:
            event: Incoming AstrBot message event.

        Yields:
            A single health-check response.
        """
        yield event.plain_result(
            "Living World 0.0.1 已加载：当前版本仅包含可加载骨架与主程规划文档。"
        )
