"""Двигает курсор по нормализованным строкам исходного текста."""


class LineCursor:
    """Хранит текущую позицию в строках протокола."""

    def __init__(self, lines: tuple[str, ...]) -> None:
        """Сохраняет исходные строки и начинает чтение с первой.

        Args:
            lines: нормализованные строки без символов перевода.
        """
        self.lines = lines
        self.index = 0

    @property
    def line_number(self) -> int:
        """Возвращает 1-based номер текущей или завершающей строки."""
        if self.index < len(self.lines):
            return self.index + 1
        return max(len(self.lines), 1)

    def at_end(self) -> bool:
        """Проверяет исчерпание исходных строк."""
        return self.index >= len(self.lines)

    def peek(self) -> str:
        """Возвращает текущую строку или пустую метку конца текста."""
        if self.at_end():
            return ""
        return self.lines[self.index]

    def take(self) -> str:
        """Считывает текущую строку и сдвигает курсор вперёд."""
        line = self.peek()
        self._advance()
        return line

    def skip_blank(self) -> None:
        """Пропускает пустые строки-разделители."""
        while not self.at_end() and self.peek() == "":
            self.index += 1

    def _advance(self) -> None:
        """Сдвигает курсор, если исходные строки ещё не исчерпаны."""
        if not self.at_end():
            self.index += 1
