#!/usr/bin/env python
"""Пересборка расчета долга территории с учетом групп и назначений."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

import pyodbc

BASE_DIR = Path(__file__).parent
GROUP_ASSIGNMENTS_FILE = BASE_DIR / "group_manager_assignments.json"
AREA_ASSIGNMENTS_FILE = BASE_DIR / "sales_area_group_assignments.json"

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)


class Manager(NamedTuple):
    id: int
    code: str
    name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Пересчитать долг выбранной территории с учетом групп."
    )
    parser.add_argument(
        "--area",
        default="101",
        help="Код территории (Sales Area) из TREES.fCODE, по умолчанию 101.",
    )
    parser.add_argument(
        "--groups",
        help="Принудительный список групп через запятую (например 002,036).",
    )
    parser.add_argument(
        "--no-area-groups",
        action="store_true",
        help="Игнорировать sales_area_group_assignments.json для подбора групп.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать детализацию по каждому менеджеру.",
    )
    return parser.parse_args()


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_groups(groups: Optional[Iterable[str]]) -> List[str]:
    if not groups:
        return []
    result: List[str] = []
    for group in groups:
        code = group.strip()
        if code and code not in result:
            result.append(code)
    return result


def build_manager_group_map(assignments: Dict[str, Sequence[int]]) -> Dict[int, List[str]]:
    manager_map: Dict[int, List[str]] = {}
    for group_code, manager_ids in assignments.items():
        ids = manager_ids if isinstance(manager_ids, list) else [manager_ids]
        for mgr_id in ids:
            manager_map.setdefault(int(mgr_id), []).append(group_code)
    return manager_map


def resolve_groups(
    manager_id: int,
    area_code: str,
    forced_groups: List[str],
    area_groups: Dict[str, List[str]],
    manager_groups: Dict[int, List[str]],
) -> List[str]:
    if forced_groups:
        return forced_groups
    if area_code in area_groups and area_groups[area_code]:
        return area_groups[area_code]
    return manager_groups.get(manager_id, [])


def fetch_area_managers(cursor, area_code: str) -> List[Manager]:
    cursor.execute(
        """
        SELECT DISTINCT ag.fID, ag.fCODE, ag.fNAME
        FROM SALESAGENTS ag
        INNER JOIN SALESAGENTAREAS sa ON sa.fSALESAGENTID = ag.fID
        WHERE sa.fSALESAREA = ? AND ag.fCLOSED = 0
        ORDER BY ag.fCODE
        """,
        (area_code,),
    )
    return [Manager(int(row.fID), row.fCODE.strip(), row.fNAME.strip()) for row in cursor.fetchall()]


def _group_clause(groups: Optional[Sequence[str]]) -> Tuple[str, Tuple[str, ...]]:
    if groups:
        placeholders = ",".join(["?"] * len(groups))
        return f" AND c.fGROUP IN ({placeholders})", tuple(groups)
    return "", tuple()


def calc_manager_debt(cursor, manager_id: int, groups: Optional[Sequence[str]]):
    group_clause, group_params = _group_clause(groups)

    debt_sql = f"""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE 0 END) as Debit,
            SUM(CASE WHEN d.fDBCR = 'C' THEN d.fSUM ELSE 0 END) as Credit
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        {group_clause}
    """
    cursor.execute(debt_sql, (manager_id, *group_params))
    row = cursor.fetchone()
    debit = float(row.Debit or 0)
    credit = float(row.Credit or 0)
    net_debt = debit - credit

    rest_sql = f"""
        SELECT 
            SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END) as Type01,
            SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END) as Type02
        FROM HIRESTCUSTOMERSSUM r
        INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        {group_clause}
    """
    cursor.execute(rest_sql, (manager_id, *group_params))
    rest_row = cursor.fetchone()
    type01 = float(rest_row.Type01 or 0)
    type02 = float(rest_row.Type02 or 0)

    debt = net_debt - abs(type01) - abs(type02)
    return {
        "manager_id": manager_id,
        "net_debt": net_debt,
        "type01": type01,
        "type02": type02,
        "debt": debt,
    }


def main():
    args = parse_args()
    forced_groups = normalize_groups(args.groups.split(",")) if args.groups else []

    area_assignments_raw = load_json(AREA_ASSIGNMENTS_FILE, {})
    area_groups = (
        {code: normalize_groups(groups) for code, groups in area_assignments_raw.items()}
        if not args.no_area_groups
        else {}
    )

    manager_assignments_raw = load_json(GROUP_ASSIGNMENTS_FILE, {})
    manager_groups = build_manager_group_map(manager_assignments_raw)

    conn = pyodbc.connect(CONN_STR)
    cursor = conn.cursor()

    managers = fetch_area_managers(cursor, args.area)
    if not managers:
        print(f"Территория {args.area} не имеет активных менеджеров.")
        return

    print("=" * 90)
    print(f"Расчет долга для территории {args.area}")
    print("=" * 90)

    grand_total = 0.0
    for manager in managers:
        effective_groups = resolve_groups(
            manager.id,
            args.area,
            forced_groups,
            area_groups,
            manager_groups,
        )
        info = calc_manager_debt(cursor, manager.id, effective_groups or None)
        grand_total += info["debt"]

        if args.verbose:
            group_label = ",".join(effective_groups) if effective_groups else "Все группы"
            print(
                f"{manager.code:<8} {manager.name:<30} | {group_label:<20} | "
                f"Долг: {info['debt']:>12,.2f} AMD"
            )

    print("-" * 90)
    print(f"ИТОГО долг: {grand_total:,.2f} AMD")

    conn.close()


if __name__ == "__main__":
    main()
