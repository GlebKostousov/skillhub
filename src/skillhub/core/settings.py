"""Определяет минимальную конфигурацию с отказом при ошибке."""

from pathlib import Path
from typing import Any, Literal

from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

Environment = Literal["development", "test", "production"]


class _StrictEnvironmentSource(EnvSettingsSource):
    """Источник окружения со списком разрешённых переменных SkillHub."""

    def __call__(self) -> dict[str, Any]:
        """Возвращает известные и неизвестные переменные с префиксом.

        Returns:
            Данные окружения для последующей строгой валидации.
        """
        values = super().__call__()
        prefix = self._apply_case_sensitive(self.env_prefix)
        allowed_names = {
            environment_name
            for field_name, field in self.settings_cls.model_fields.items()
            for _, environment_name, _ in self._extract_field_info(field, field_name)
        }
        for environment_name in sorted(self.env_vars):
            if (
                environment_name.startswith(prefix)
                and environment_name not in allowed_names
            ):
                field_name = environment_name[len(prefix) :]
                values[field_name] = self.env_vars[environment_name]
        return values


class Settings(BaseSettings):
    """Проверенная конфигурация без секретных запасных значений.

    Attributes:
        environment: режим представления технических логов.
        usage_path: путь к файлу журнала расходов.
        daily_budget_nanos: дневной потолок в нано-USD или его отсутствие.
    """

    model_config = SettingsConfigDict(
        env_prefix="SKILLHUB_",
        env_file=None,
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
        """Устанавливает строгий источник переменных окружения.

        Args:
            settings_cls: класс собираемой конфигурации.
            init_settings: значения явных аргументов.
            env_settings: стандартный источник окружения.
            dotenv_settings: отключённый источник dotenv.
            file_secret_settings: источник файловых секретов.

        Returns:
            Источники в порядке их приоритета.
        """
        del env_settings
        return (
            init_settings,
            _StrictEnvironmentSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )
