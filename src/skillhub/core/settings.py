"""Определяет минимальную конфигурацию с отказом при ошибке."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

Environment = Literal["development", "test", "production"]


def _strict_values(
    values: dict[str, Any],
    *,
    field_names: set[str],
    prefix: str,
    allowed_env_names: set[str],
    env_vars: Mapping[str, Any],
) -> dict[str, Any]:
    """Оставляет поля модели и неизвестные переменные с префиксом.

    Args:
        values: разобранные значения источника.
        field_names: имена полей Settings.
        prefix: префикс переменных SkillHub с учётом регистра.
        allowed_env_names: известные имена переменных полей.
        env_vars: сырые переменные источника.

    Returns:
        Данные для последующей строгой валидации.
    """
    filtered = {key: value for key, value in values.items() if key in field_names}
    for environment_name in sorted(env_vars):
        if (
            environment_name.startswith(prefix)
            and environment_name not in allowed_env_names
        ):
            field_name = environment_name[len(prefix) :]
            filtered[field_name] = env_vars[environment_name]
    return filtered


class _StrictEnvironmentSource(EnvSettingsSource):
    """Источник окружения со списком разрешённых переменных SkillHub."""

    def __call__(self) -> dict[str, Any]:
        """Возвращает известные и неизвестные переменные с префиксом.

        Returns:
            Данные окружения для последующей строгой валидации.
        """
        prefix = self._apply_case_sensitive(self.env_prefix)
        allowed_names = {
            environment_name
            for field_name, field in self.settings_cls.model_fields.items()
            for _, environment_name, _ in self._extract_field_info(field, field_name)
        }
        return _strict_values(
            super().__call__(),
            field_names=set(self.settings_cls.model_fields),
            prefix=prefix,
            allowed_env_names=allowed_names,
            env_vars=self.env_vars,
        )


class _StrictDotEnvSource(DotEnvSettingsSource):
    """Источник `.env` со списком разрешённых переменных SkillHub."""

    def __call__(self) -> dict[str, Any]:
        """Возвращает известные и неизвестные переменные файла.

        Returns:
            Данные файла для последующей строгой валидации.
        """
        prefix = self._apply_case_sensitive(self.env_prefix)
        allowed_names = {
            environment_name
            for field_name, field in self.settings_cls.model_fields.items()
            for _, environment_name, _ in self._extract_field_info(field, field_name)
        }
        return _strict_values(
            super().__call__(),
            field_names=set(self.settings_cls.model_fields),
            prefix=prefix,
            allowed_env_names=allowed_names,
            env_vars=self.env_vars,
        )


class Settings(BaseSettings):
    """Проверенная конфигурация без секретных запасных значений.

    Attributes:
        environment: режим представления технических логов.
        usage_path: путь к файлу журнала расходов.
        daily_budget_nanos: дневной потолок в нано-USD или его отсутствие.
    """

    model_config = SettingsConfigDict(
        env_prefix="SKILLHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    environment: Environment = "development"
    usage_path: Path = Path("usage.sqlite3")
    daily_budget_nanos: int | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Устанавливает источники: процесс, затем локальный `.env`.

        Args:
            settings_cls: класс собираемой конфигурации.
            init_settings: значения явных аргументов.
            env_settings: стандартный источник окружения.
            dotenv_settings: стандартный источник dotenv.
            file_secret_settings: источник файловых секретов.

        Returns:
            Источники в порядке их приоритета.
        """
        del env_settings
        del dotenv_settings
        return (
            init_settings,
            _StrictEnvironmentSource(settings_cls),
            _StrictDotEnvSource(settings_cls),
            file_secret_settings,
        )
