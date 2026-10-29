"""Shared intake root and year/month archive paths."""
import os
import re
from pathlib import Path


def work_root(home=None):
    return Path(os.environ.get('DOCSYNC_ROOT') or
                Path(home or Path.home()) / 'Documents' / 'WIP & Reference').expanduser().absolute()


def month_root(root, year, month):
    year, month = str(year), str(month).zfill(2)
    if not re.fullmatch(r'\d{4}', year) or month not in {f'{n:02d}' for n in range(1, 13)}:
        raise ValueError('Expected a four-digit year and month 01–12')
    return Path(root) / year / f'{year}-{month}'


def archive_relative(relative, year):
    """Remap only an old top-level month, preserving the remaining spelling."""
    first, separator, rest = str(relative).partition('/')
    if re.fullmatch(r'0[1-9]|1[0-2]', first):
        return month_root(Path('.'), year, first).as_posix() + (separator + rest if separator else '')
    return str(relative)
