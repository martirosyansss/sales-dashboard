"""Допуск конкретных машин к магазину; правила действуют во все дни развоза."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection


@dataclass(frozen=True)
class VehicleAccess:
    mode: str
    trucks: tuple[str, ...]

    def allows(self, code: str) -> bool:
        return (code in self.trucks) if self.mode == 'allow' else (code not in self.trucks)

    def to_json(self) -> dict[str, Any]:
        return {'mode': self.mode, 'trucks': list(self.trucks)}


def check_access(raw: Any, known_trucks: Collection[str] | None = None) -> tuple[VehicleAccess | None, str | None]:
    """null снимает ограничение. Пустой список разрешённых запрещает обслуживание всеми машинами."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict) or set(raw) != {'mode', 'trucks'}:
        return None, 'Սպասվում էր կանոն՝ mode և trucks դաշտերով'
    mode, trucks = raw['mode'], raw['trucks']
    if mode not in ('allow', 'deny'):
        return None, 'Ռեժիմ՝ allow (միայն ընտրվածները) կամ deny (բոլորը, բացի ընտրվածներից)'
    if not isinstance(trucks, list) or len(trucks) > 500 or any(
            not isinstance(code, str) or not code.strip() or len(code) > 80 or code != code.strip()
            for code in trucks):
        return None, 'Մեքենաներ՝ մեքենաների ոչ դատարկ կոդերի ցուցակ'
    if known_trucks is not None and any(code not in known_trucks for code in trucks):
        return None, 'Մեքենան չի գտնվել ցուցակում — թարմացրեք էջը'
    return VehicleAccess(mode, tuple(sorted(set(trucks)))), None
