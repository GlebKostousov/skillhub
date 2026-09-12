"""Проверяет fail-closed конфигурацию приложения."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from skillhub.app_factory import create_app
from skillhub.core import Settings


def test_settings_start_without_dotenv_or_deepseek_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверяет безопасный старт без файлов и ключа провайдера."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SKILLHUB_ENVIRONMENT", raising=False)

    settings = Settings()

    assert settings.environment == "development"
    assert not hasattr(settings, "deepseek_api_key")


def test_invalid_environment_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет отказ при недопустимом окружении."""
    monkeypatch.setenv("SKILLHUB_ENVIRONMENT", "unsafe")

    with pytest.raises(ValidationError):
        Settings()


def test_extra_configuration_fails_closed() -> None:
    """Проверяет отказ при неизвестном параметре конфигурации."""
    with pytest.raises(ValidationError):
        Settings.model_validate({"unexpected": "enabled"})


def test_unknown_environment_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ при опечатке в переменной окружения SkillHub."""
    monkeypatch.setenv("SKILLHUB_UNEXPECTED", "enabled")

    with pytest.raises(ValidationError) as captured:
        Settings()

    assert "enabled" not in str(captured.value)


def test_unrelated_environment_variable_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет изоляцию системных переменных с другим префиксом."""
    monkeypatch.setenv("OPERATING_SYSTEM_SETTING", "enabled")

    assert Settings().environment == "development"


def test_app_factory_fails_closed_on_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет остановку сборки при неверной конфигурации."""
    monkeypatch.setenv("SKILLHUB_ENVIRONMENT", "unsafe")

    with pytest.raises(ValidationError):
        create_app()
