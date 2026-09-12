"""Проверяет публичный фасад тарифов моделей."""

import locale
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from skillhub.usage import (
    InvalidCurrencyError,
    InvalidPriceError,
    TariffError,
    TariffSnapshot,
    TokenCounts,
    UnknownFieldError,
    UnknownModelError,
    calculate_cost,
    load_tariffs,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_TARIFF_PATH = _REPOSITORY_ROOT / "config" / "model-tariffs.yaml"
_LEAK_MARKER = "sk-leak-marker-must-stay-out"
_README = _REPOSITORY_ROOT / "README.md"
_SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing"
_VERIFIED_AT = "2026-09-11"
_SECRET_KEYS = frozenset({"api_key", "token", "password"})
_NON_ENGLISH_TIME_LOCALES = (
    "ru_RU.UTF-8",
    "Russian_Russia.1251",
    "Russian_Russia",
    "ru_RU",
    "Russian",
)


def test_valid_yaml_loads_deepseek_flash_limits_and_prices() -> None:
    """Проверяет загрузку типизированных лимитов и цен deepseek-flash."""
    catalog = load_tariffs(_TARIFF_PATH)
    tariff = catalog.model("deepseek-flash")

    assert tariff.model == "deepseek-flash"
    assert tariff.max_tokens == 4096
    assert tariff.timeout == 30
    assert tariff.temperature == 0
    assert tariff.peak.cache_hit == Decimal("0.006")
    assert tariff.peak.cache_miss == Decimal("0.30")
    assert tariff.peak.output == Decimal("1.20")
    assert tariff.off_peak.cache_hit == Decimal("0.003")
    assert tariff.off_peak.cache_miss == Decimal("0.15")
    assert tariff.off_peak.output == Decimal("0.60")


def test_unknown_field_like_api_key_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ неизвестного поля без утечки секрета."""
    path = _write_text(tmp_path, _committed_text() + f"\napi_key: {_LEAK_MARKER}\n")

    with pytest.raises(UnknownFieldError) as captured:
        load_tariffs(path)

    assert captured.value.code == "unknown_field"
    assert _LEAK_MARKER not in str(captured.value)
    assert _LEAK_MARKER not in repr(captured.value)


def test_unknown_model_is_rejected_at_load(tmp_path: Path) -> None:
    """Проверяет отказ неизвестного имени модели при загрузке."""
    path = _write_text(tmp_path, _committed_text().replace("deepseek-flash", "GPT-4"))

    with pytest.raises(UnknownModelError) as captured:
        load_tariffs(path)

    assert captured.value.code == "unknown_model"


def test_unknown_model_is_rejected_from_catalog() -> None:
    """Проверяет отказ модели, которой нет в загруженном каталоге."""
    catalog = load_tariffs(_TARIFF_PATH)

    with pytest.raises(UnknownModelError) as captured:
        catalog.model("missing-model")

    assert captured.value.code == "unknown_model"


def test_negative_price_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ отрицательной цены при загрузке."""
    path = _write_text(tmp_path, _committed_text().replace('"0.006"', '"-0.006"'))

    with pytest.raises(InvalidPriceError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_price"


def test_float_price_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ вещественной цены без перевода в Decimal."""
    path = _write_text(tmp_path, _committed_text().replace('"0.006"', "0.006"))

    with pytest.raises(InvalidPriceError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_price"


def test_non_usd_currency_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ валюты, отличной от USD."""
    path = _write_text(
        tmp_path, _committed_text().replace("currency: USD", "currency: EUR")
    )

    with pytest.raises(InvalidCurrencyError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_currency"


def test_invalid_yaml_document_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ документа, который не является отображением."""
    path = _write_text(tmp_path, "- just-a-list\n")

    with pytest.raises(TariffError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_tariff"
    assert "just-a-list" not in str(captured.value)


def test_invalid_weekday_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ неизвестного дня пикового окна."""
    path = _write_text(tmp_path, _committed_text().replace("Monday", "Funday"))

    with pytest.raises(TariffError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_tariff"


def test_missing_tariff_file_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ отсутствующего файла без пути в сообщении."""
    path = tmp_path / "missing.yaml"

    with pytest.raises(TariffError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_tariff"
    assert str(path) not in str(captured.value)


def test_null_price_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ пустой цены, которая не является строкой или целым."""
    path = _write_text(tmp_path, _committed_text().replace('"0.006"', "null"))

    with pytest.raises(InvalidPriceError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_price"


def test_duplicate_model_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ повторного имени модели в одном файле."""
    text = _committed_text()
    block = text.split("models:\n", maxsplit=1)[1]
    path = _write_text(tmp_path, text + block)

    with pytest.raises(UnknownModelError) as captured:
        load_tariffs(path)

    assert captured.value.code == "unknown_model"


def test_integer_price_is_accepted(tmp_path: Path) -> None:
    """Проверяет приём целой цены без вещественного приближения."""
    path = _write_text(tmp_path, _committed_text().replace('"1.20"', "2"))
    catalog = load_tariffs(path)

    assert catalog.model("deepseek-flash").peak.output == Decimal(2)


def test_invalid_price_text_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ цены, которую нельзя прочитать как Decimal."""
    path = _write_text(tmp_path, _committed_text().replace('"0.006"', '"not-a-price"'))

    with pytest.raises(InvalidPriceError) as captured:
        load_tariffs(path)

    assert captured.value.code == "invalid_price"
    assert "not-a-price" not in str(captured.value)


def test_naive_datetime_uses_utc_peak_window() -> None:
    """Проверяет, что наивный момент читается как UTC."""
    catalog = load_tariffs(_TARIFF_PATH)
    aware_at = datetime(2026, 9, 14, 2, 0, tzinfo=UTC)
    naive = catalog.snapshot("deepseek-flash", aware_at.replace(tzinfo=None))
    aware = catalog.snapshot("deepseek-flash", aware_at)
    tokens = TokenCounts(cache_hit=0, cache_miss=1_000_000, output=0)

    assert calculate_cost(tokens, naive) == calculate_cost(tokens, aware)


def test_committed_yaml_has_no_secret_keys() -> None:
    """Проверяет отсутствие секретных ключей в файле тарифов."""
    loaded = yaml.safe_load(_TARIFF_PATH.read_text(encoding="utf-8"))
    _assert_no_secret_keys(loaded)


def test_readme_contains_source_url_and_verified_at() -> None:
    """Проверяет публикацию источника и даты проверки тарифа."""
    text = _README.read_text(encoding="utf-8")
    assert _SOURCE_URL in text
    assert _VERIFIED_AT in text
    assert "source_url" in text
    assert "verified_at" in text


def test_calculate_cost_uses_one_half_even_rounding() -> None:
    """Проверяет независимые нано-USD и одно округление HALF_EVEN."""
    million_miss = calculate_cost(
        TokenCounts(cache_hit=0, cache_miss=1_000_000, output=0),
        TariffSnapshot(
            model="deepseek-flash",
            cache_hit=Decimal("0.006"),
            cache_miss=Decimal("0.30"),
            output=Decimal("1.20"),
        ),
    )
    half_down = calculate_cost(
        TokenCounts(cache_hit=1, cache_miss=0, output=0),
        TariffSnapshot(
            model="probe",
            cache_hit=Decimal("0.0005"),
            cache_miss=Decimal(0),
            output=Decimal(0),
        ),
    )
    half_up = calculate_cost(
        TokenCounts(cache_hit=3, cache_miss=0, output=0),
        TariffSnapshot(
            model="probe",
            cache_hit=Decimal("0.0005"),
            cache_miss=Decimal(0),
            output=Decimal(0),
        ),
    )

    assert million_miss == 300_000_000
    assert half_down == 0
    assert half_up == 2


def test_peak_windows_select_monday_utc_prices() -> None:
    """Проверяет выбор пиковых и внепиковых цен по окнам UTC."""
    catalog = load_tariffs(_TARIFF_PATH)
    peak = catalog.snapshot("deepseek-flash", datetime(2026, 9, 14, 2, 0, tzinfo=UTC))
    off_peak = catalog.snapshot(
        "deepseek-flash", datetime(2026, 9, 14, 5, 0, tzinfo=UTC)
    )
    second_peak = catalog.snapshot(
        "deepseek-flash", datetime(2026, 9, 14, 7, 0, tzinfo=UTC)
    )
    weekend = catalog.snapshot(
        "deepseek-flash", datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
    )
    tokens = TokenCounts(cache_hit=0, cache_miss=1_000_000, output=0)

    assert calculate_cost(tokens, peak) == 300_000_000
    assert calculate_cost(tokens, off_peak) == 150_000_000
    assert calculate_cost(tokens, second_peak) == 300_000_000
    assert calculate_cost(tokens, weekend) == 150_000_000


def test_peak_snapshot_uses_english_weekdays_under_foreign_locale() -> None:
    """Проверяет пиковые цены UTC при неанглийской локали процесса."""
    catalog = load_tariffs(_TARIFF_PATH)
    peak_at = datetime(2026, 9, 14, 2, 0, tzinfo=UTC)
    previous = locale.setlocale(locale.LC_TIME)
    try:
        _activate_non_english_time_locale(peak_at)
        snapshot = catalog.snapshot("deepseek-flash", peak_at)
    finally:
        locale.setlocale(locale.LC_TIME, previous)

    assert snapshot.cache_miss == Decimal("0.30")


def _committed_text() -> str:
    """Возвращает текст зафиксированного файла тарифов."""
    return _TARIFF_PATH.read_text(encoding="utf-8")


def _write_text(tmp_path: Path, text: str) -> Path:
    """Записывает вариант тарифа во временный файл.

    Args:
        tmp_path: каталог pytest.
        text: содержимое YAML.

    Returns:
        Путь к временному файлу тарифов.
    """
    path = tmp_path / "model-tariffs.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _assert_no_secret_keys(payload: object) -> None:
    """Проверяет дерево YAML на секретные имена полей.

    Args:
        payload: узел после `yaml.safe_load`.
    """
    if type(payload) is dict:
        _assert_mapping_has_no_secrets(payload)
        return
    _assert_sequence_has_no_secrets(payload)


def _assert_mapping_has_no_secrets(payload: dict[object, object]) -> None:
    """Проверяет ключи одного отображения.

    Args:
        payload: отображение YAML.
    """
    assert _SECRET_KEYS.isdisjoint(payload)
    for value in payload.values():
        _assert_no_secret_keys(value)


def _assert_sequence_has_no_secrets(payload: object) -> None:
    """Проверяет элементы списка YAML.

    Args:
        payload: узел, который может быть списком.
    """
    if type(payload) is not list:
        return
    for item in payload:
        _assert_no_secret_keys(item)


def _activate_non_english_time_locale(sample: datetime) -> None:
    """Включает локаль, в которой имя дня не английское.

    Args:
        sample: момент, по которому проверяется имя дня.
    """
    for name in _NON_ENGLISH_TIME_LOCALES:
        if _switched_time_locale(name, sample):
            return


def _switched_time_locale(name: str, sample: datetime) -> bool:
    """Пробует одну локаль времени и проверяет имя дня.

    Args:
        name: кандидат `setlocale`.
        sample: момент, по которому читается `%A`.

    Returns:
        Признак, что локаль сменила английское имя дня.
    """
    try:
        locale.setlocale(locale.LC_TIME, name)
    except locale.Error:
        return False
    return sample.strftime("%A") != "Monday"
