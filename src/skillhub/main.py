"""Публикует собранное ASGI-приложение SkillHub."""

from skillhub._server import create_process_app

app = create_process_app()
