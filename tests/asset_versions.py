"""Проверка обновления кеша допускает более новые версии того же ресурса."""
import re


def assert_minimum_asset_version(template: str, filename: str, minimum: int) -> None:
    version = re.search(re.escape(filename) + r"'\) }}\?v=(\d+)", template)
    assert version and int(version.group(1)) >= minimum, filename
