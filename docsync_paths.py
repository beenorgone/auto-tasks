"""Shared intake root and year/month archive paths."""
import json
import os
import re
import shlex
from pathlib import Path
from tempfile import NamedTemporaryFile


OPTIONAL_SCAN_PATHS = ('DOCSYNC_DOCUMENTS_SCAN', 'DOCSYNC_DOWNLOADS_SCAN')


def workspace_defaults(home=None):
    """Default locations shared by the web app, watcher, CLI and organizer."""
    documents = Path(home or Path.home()) / 'Documents'
    return {
        'DOCSYNC_ROOT': str(documents / 'WIP & Reference'),
        'IVAR_DATA': str(documents / 'Drives/gDriveProjects/Working/Projects/IVAR/Accounting - Ke toan/Data - Du lieu'),
        'DOCSYNC_DOCUMENTS_SCAN': str(documents),
        'DOCSYNC_DOWNLOADS_SCAN': str(documents.parent / 'Downloads'),
    }


def workspace_config(home=None):
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path(home or Path.home()) / '.config')
    return config / 'docsync' / 'folders.json'


def saved_paths(home=None):
    path = workspace_config(home)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except ValueError as error:
        raise OSError('File cấu hình thư mục DocSync không hợp lệ.') from error
    keys = workspace_defaults(home)
    if not isinstance(data, dict) or any(k not in keys or not isinstance(v, str) for k, v in data.items()):
        raise OSError('File cấu hình thư mục DocSync không hợp lệ.')
    return data


def workspace_paths(home=None):
    paths = workspace_defaults(home)
    paths.update(saved_paths(home))
    for key in paths:
        if key in os.environ and (os.environ[key] or key in OPTIONAL_SCAN_PATHS):
            paths[key] = os.environ[key]
        if paths[key]:
            paths[key] = str(Path(paths[key]).expanduser().absolute())
    return paths


def settings_payload():
    return {
        'paths': workspace_paths(),
        'overridden': [key for key in workspace_defaults() if key in os.environ and (os.environ[key] or key in OPTIONAL_SCAN_PATHS)],
    }


def save_workspace_paths(values):
    """Validate the routing boundaries, then atomically save the location choices."""
    current = settings_payload()
    if not isinstance(values, dict) or values.keys() != current['paths'].keys():
        raise ValueError('Nhập đủ các trường thư mục.')
    overrides = set(current['overridden'])
    paths = {}
    for key, raw in values.items():
        if not isinstance(raw, str) or len(raw) > 4096 or any(ord(char) < 32 for char in raw):
            raise ValueError('Đường dẫn thư mục không hợp lệ.')
        if key in overrides and raw != current['paths'][key]:
            raise ValueError('Thư mục do môi trường chạy cấu hình không thể đổi tại đây.')
        if key in OPTIONAL_SCAN_PATHS and not raw.strip():
            paths[key] = ''
            continue
        try:
            path = Path(raw.strip()).expanduser()
            if not path.is_absolute() or not path.is_dir() or path.resolve() == Path('/'):
                raise ValueError('Chọn đường dẫn tuyệt đối tới một thư mục đang tồn tại.')
            paths[key] = str(path.resolve())
        except RuntimeError as error:
            raise ValueError('Không đọc được đường dẫn thư mục.') from error

    work, accounting = (Path(paths[key]) for key in ('DOCSYNC_ROOT', 'IVAR_DATA'))
    if work.is_relative_to(accounting) or accounting.is_relative_to(work):
        raise ValueError('Thư mục WIP và thư mục đích phải tách riêng.')
    for key in OPTIONAL_SCAN_PATHS:
        if paths[key] and Path(paths[key]).is_relative_to(accounting):
            raise ValueError('Không dùng thư mục đích hoặc thư mục con của đích làm nguồn quét.')

    config = workspace_config()
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths_to_save = saved_paths()
    paths_to_save.update({key: value for key, value in paths.items() if key not in overrides})
    with NamedTemporaryFile('w', encoding='utf-8', dir=config.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(paths_to_save, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            temporary.replace(config)
        finally:
            temporary.unlink(missing_ok=True)


def state_directory(home=None):
    return Path(os.environ.get('STATE_DIR') or Path(home or Path.home()) / 'Documents/.auto-tasks/ivar-documents-organizer')


def work_root(home=None):
    return Path(workspace_paths(home)['DOCSYNC_ROOT'])


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


if __name__ == '__main__':
    for key, value in {**workspace_paths(), 'STATE_DIR': str(state_directory())}.items():
        print(f'{key}={shlex.quote(value)}')
