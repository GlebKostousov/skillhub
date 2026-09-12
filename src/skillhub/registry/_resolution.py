"""Задаёт единый бюджет покомпонентного разрешения путей."""

from dataclasses import dataclass

from skillhub.registry._platform_types import UnsafePathError

MAX_RESOLUTION_STEPS = 256
"""Максимальная сумма посещений компонентов и переходов по ссылкам."""

MAX_LINK_HOPS = 40
"""Максимальное число переходов по ссылкам внутри общего бюджета."""


@dataclass(slots=True)
class ResolutionBudget:
    """Учитывает работу одного разрешения пути на любой платформе."""

    steps: int = 0
    link_hops: int = 0

    def visit_component(self) -> None:
        """Учитывает посещение одного компонента пути."""
        self._consume_step()

    def follow_link(self) -> None:
        """Учитывает переход по ссылке и его долю общего бюджета."""
        if self.link_hops >= MAX_LINK_HOPS:
            raise UnsafePathError from None
        self._consume_step()
        self.link_hops += 1

    def require_pending(self, count: int) -> None:
        """Проверяет, что текущая очередь помещается в остаток бюджета.

        Args:
            count: число ещё не посещённых компонентов.
        """
        if self.steps + count > MAX_RESOLUTION_STEPS:
            raise UnsafePathError from None

    def _consume_step(self) -> None:
        if self.steps >= MAX_RESOLUTION_STEPS:
            raise UnsafePathError from None
        self.steps += 1
