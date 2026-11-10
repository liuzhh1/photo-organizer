"""Persistent catalog, background tasks, copy-only export, and local inference."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

import numpy as np

from imaging import SUPPORTED, analyze, duplicate_groups, file_hash, open_image
from model import DEFAULT_CATEGORIES, LocalModel
from folders import require_directory

DEFAULT_CONFIG = {'model_path': '', 'device': 'auto', 'categories': DEFAULT_CATEGORIES,
                  'duplicate_threshold': 6, 'quality_threshold': 65}


class Cancelled(Exception):
    pass


class Library:
    def __init__(self, data: Path):
        self.data = data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / 'thumbs').mkdir(exist_ok=True)
        self.db_path = self.data / 'catalog.sqlite3'
        self.config_path = self.data / 'config.json'
        self.config = json.loads(self.config_path.read_text('utf-8')) if self.config_path.exists() else dict(DEFAULT_CONFIG)
        self.model = LocalModel()
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.job = {'id': '', 'running': False, 'type': '', 'done': 0, 'total': 0,
                    'message': '准备就绪', 'errors': [], 'result': None}
        with closing(self.connect()) as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assets (
                  id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                  root TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                  width INTEGER, height INTEGER, sha256 TEXT, phash TEXT,
                  sharpness REAL, brightness REAL, contrast REAL, dark_ratio REAL,
                  bright_ratio REAL, quality INTEGER, flags TEXT, category TEXT,
                  similarity REAL, embedding BLOB, model_key TEXT,
                  duplicate_group TEXT, duplicate_kind TEXT,
                  review TEXT NOT NULL DEFAULT 'unreviewed', imported_at REAL);
                CREATE INDEX IF NOT EXISTS idx_dup ON assets(duplicate_group);
                CREATE TABLE IF NOT EXISTS exports (path TEXT PRIMARY KEY);
            ''')

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def get_job(self):
        with self.lock:
            return dict(self.job, errors=list(self.job['errors']))

    def update(self, **kwargs):
        with self.lock:
            self.job.update(kwargs)

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def error(self, message):
        with self.lock:
            self.job['errors'].append(str(message))
            self.job['errors'] = self.job['errors'][-100:]

    def start(self, kind, operation):
        with self.lock:
            if self.job['running']:
                raise ValueError('已有任务正在运行，请等待完成或先取消')
            self.cancel_event.clear()
            self.job = {'id': uuid.uuid4().hex, 'running': True, 'type': kind, 'done': 0, 'total': 0,
                        'message': '正在准备…', 'errors': [], 'result': None}

        def run():
            try:
                result = operation()
                self.update(message='任务完成' if not self.get_job()['errors'] else '任务完成，部分文件需要检查', result=result)
            except Cancelled:
                if kind == 'scan':
                    self.regroup()
                self.update(message='任务已取消，已完成的结果已保存')
            except Exception as exc:
                logging.exception('Background task failed: %s', kind)
                self.error(str(exc))
                self.update(message='任务未完成，请查看详情')
            finally:
                self.update(running=False)
        threading.Thread(target=run, daemon=True).start()
        return self.get_job()

    def save_config(self, payload):
        with self.lock:
            if self.job['running']:
                raise ValueError('任务运行中，请在完成后修改设置')
            config = {key: payload.get(key, self.config[key]) for key in DEFAULT_CONFIG}
            if config['device'] not in {'auto', 'cpu', 'cuda'}:
                raise ValueError('无效的推理设备')
            config['duplicate_threshold'] = int(config['duplicate_threshold'])
            config['quality_threshold'] = int(config['quality_threshold'])
            if not 0 <= config['duplicate_threshold'] <= 12 or not 0 <= config['quality_threshold'] <= 100:
                raise ValueError('筛选阈值超出范围')
            if not isinstance(config['categories'], list) or not 1 <= len(config['categories']) <= 50:
                raise ValueError('请设置 1–50 个分类')
            names = set()
            for category in config['categories']:
                if not isinstance(category, dict):
                    raise ValueError('分类格式错误')
                for key in ('name', 'prompt'):
                    if not isinstance(category.get(key), str) or not 1 <= len(category[key].strip()) <= 500:
                        raise ValueError('分类名称和描述不能为空，且应少于 500 字')
                    category[key] = category[key].strip()
                if category['name'] in names:
                    raise ValueError('分类名称不能重复')
                names.add(category['name'])
            config['model_path'] = str(config['model_path']).strip().strip('"')
            if config['model_path'] and not (Path(config['model_path']) / 'config.json').is_file():
                raise ValueError('模型目录中找不到 config.json，请选择完整的本地模型文件夹')
            changed_model = config['model_path'] != self.config['model_path']
            changed_categories = config['categories'] != self.config['categories']
            with closing(self.connect()) as db, db:
                if changed_model:
                    db.execute('UPDATE assets SET embedding=NULL, model_key=NULL, category=NULL, similarity=NULL')
                elif changed_categories:
                    db.execute('UPDATE assets SET category=NULL, similarity=NULL')
            temporary = self.config_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.config_path)
            self.config = config
        return config

    def scan(self, folder, recursive=True):
        root = require_directory(folder)
        with closing(self.connect()) as db:
            excluded = [Path(row['path']) for row in db.execute('SELECT path FROM exports')]
        excluded.append(self.data)
        if any(root == path or path in root.parents for path in excluded):
            raise ValueError('请选择原始素材目录；缓存和导出目录不能作为导入来源')

        def operation():
            files = []
            self.update(message='正在查找照片…')
            def walk_error(exc):
                self.error(str(exc))
            for base, directories, filenames in os.walk(root, onerror=walk_error, followlinks=False):
                self.check_cancel()
                directories[:] = sorted(d for d in directories if not d.startswith('.')
                                         and not Path(base, d).is_symlink()
                                         and Path(base, d).resolve() not in excluded)
                for name in sorted(filenames):
                    path = Path(base, name)
                    if path.suffix.lower() in SUPPORTED and not path.is_symlink():
                        files.append(path)
                if not recursive:
                    break
            self.update(total=len(files))
            added = skipped = 0
            with closing(self.connect()) as db:
                for n, path in enumerate(files):
                    self.check_cancel()
                    self.update(done=n, message=f'分析 {path.name}')
                    try:
                        stat = path.stat()
                        old = db.execute('SELECT * FROM assets WHERE path=?', (str(path),)).fetchone()
                        if (old and old['mtime_ns'] == stat.st_mtime_ns and old['size'] == stat.st_size
                                and (self.data / 'thumbs' / f'{old["id"]}.jpg').exists()):
                            skipped += 1
                            continue
                        image = open_image(path)
                        metrics = analyze(image)
                        asset_id = hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()[:24]
                        sha = file_hash(path)
                        after = path.stat()
                        if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                            raise ValueError('分析期间文件被修改，请稍后重新扫描')
                        image.thumbnail((640, 640))
                        temporary = self.data / 'thumbs' / f'{asset_id}.tmp'
                        image.save(temporary, 'JPEG', quality=85)
                        temporary.replace(self.data / 'thumbs' / f'{asset_id}.jpg')
                        record = dict(id=asset_id, path=str(path), name=path.name, root=str(root),
                                      size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=sha,
                                      imported_at=time.time(), **metrics)
                        record['flags'] = json.dumps(record['flags'], ensure_ascii=False)
                        columns = ','.join(record)
                        updates = ','.join(f'{key}=excluded.{key}' for key in record if key != 'id')
                        with db:
                            db.execute(f"INSERT INTO assets ({columns}) VALUES ({','.join('?' for _ in record)}) "
                                       f'ON CONFLICT(id) DO UPDATE SET {updates}, '
                                       'embedding=NULL,model_key=NULL,category=NULL,similarity=NULL,'
                                       "duplicate_group=NULL,duplicate_kind=NULL,review='unreviewed'", tuple(record.values()))
                        added += 1
                    except Exception as exc:
                        self.error(f'{path.name}：{exc}')
                    finally:
                        self.update(done=n + 1)
            self.update(message='正在计算重复分组…')
            self.regroup()
            return {'analyzed': added, 'unchanged': skipped, 'found': len(files)}
        return self.start('scan', operation)

    def regroup(self):
        with closing(self.connect()) as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM assets')]
            # Missing originals remain in the catalog for review, but never win recommendations.
            rows = [row for row in rows if Path(row['path']).is_file()]
            groups = duplicate_groups(rows, self.config['duplicate_threshold'])
            with db:
                db.execute('UPDATE assets SET duplicate_group=NULL,duplicate_kind=NULL')
                db.executemany('UPDATE assets SET duplicate_group=?,duplicate_kind=? WHERE id=?',
                               ((group, kind, key) for key, (group, kind) in groups.items()))

    def public(self, row):
        result = {key: row[key] for key in row.keys() if key not in {'embedding', 'sha256', 'phash', 'model_key', 'mtime_ns'}}
        result['flags'] = json.loads(result['flags'])
        result['thumbnail'] = f"/thumbs/{result['id']}.jpg"
        return result

    def list_assets(self, params):
        conditions, args = [], []
        view = params.get('view', 'all')
        if view == 'duplicates':
            conditions.append('duplicate_group IS NOT NULL')
        elif view == 'quality':
            conditions.append('(quality < ? OR flags != ?)')
            args.extend([self.config['quality_threshold'], '[]'])
        elif view in {'kept', 'rejected', 'unreviewed'}:
            conditions.append('review=?')
            args.append(view)
        category = params.get('category', '')
        if category:
            conditions.append('category=?')
            args.append(category)
        query = params.get('q', '').strip()
        if query:
            conditions.append('(instr(lower(name),lower(?))>0 OR instr(lower(path),lower(?))>0)')
            args.extend([query, query])
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        order = {'newest': 'imported_at DESC,id', 'name': 'name COLLATE NOCASE,id',
                 'quality': 'quality DESC,id', 'worst': 'quality ASC,id',
                 'size': 'size DESC,id'}.get(params.get('sort'), 'imported_at DESC,id')
        if view == 'duplicates':
            order = 'duplicate_group,quality DESC,size DESC,id'
        page = max(1, int(params.get('page', 1)))
        limit = max(1, min(200, int(params.get('limit', 80))))
        with closing(self.connect()) as db:
            count = db.execute('SELECT COUNT(*) FROM assets' + where, args).fetchone()[0]
            rows = db.execute('SELECT * FROM assets' + where + f' ORDER BY {order} LIMIT ? OFFSET ?',
                              args + [limit, (page - 1) * limit]).fetchall()
        return {'items': [self.public(row) for row in rows], 'total': count, 'page': page, 'limit': limit}

    def state(self):
        with closing(self.connect()) as db:
            stats = dict(db.execute('''SELECT COUNT(*) AS total, COALESCE(SUM(size),0) AS bytes,
              COUNT(DISTINCT duplicate_group) AS duplicate_groups,
              COALESCE(SUM(duplicate_group IS NOT NULL),0) AS duplicates,
              COALESCE(SUM(quality < ? OR flags != '[]'),0) AS quality,
              COALESCE(SUM(review='kept'),0) AS kept,
              COALESCE(SUM(review='rejected'),0) AS rejected,
              COALESCE(SUM(category IS NOT NULL),0) AS classified FROM assets''',
                                    (self.config['quality_threshold'],)).fetchone())
            categories = [dict(row) for row in db.execute('SELECT category,COUNT(*) AS count FROM assets WHERE category IS NOT NULL GROUP BY category ORDER BY count DESC')]
            roots = [row[0] for row in db.execute('SELECT DISTINCT root FROM assets')]
        model = self.model.status(self.config)
        model['loaded'] = self.model.loaded_key is not None
        model['active_device'] = self.model.device
        return {'stats': stats, 'categories': categories, 'roots': roots,
                'config': self.config, 'model': model, 'job': self.get_job()}

    def classify(self):
        status = self.model.status(self.config)
        if not status['ready']:
            raise ValueError(status['problem'])
        def operation():
            config = json.loads(json.dumps(self.config))
            self.update(message='正在载入本地 SigLIP 2 模型（首次可能较慢）…')
            self.model.ensure(config)
            key = self.model.key(config)
            text_vectors = self.model.text([c['prompt'] for c in config['categories']])
            with closing(self.connect()) as db:
                rows = db.execute("SELECT * FROM assets WHERE review!='rejected' ORDER BY id").fetchall()
                self.update(total=len(rows))
                for n, row in enumerate(rows):
                    self.check_cancel()
                    self.update(done=n, message=f'理解图像 {row["name"]}')
                    try:
                        if row['embedding'] and row['model_key'] == key:
                            vector = np.frombuffer(row['embedding'], dtype=np.float32)
                        else:
                            path = Path(row['path'])
                            stat = path.stat()
                            if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime_ns']):
                                raise ValueError('原文件已修改，请先重新扫描')
                            vector = np.asarray(self.model.image(open_image(path)), dtype=np.float32)
                        similarities = text_vectors @ vector
                        best = int(similarities.argmax())
                        with db:
                            db.execute('UPDATE assets SET category=?,similarity=?,embedding=?,model_key=? WHERE id=?',
                                       (config['categories'][best]['name'], float(similarities[best]),
                                        vector.tobytes(), key, row['id']))
                    except Exception as exc:
                        self.error(f'{row["name"]}：{exc}')
                    finally:
                        self.update(done=n + 1)
            return {'processed': len(rows)}
        return self.start('classify', operation)

    def search(self, query):
        query = str(query).strip()
        if not query or len(query) > 500:
            raise ValueError('请输入 1–500 字的图像描述')
        # Search is a background task too: prevents simultaneous GPU calls and keeps UI responsive.
        def operation():
            self.update(message='正在理解检索文字…')
            self.model.ensure(self.config)
            key = self.model.key(self.config)
            vector = self.model.text([query])[0]
            with closing(self.connect()) as db:
                rows = db.execute('SELECT * FROM assets WHERE embedding IS NOT NULL AND model_key=?', (key,)).fetchall()
            if not rows:
                raise ValueError('尚无图像向量，请先运行 AI 分类')
            results = []
            for row in rows:
                self.check_cancel()
                similarity = float(np.frombuffer(row['embedding'], dtype=np.float32) @ vector)
                results.append(dict(self.public(row), search_similarity=similarity))
            results.sort(key=lambda row: row['search_similarity'], reverse=True)
            self.update(done=len(rows), total=len(rows))
            return {'query': query, 'items': results[:200], 'indexed': len(rows)}
        return self.start('search', operation)

    def review(self, ids, status):
        if status not in {'unreviewed', 'kept', 'rejected'} or not isinstance(ids, list) or not ids:
            raise ValueError('请选择照片及有效的标记状态')
        if len(ids) > 10000:
            raise ValueError('单次最多标记 10000 张照片')
        with closing(self.connect()) as db, db:
            db.executemany('UPDATE assets SET review=? WHERE id=?', ((status, key) for key in ids))
        return {'updated': len(ids)}

    def recommend(self):
        with closing(self.connect()) as db:
            rows = db.execute('SELECT * FROM assets WHERE duplicate_group IS NOT NULL ORDER BY quality DESC,size DESC,id').fetchall()
        winners, suggestions = set(), []
        for row in rows:
            if row['duplicate_group'] not in winners and row['review'] != 'rejected':
                winners.add(row['duplicate_group'])
            elif row['review'] == 'unreviewed':
                suggestions.append(row['id'])
        return {'ids': suggestions}

    @staticmethod
    def safe_name(name):
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:100] or '未分类'
        if name.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            name = '_' + name
        return name

    def export(self, payload):
        destination = require_directory(payload.get('destination', ''))
        if destination == self.data or self.data in destination.parents:
            raise ValueError('不能导出到软件缓存目录')
        ids = payload.get('ids', [])
        if not isinstance(ids, list) or len(ids) > 10000:
            raise ValueError('无效的导出选择')
        by_category = bool(payload.get('by_category', True))

        def operation():
            with closing(self.connect()) as db:
                if ids:
                    rows = []
                    for key in dict.fromkeys(ids):
                        row = db.execute('SELECT * FROM assets WHERE id=?', (key,)).fetchone()
                        if row:
                            rows.append(row)
                else:
                    rows = db.execute("SELECT * FROM assets WHERE review='kept'").fetchall()
            if not rows:
                raise ValueError('没有可导出的照片，请选择照片或先标记为保留')
            output = destination / f'拾光导出_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}'
            output.mkdir()
            with closing(self.connect()) as db, db:
                db.execute('INSERT INTO exports(path) VALUES (?)', (str(output),))
            self.update(total=len(rows))
            copied = []
            for n, row in enumerate(rows):
                self.check_cancel()
                self.update(done=n, message=f'复制 {row["name"]}')
                try:
                    source = Path(row['path'])
                    stat = source.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime_ns']):
                        raise ValueError('原文件已修改，请先重新扫描')
                    subfolder = self.safe_name(row['category'] or '未分类') if by_category else ''
                    target_dir = output / subfolder
                    target_dir.mkdir(exist_ok=True)
                    target = target_dir / row['name']
                    if target.exists():
                        target = target_dir / f'{source.stem}_{row["id"]}{source.suffix}'
                    created = False
                    try:
                        with source.open('rb') as incoming, target.open('xb') as outgoing:
                            created = True
                            shutil.copyfileobj(incoming, outgoing)
                        if file_hash(target) != row['sha256']:
                            raise ValueError('源文件在复制期间被修改或校验失败')
                        shutil.copystat(source, target)
                    except Exception:
                        if created and target.exists():
                            target.unlink()
                        raise
                    copied.append({'source': str(source), 'destination': str(target), 'category': row['category'] or '未分类',
                                   'quality': row['quality'], 'review': row['review'], 'sha256': row['sha256']})
                    # Flush manifest after every successful copy so cancellation retains a usable record.
                    with (output / '整理清单.csv').open('w', newline='', encoding='utf-8-sig') as stream:
                        writer = csv.DictWriter(stream, fieldnames=list(copied[0]))
                        writer.writeheader()
                        writer.writerows(copied)
                except Exception as exc:
                    self.error(f'{row["name"]}：{exc}')
                finally:
                    self.update(done=n + 1, result={'destination': str(output), 'copied': len(copied)})
            return {'destination': str(output), 'copied': len(copied)}
        return self.start('export', operation)
