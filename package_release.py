"""Create and verify a ZIP with the local model, Python dependencies and catalog."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import urllib.error
import urllib.request
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    try:
        urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2).close()
    except urllib.error.URLError:
        pass
    else:
        raise RuntimeError('请先停止本机服务，再打包素材库')
    output = root.parent / 'releases'
    output.mkdir(exist_ok=True)
    destination = output / f'FramePhoto-0.1.0-Windows-{datetime.now():%Y%m%d-%H%M%S}.zip'
    files = [file for file in root.iterdir() if file.is_file() and file.name != '.gitignore']
    for folder in ('web', 'tests', '.vendor-py310'):
        files.extend(file for file in (root / folder).rglob('*') if file.is_file()
                     and '__pycache__' not in file.parts and file.suffix not in {'.pyc', '.pyo'})
    model = root / '.models' / 'siglip2-base-patch16-224'
    files.extend(file for file in model.iterdir() if file.is_file())
    thumbs = root / '.data' / 'thumbs'
    files.extend(file for file in thumbs.glob('*.jpg') if file.is_file())
    location_file = root / '.data' / 'locations.json'
    if location_file.exists():
        files.append(location_file)
    manifest = []
    prefix = 'photo-organizer/'
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / 'catalog.sqlite3'
        with closing(sqlite3.connect(root / '.data' / 'catalog.sqlite3')) as source:
            with closing(sqlite3.connect(database)) as target:
                source.backup(target)
                assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                photos = target.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
        config = json.loads((root / '.data' / 'config.json').read_text('utf-8'))
        config['model_path'] = '.models/siglip2-base-patch16-224'
        instructions = f'''拾光 FRAME v0.1.0 · Windows 打包版

解压整个 photo-organizer 文件夹后，双击“启动拾光.cmd”。
默认复用当前电脑已有的 Conda 环境 matanyone2（Python 3.10 / PyTorch 2.10 CUDA）。
本包不是独立 EXE，不包含 Conda 环境、PyTorch 或 NVIDIA 驱动。
其他电脑需安装可用的同类 Conda 环境；可在 start.ps1 参数中指定环境名称。

包含：源代码、网页界面、正式 SigLIP 2 模型、Python 3.10 项目局部依赖、
当前素材库（{photos} 条记录）、缩略图、分类向量和人工标记。
NAS 原始照片未打包。数据库仍引用原 NAS 路径，访问时需能连接原共享。
包内含私人照片缩略图与目录信息，请按个人素材备份保管。

双击启动后访问 http://127.0.0.1:8765/ 。
停止：在解压目录运行 powershell -ExecutionPolicy Bypass -File .\\stop.ps1
模型配置在包内使用相对路径，无需重新下载模型。
未包含下载缓存、临时分段、测试生成的素材、旧版本依赖、日志和进程 PID。
详细说明见 README.md；SHA-256 清单见 PACKAGE-MANIFEST.json。
'''
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=3, allowZip64=True) as archive:
            for index, file in enumerate(sorted(files)):
                relative = file.relative_to(root).as_posix()
                with file.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                archive.write(file, prefix + relative,
                              compress_type=zipfile.ZIP_STORED if file.suffix in {'.safetensors', '.jpg'} else zipfile.ZIP_DEFLATED)
                manifest.append({'path': relative, 'bytes': file.stat().st_size, 'sha256': digest})
                if index % 250 == 0:
                    print(f'packaged {index + 1}/{len(files)}', flush=True)
            archive.write(database, prefix + '.data/catalog.sqlite3')
            archive.writestr(prefix + '.data/config.json', json.dumps(config, ensure_ascii=False, indent=2))
            archive.writestr(prefix + '打包版使用说明.txt', instructions)
            archive.writestr(prefix + 'PACKAGE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        print('Verifying ZIP CRC and bundled catalog/model entries...', flush=True)
        with zipfile.ZipFile(destination) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise RuntimeError('ZIP verification failed: ' + bad_file)
            names = set(archive.namelist())
            for required in ('启动拾光.cmd', 'start.ps1', 'app.py', '.data/catalog.sqlite3',
                             '.models/siglip2-base-patch16-224/model.safetensors'):
                assert prefix + required in names
            assert not any('/.cache/' in name or '/qa/' in name or '/__pycache__/' in name for name in names)
        with destination.open('rb') as stream:
            archive_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        checksum = destination.with_suffix('.zip.sha256')
        checksum.write_text(f'{archive_digest}  {destination.name}\n', encoding='utf-8')
        summary = {'archive': str(destination), 'bytes': destination.stat().st_size,
                   'sha256': archive_digest, 'photos': photos, 'files': len(names), 'verified': True}
        destination.with_suffix('.zip.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
