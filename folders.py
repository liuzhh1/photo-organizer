"""Browse local and Windows SMB folders without native GUI dialogs."""
from __future__ import annotations

import ctypes
import json
import ntpath
import os
import re
import stat
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit


def normalize_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('文件夹路径格式无效')
    value = value.strip().strip('\"\'“”').strip()
    if not value or '\x00' in value:
        raise ValueError('请输入文件夹路径，例如 D:\\照片 或 \\\\NAS\\photos')
    if re.match(r'^(smb|file)://', value, re.IGNORECASE):
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            raise ValueError('请勿在路径中填写账号密码；请先在 Windows 资源管理器登录 NAS')
        value = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != 'localhost':
            value = '//' + parsed.netloc + value
        elif re.match(r'^/[a-zA-Z]:', value):
            value = value[1:]
    value = os.path.expandvars(os.path.expanduser(value))
    # ntpath also makes UNC normalization independently testable on other platforms.
    if os.name == 'nt' or value.startswith(('\\\\', '//')):
        network = value.startswith(('\\\\', '//'))
        value = ntpath.normpath(value.replace('/', '\\'))
        # Python 3.10 collapses a bare UNC server (\\server) to one leading slash.
        if network:
            value = '\\\\' + value.lstrip('\\')
        if re.fullmatch(r'[a-zA-Z]:', value):
            value += '\\'
    else:
        value = os.path.normpath(value)
    return value


def folder_error(exc: OSError, path: str) -> ValueError:
    network = path.startswith(('\\\\', '//'))
    code = getattr(exc, 'winerror', None)
    if isinstance(exc, PermissionError) or code in {5, 65, 86, 1219, 1326}:
        hint = ('请先在 Windows 资源管理器打开此共享并登录 NAS，'
                '再使用同一 Windows 用户启动拾光。' if network else '请检查当前 Windows 用户的文件夹访问权限。')
        return ValueError(f'没有权限访问此文件夹：{path}。{hint}')
    if network and (isinstance(exc, FileNotFoundError) or code in {53, 64, 67, 121, 1231}):
        return ValueError(f'无法连接 NAS 文件夹：{path}。请检查网络、共享名称，以及资源管理器是否能打开该位置。')
    if isinstance(exc, FileNotFoundError):
        return ValueError(f'文件夹不存在：{path}')
    return ValueError(f'无法读取文件夹 {path}：{exc}')


def require_directory(value: str) -> Path:
    normalized = normalize_path(value)
    candidate = Path(normalized)
    try:
        if not stat.S_ISDIR(candidate.stat().st_mode):
            raise ValueError(f'此位置不是文件夹：{normalized}')
        return candidate.resolve()
    except OSError as exc:
        raise folder_error(exc, normalized) from exc


def windows_shares(server: str) -> list[dict]:
    """List the current Windows user's accessible disk-share names, without credentials."""
    from ctypes import wintypes

    class SHARE_INFO_1(ctypes.Structure):
        _fields_ = [('name', wintypes.LPWSTR), ('type', wintypes.DWORD), ('remark', wintypes.LPWSTR)]

    api = ctypes.WinDLL('Netapi32')
    enum = api.NetShareEnum
    enum.argtypes = [wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    enum.restype = wintypes.DWORD
    api.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    api.NetApiBufferFree.restype = wintypes.DWORD
    resume = wintypes.DWORD(0)
    result = []
    while True:
        buffer = ctypes.c_void_p()
        count, total = wintypes.DWORD(), wintypes.DWORD()
        code = enum(server, 1, ctypes.byref(buffer), 0xffffffff,
                    ctypes.byref(count), ctypes.byref(total), ctypes.byref(resume))
        try:
            if code not in {0, 234}:
                raise folder_error(ctypes.WinError(code), server)
            entries = ctypes.cast(buffer, ctypes.POINTER(SHARE_INFO_1))
            for index in range(count.value):
                entry = entries[index]
                if entry.type & 0xffff == 0 and not entry.name.endswith('$'):
                    result.append({'name': entry.name, 'path': server + '\\' + entry.name})
        finally:
            if buffer:
                api.NetApiBufferFree(buffer)
        if code != 234:
            return sorted(result, key=lambda item: item['name'].casefold())


class FolderBrowser:
    def __init__(self, data: Path):
        self.history_file = data / 'locations.json'
        self.lock = threading.Lock()

    def recent(self):
        try:
            data = json.loads(self.history_file.read_text('utf-8'))
            return [path for path in data if isinstance(path, str)][:12] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def locations(self):
        result = [{'name': '用户文件夹', 'path': str(Path.home()), 'kind': 'home'}]
        if os.name == 'nt':
            drives = ctypes.windll.kernel32.GetLogicalDrives()
            for index in range(26):
                if drives & (1 << index):
                    drive = chr(65 + index) + ':\\'
                    remote = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)) == 4
                    result.append({'name': f'{drive[:2]} {"网络驱动器" if remote else "本地驱动器"}',
                                   'path': drive, 'kind': 'network' if remote else 'drive'})
        else:
            result.append({'name': '文件系统', 'path': '/', 'kind': 'drive'})
        result.extend({'name': path, 'path': path, 'kind': 'recent'} for path in self.recent())
        return result

    def remember(self, value):
        path = str(require_directory(value))
        # Check actual directory enumeration, not just existence.
        try:
            with os.scandir(path) as entries:
                next(entries, None)
        except OSError as exc:
            raise folder_error(exc, path) from exc
        with self.lock:
            recent = [path] + [old for old in self.recent() if os.path.normcase(old) != os.path.normcase(path)]
            temporary = self.history_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(recent[:12], ensure_ascii=False), 'utf-8')
            temporary.replace(self.history_file)
        return {'folder': path}

    def browse(self, value='', query='', offset=0, limit=200):
        offset = max(0, int(offset))
        limit = max(1, min(500, int(limit)))
        if not value:
            return {'path': '', 'parent': None, 'folders': [], 'locations': self.locations(),
                    'total': 0, 'offset': 0, 'limit': limit, 'selectable': False}
        normalized = normalize_path(value)
        server = re.fullmatch(r'\\\\[^\\]+\\?', normalized)
        if server:
            if os.name != 'nt':
                raise ValueError('此系统需要先挂载 SMB 共享，或填写完整共享路径')
            current = normalized.rstrip('\\')
            folders = windows_shares(current)
            parent, selectable = '', False
        else:
            current_path = require_directory(normalized)
            current = str(current_path)
            folders = []
            try:
                with os.scandir(current_path) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=True) and not entry.name.startswith('.'):
                                folders.append({'name': entry.name, 'path': str(current_path / entry.name)})
                        except OSError:
                            continue
            except OSError as exc:
                raise folder_error(exc, current) from exc
            if current.startswith('\\\\'):
                parts = current.strip('\\').split('\\')
                parent = '\\\\' + parts[0] if len(parts) == 2 else str(current_path.parent)
            else:
                parent = str(current_path.parent) if current_path.parent != current_path else ''
            selectable = True
        if query:
            folders = [entry for entry in folders if str(query).casefold() in entry['name'].casefold()]
        folders.sort(key=lambda entry: entry['name'].casefold())
        return {'path': current, 'parent': parent, 'folders': folders[offset:offset + limit],
                'locations': self.locations(), 'total': len(folders), 'offset': offset,
                'limit': limit, 'selectable': selectable}
