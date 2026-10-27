# -*- coding: utf-8 -*-
"""«Excel ERP-ի համար» (владелец 13.10): առաքիչ и машина заказов дня из отправленного плана — в официальный шаблон
ArmSoft импорта заказов (SalesOrder.xlsm, лист «Խմբագրում»). Сайт в ERP НЕ пишет: оператор открывает файл в Excel на
компьютере с надстройкой «AS-Sales Management» и нажимает кнопку листа (макрос ArmSoft EditDocument) — правку делает
сама ERP, со своими проверками и историей.

Что кладётся (build_xlsm, чистая функция байты → байты):
- лист «Խմբագրում», строки с 3-й: по строке на заказ (ImportRow). Изменяются только Առաքիչ (F) и Առաքող ավտոմեքենա (G);
  остальные поля шапки заказа — ровно текущие значения ERP (erp.order_heads). Макрос ArmSoft отправляет адрес доставки
  (AC) всегда, и пустым: в ERP хранится id, шаблон ждёт код — заказ с адресом в файл не попадает (views, has_address);
- файл — снимок: загруженный позже вернёт в заказ значения шапки на момент скачивания (имя файла — со временем);
- лист «Կարգավորումներ»: пересчёт цен, скидок, тары и подарков выключен (C6:C9 и сами флажки) — правка шапки не должна
  пересчитывать строки товаров и 10+1;
- строк товаров нет: таблицы шаблона в режиме «Ավելացնել տողերը» (пустая ничего не добавляет); режим «Վերարտագրել
  տողերը» с пустой таблицей стёр бы строки заказа — такой шаблон не принимается (_check_add_mode).
Остальное в файле (макросы, кнопки, справка) — байт-в-байт как в шаблоне: XML правится строкой, без перепаковки
библиотекой (openpyxl теряет кнопки форм).
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from xml.sax.saxutils import escape

TEMPLATE = Path(__file__).resolve().parent.parent / 'templates' / 'erp' / 'SalesOrder.xlsm'
EDIT_SHEET = 'Խմբագրում'
SETTINGS_SHEET = 'Կարգավորումներ'
FIRST_ROW = 3                       # строки 1–2 — заголовки шаблона
SETTINGS_OFF = ('C6', 'C7', 'C8', 'C9')   # пересчёт цен, скидок, тары, подарков
GRID_MODES = ('B13', 'B14', 'B15', 'B16')  # режим таблиц товаров, подарков, «ի պահ», обмена: A1 «добавить», A2 «перезаписать»
TEXT_STYLE = 4                      # формат «@» шаблона
DATE_STYLE = 69                     # формат даты шаблона (numFmt 14)
EXCEL_EPOCH = date(1899, 12, 30)
_BAD_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')


class TemplateError(ValueError):
    """Шаблон не того вида (нет листа, ячейки настроек, таблицы строк) — файл не собирается."""


@dataclass(frozen=True)
class ImportRow:
    """Строка листа «Խմբագրում»: заказ doc_num, առաքիչ courier и машина car — новые, остальное — как сейчас в ERP."""
    doc_num: str
    day: date
    agent: str
    customer: str
    courier: str
    car: str = ''
    sales_area: str = ''
    comment: str = ''
    price_type: str = ''
    tax_number: str = ''
    submit_date: date | None = None
    cosign_date: date | None = None
    assignor: str = ''
    account: str = ''
    doc_group: str = ''
    delay_days: int | None = None
    pay_type: str = ''
    priority: int | None = None
    contact: str = ''
    create_method: str = ''
    division: str = ''
    send_status: str = ''
    delivery_date: date | None = None


# столбец листа «Խմբագրում» → (поле ImportRow, вид: t — текст, d — дата, n — число)
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ('A', 'doc_num', 't'), ('B', 'day', 'd'), ('C', 'agent', 't'), ('D', 'sales_area', 't'), ('E', 'customer', 't'),
    ('F', 'courier', 't'), ('G', 'car', 't'), ('H', 'delivery_date', 'd'), ('I', 'comment', 't'), ('J', 'price_type', 't'),
    ('AD', 'tax_number', 't'), ('AE', 'submit_date', 'd'), ('AF', 'cosign_date', 'd'), ('AG', 'assignor', 't'),
    ('AH', 'account', 't'), ('AI', 'doc_group', 't'), ('AJ', 'delay_days', 'n'), ('AK', 'pay_type', 't'),
    ('AL', 'priority', 'n'), ('AM', 'contact', 't'), ('AN', 'create_method', 't'), ('AO', 'division', 't'),
    ('AP', 'send_status', 't'),
)


def _text(value: str) -> str:
    """Текст ячейки: XML-экранирование; «_x» и \r — экранированием Excel (_xHHHH_), иначе Excel раскодирует «_x0041_» в
    «A», а \r\n в XML станет \n — комментарий заказа изменился бы."""
    return escape(_BAD_XML.sub('', value).replace('_x', '_x005F_x').replace('\r', '_x000D_'))


def _cell(ref: str, kind: str, value: object) -> str:
    if value is None or value == '':
        return ''
    if kind == 'd':
        assert isinstance(value, date)
        return f'<c r="{ref}" s="{DATE_STYLE}"><v>{(value - EXCEL_EPOCH).days}</v></c>'
    if kind == 'n':
        return f'<c r="{ref}"><v>{int(value)}</v></c>'   # type: ignore[call-overload]
    return f'<c r="{ref}" s="{TEXT_STYLE}" t="inlineStr"><is><t xml:space="preserve">{_text(str(value))}</t></is></c>'


def rows_xml(rows: Sequence[ImportRow]) -> str:
    out = []
    for i, row in enumerate(rows, FIRST_ROW):
        cells = ''.join(_cell(f'{col}{i}', kind, getattr(row, field)) for col, field, kind in COLUMNS)
        out.append(f'<row r="{i}">{cells}</row>')
    return ''.join(out)


def _sheet_paths(files: dict[str, bytes]) -> dict[str, str]:
    """Имя листа → путь его XML в архиве (workbook.xml + его rels)."""
    book = files['xl/workbook.xml'].decode('utf-8')
    rels = files['xl/_rels/workbook.xml.rels'].decode('utf-8')
    targets = {m.group(1): m.group(2) for m in re.finditer(r'<Relationship [^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels)}
    targets.update({m.group(2): m.group(1)
                    for m in re.finditer(r'<Relationship [^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
    out = {}
    for m in re.finditer(r'<sheet [^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"', book):
        target = targets.get(m.group(2), '')
        out[m.group(1)] = target.lstrip('/') if target.startswith('/') else 'xl/' + target
    return out


def _sheet_rels(files: dict[str, bytes], sheet: str) -> list[str]:
    """Пути файлов, на которые ссылается лист (флажки, VML)."""
    folder, name = sheet.rsplit('/', 1)
    rels = files.get(f'{folder}/_rels/{name}.rels', b'').decode('utf-8')
    return ['xl/' + t.replace('../', '') for t in re.findall(r'Target="([^"]+)"', rels)]


def _fill_edit_sheet(xml: str, rows: Sequence[ImportRow]) -> str:
    m = re.search(r'(<sheetData>)(.*?)(</sheetData>)', xml, re.S)
    if m is None:
        raise TemplateError('нет таблицы строк листа «Խմբագրում»')
    head = ''.join(re.findall(r'<row r="[12]"[ >].*?</row>', m.group(2), re.S))
    if head.count('<row ') != 2:
        raise TemplateError('нет строк заголовка листа «Խմբագրում»')
    xml = xml[:m.start()] + m.group(1) + head + rows_xml(rows) + m.group(3) + xml[m.end():]
    last = max(FIRST_ROW + len(rows) - 1, 2)
    return re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:AR{last}"/>', xml, count=1)


def _shared(xml: str, ref: str) -> str | None:
    m = re.search(rf'<c r="{ref}"[^>]*t="s"[^>]*><v>(\d+)</v>', xml)
    return m.group(1) if m else None


def _check_add_mode(xml: str) -> None:
    """Все таблицы строк — «Ավելացնել տողերը» (то же, что A1, не A2); иначе — TemplateError."""
    add, rewrite = _shared(xml, 'A1'), _shared(xml, 'A2')
    if add is None or rewrite is None or add == rewrite or any(_shared(xml, ref) != add for ref in GRID_MODES):
        raise TemplateError('таблицы строк шаблона не в режиме «Ավելացնել տողերը»')


def _settings_off(xml: str) -> str:
    _check_add_mode(xml)
    for ref in SETTINGS_OFF:
        xml, n = re.subn(rf'(<c r="{ref}"[^>]*t="b"[^>]*>)<v>[01]</v>', r'\g<1><v>0</v>', xml)
        if n != 1:
            raise TemplateError(f'нет флажка {ref} листа «Կարգավորումներ»')
    return xml


def build_xlsm(template: bytes, rows: Sequence[ImportRow]) -> bytes:
    """Шаблон ArmSoft + строки заказов → файл .xlsm (тот же архив, изменены только три вида частей: лист правки, лист
    настроек, флажки настроек)."""
    with zipfile.ZipFile(io.BytesIO(template)) as src:
        infos = src.infolist()
        files = {i.filename: src.read(i) for i in infos}
    sheets = _sheet_paths(files)
    if EDIT_SHEET not in sheets or SETTINGS_SHEET not in sheets:
        raise TemplateError('в шаблоне нет листов «Խմբագրում» / «Կարգավորումներ»')
    changed = {
        sheets[EDIT_SHEET]: _fill_edit_sheet(files[sheets[EDIT_SHEET]].decode('utf-8'), rows),
        sheets[SETTINGS_SHEET]: _settings_off(files[sheets[SETTINGS_SHEET]].decode('utf-8')),
    }
    for part in _sheet_rels(files, sheets[SETTINGS_SHEET]):
        text = files.get(part, b'').decode('utf-8', 'replace')
        if part.startswith('xl/ctrlProps/') and 'objectType="CheckBox"' in text:
            changed[part] = text.replace(' checked="Checked"', '')
        elif part.endswith('.vml'):
            changed[part] = re.sub(r'\s*<x:Checked>1</x:Checked>', '', files[part].decode('utf-8'))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as dst:
        for info in infos:
            data = changed[info.filename].encode('utf-8') if info.filename in changed else files[info.filename]
            dst.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def file_name(day: date, made: datetime) -> str:
    """Имя файла: день плана и время скачивания — старый файл видно по имени."""
    return f'ERP-araqich-{day.isoformat()}-{made:%H%M}.xlsm'
