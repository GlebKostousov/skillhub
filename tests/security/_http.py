"""Собирает общие HTTP-помощники атакующих проверок."""

from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
from httpx2 import Response

from skillhub.app_factory import create_app
from skillhub.llm import FakeLlmGateway, LlmRequest, LlmResult, LlmUsage


class QueuedFakeGateway(FakeLlmGateway):
    """Возвращает очередь заранее заданных ответов тестового шва."""

    def __init__(self, *texts: str) -> None:
        """Сохраняет ответы в порядке вызовов.

        Args:
            texts: тексты успешных ответов шлюза.
        """
        super().__init__(result=_llm_result(texts[0] if texts else ""))
        self._texts = list(texts)

    def complete(self, request: LlmRequest) -> LlmResult:
        """Выдаёт следующий заранее заданный ответ.

        Args:
            request: типизированный запрос вызывающей стороны.

        Returns:
            Следующий сохранённый результат.
        """
        self.requests.append(request)
        return _llm_result(self._texts.pop(0))


class _CsrfParser(HTMLParser):
    """Собирает скрытый CSRF-токен из разметки."""

    def __init__(self) -> None:
        """Создаёт пустой результат разбора."""
        super().__init__()
        self.token = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает значение поля csrf_token.

        Args:
            tag: имя открывающего HTML-тега.
            attrs: атрибуты открывающего HTML-тега.
        """
        attributes = dict(attrs)
        if tag != "input" or attributes.get("name") != "csrf_token":
            return
        token = attributes.get("value")
        if token:
            self.token = token


def write_skill(
    directory: Path,
    *,
    name: str,
    caption: str = "Подпись",
    description: str = "Описание режима.",
    body: str,
) -> Path:
    """Записывает SKILL.md с заданным телом.

    Args:
        directory: каталог скилла.
        name: каноническое имя.
        caption: подпись карточки.
        description: описание для классификатора.
        body: тело инструкции.

    Returns:
        Путь записанного файла.
    """
    directory.mkdir(parents=True, exist_ok=True)
    header = f"name: {name}\ncaption: {caption}\ndescription: {description}"
    content = f"---\n{header}\n---\n{body}"
    skill_file = directory / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def app_client(
    root: Path,
    *texts: str,
) -> tuple[TestClient, QueuedFakeGateway]:
    """Собирает клиент с очередью ответов модели.

    Args:
        root: корень тестового каталога скиллов.
        texts: ответы шлюза в порядке вызовов.

    Returns:
        Клиент и подставленный шлюз.
    """
    gateway = QueuedFakeGateway(*texts)
    client = TestClient(create_app(skills_root=root, llm_gateway=gateway))
    return client, gateway


def csrf_token(client: TestClient) -> str:
    """Читает CSRF-токен с корневой страницы.

    Args:
        client: HTTP-клиент приложения.

    Returns:
        Значение скрытого поля csrf_token.
    """
    parser = _CsrfParser()
    parser.feed(client.get("/").text)
    return parser.token


def post_assistant(
    client: TestClient,
    token: str,
    intent: str,
    material: str,
) -> Response:
    """Отправляет JSON-запрос ассистента с CSRF в заголовке.

    Args:
        client: HTTP-клиент приложения.
        token: секрет CSRF.
        intent: намерение пользователя.
        material: недоверенный материал.

    Returns:
        HTTP-ответ окна ассистента.
    """
    return client.post(
        "/api/assistant",
        json={"intent": intent, "material": material},
        headers={
            "origin": "http://testserver",
            "x-csrf-token": token,
        },
    )


def _llm_result(text: str) -> LlmResult:
    return LlmResult(
        text=text,
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
