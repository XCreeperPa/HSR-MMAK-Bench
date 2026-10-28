"""A very small, dependency free XLSX writer.

The official statistics export has to be an XLSX workbook, and the deployment
host has no spreadsheet library, so this module writes the workbook directly
as a zip of XML parts. It supports what the export needs: several sheets, a
bold header row, text cells that keep leading zeros (the six digit codes), and
numbers.

Cell values are written as inline strings or as plain numbers, which keeps the
file valid for Excel, LibreOffice and ``openpyxl`` alike.
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from typing import Sequence

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheets}
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
</styleSheet>"""


def _escape(value: str) -> str:
    return (
        value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def _column_name(index: int) -> str:
    name = ''
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell(row_index: int, column_index: int, value: object, style: int) -> str:
    reference = f'{_column_name(column_index)}{row_index}'
    style_attr = f' s="{style}"' if style else ''
    if value is None:
        return f'<c r="{reference}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{reference}"{style_attr} t="inlineStr"><is><t>{"TRUE" if value else "FALSE"}</t></is></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
    text = _escape(str(value))
    preserve = ' xml:space="preserve"' if text != text.strip() else ''
    return f'<c r="{reference}"{style_attr} t="inlineStr"><is><t{preserve}>{text}</t></is></c>'


def _sheet_xml(rows: Sequence[Sequence[object]], header: bool = True) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for row_index, row in enumerate(rows):
        style = 1 if (header and row_index == 0) else 0
        cells = ''.join(_cell(row_index + 1, col, value, style) for col, value in enumerate(row))
        parts.append(f'<row r="{row_index + 1}">{cells}</row>')
    parts.append('</sheetData></worksheet>')
    return ''.join(parts)


def build_workbook(sheets: Sequence[tuple[str, Sequence[Sequence[object]]]]) -> bytes:
    """Return the bytes of an XLSX workbook.

    ``sheets`` is a sequence of ``(sheet name, rows)`` pairs. The first row of
    each sheet is written as a bold header.
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        overrides = ''.join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        archive.writestr('[Content_Types].xml', CONTENT_TYPES.format(sheets=overrides))
        archive.writestr('_rels/.rels', ROOT_RELS)

        sheet_entries = ''.join(
            f'<sheet name="{_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, (name, _) in enumerate(sheets, start=1)
        )
        archive.writestr(
            'xl/workbook.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheet_entries}</sheets></workbook>',
        )

        rels = ''.join(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        rels += (
            f'<Relationship Id="rId{len(sheets) + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        archive.writestr(
            'xl/_rels/workbook.xml.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{rels}</Relationships>',
        )
        archive.writestr('xl/styles.xml', STYLES)

        for index, (_, rows) in enumerate(sheets, start=1):
            archive.writestr(f'xl/worksheets/sheet{index}.xml', _sheet_xml(rows))
    return buffer.getvalue()
