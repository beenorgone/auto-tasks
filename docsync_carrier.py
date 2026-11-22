"""Recognize GHTK payment statements before the watcher sends them to EIM."""

import csv
import io
from itertools import islice
import re
import unicodedata
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from docsync_intake import IntakeError


PAYMENT_NAME = re.compile(r'bien_ban_doi_soat_s\d+_[a-z0-9-]+_\d{9,11}_\d+\.(csv|xlsx)', re.IGNORECASE | re.ASCII)
PAYMENT_COLUMNS = {'ma don hang', 'ma don hang shop', 'trang thai don hang', 'ngay hoan thanh', 'tien cod'}
MAX_HEADER_ROWS = 96


def normalized(value):
    value = unicodedata.normalize('NFKD', value.casefold().replace('đ', 'd'))
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).split())


def has_payment_header(rows):
    for row in islice(rows, MAX_HEADER_ROWS):
        if row and normalized(row[0]) == 'stt' and PAYMENT_COLUMNS.issubset(map(normalized, row)):
            return True
    return False


def read_xml(archive, name, limit):
    member = archive.getinfo(name)
    if member.file_size > limit:
        raise ValueError('Workbook XML exceeds the review limit.')
    data = archive.read(member)
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise ValueError('Workbook declares external entities.')
    return ElementTree.fromstring(data)


def xlsx_has_payment_header(stream):
    with ZipFile(stream) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = read_xml(archive, 'xl/sharedStrings.xml', 12 * 1024 * 1024)
            strings = [''.join(node.text or '' for node in item.findall('.//{*}t')) for item in shared.findall('{*}si')]
        sheet = read_xml(archive, 'xl/worksheets/sheet1.xml', 16 * 1024 * 1024)
        rows = []
        for row in islice(sheet.iterfind('.//{*}row'), MAX_HEADER_ROWS):
            values = []
            for cell in row.findall('{*}c'):
                value = cell.findtext('{*}v') or ''
                if cell.get('t') == 's':
                    value = strings[int(value)]
                if cell.get('t') == 'inlineStr':
                    value = ''.join(node.text or '' for node in cell.findall('.//{*}t'))
                values.append(value)
            rows.append(values)
        return has_payment_header(rows)


def csv_has_payment_header(stream):
    prefix = stream.read(512 * 1024)
    encoding = 'utf-16' if prefix.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    text = prefix.decode(encoding, errors='replace')
    return any(has_payment_header(csv.reader(io.StringIO(text), delimiter=delimiter)) for delimiter in (',', ';', '\t'))


def validate_payment_statement(stream, suffix):
    """Require the GHTK settlement header in the bytes being uploaded."""
    try:
        checker = xlsx_has_payment_header if suffix == '.xlsx' else csv_has_payment_header
        if checker(stream):
            return
    except (BadZipFile, ElementTree.ParseError, KeyError, ValueError, IndexError, RuntimeError, NotImplementedError, csv.Error):
        pass
    raise IntakeError('Cần kiểm tra file. Biên bản thanh toán GHTK phải có tiêu đề Mã đơn hàng shop, Ngày hoàn thành và Tiền CoD.')
