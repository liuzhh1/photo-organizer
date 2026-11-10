from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401

import csv
import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app import make_server
from imaging import analyze, duplicate_groups, file_hash
from library import Library


def photo(path, seed=1):
    rng = np.random.default_rng(seed)
    image = Image.fromarray(rng.integers(30, 225, (900, 1200, 3), dtype=np.uint8))
    image.save(path)


def wait(library):
    deadline = time.monotonic() + 20
    while library.get_job()['running']:
        if time.monotonic() > deadline:
            raise TimeoutError(library.get_job())
        time.sleep(.01)
    return library.get_job()


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / '照片'
        self.source.mkdir()
        self.library = Library(self.root / 'data')

    def tearDown(self):
        wait(self.library)
        self.temporary.cleanup()

    def scan(self):
        self.library.scan(str(self.source))
        job = wait(self.library)
        self.assertFalse(job['errors'], job)
        return self.library.list_assets({})['items']

    def test_exact_duplicates_and_incremental_import(self):
        photo(self.source / 'a.png')
        shutil.copy2(self.source / 'a.png', self.source / 'b.png')
        before = file_hash(self.source / 'a.png')
        items = self.scan()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['duplicate_group'], items[1]['duplicate_group'])
        self.assertEqual(items[0]['duplicate_kind'], 'exact')
        self.scan()
        self.assertEqual(self.library.get_job()['result']['unchanged'], 2)
        self.assertEqual(file_hash(self.source / 'a.png'), before)

    def test_uniform_images_are_not_perceptual_duplicates(self):
        Image.new('RGB', (1000, 1000), '#f8f8f8').save(self.source / 'white.png')
        Image.new('RGB', (1000, 1000), '#040404').save(self.source / 'black.png')
        items = self.scan()
        self.assertTrue(all(item['duplicate_group'] is None for item in items))
        flags = [flag for item in items for flag in item['flags']]
        self.assertIn('可能欠曝', flags)
        self.assertIn('可能过曝', flags)

    def test_blur_signal_decreases(self):
        photo(self.source / 'sharp.png')
        with Image.open(self.source / 'sharp.png') as image:
            sharp = analyze(image)
            blurred = analyze(image.filter(ImageFilter.GaussianBlur(10)))
        self.assertGreater(sharp['sharpness'], blurred['sharpness'])
        self.assertIn('可能模糊', blurred['flags'])

    def test_corrupt_file_is_reported_and_other_images_imported(self):
        (self.source / 'bad.jpg').write_bytes(b'not a jpeg')
        photo(self.source / 'ok.png')
        self.library.scan(str(self.source))
        job = wait(self.library)
        self.assertEqual(len(job['errors']), 1)
        self.assertIn('bad.jpg', job['errors'][0])
        self.assertEqual(self.library.state()['stats']['total'], 1)

    def test_review_export_same_names_and_hash_verification(self):
        (self.source / 'one').mkdir()
        (self.source / 'two').mkdir()
        photo(self.source / 'one' / 'photo.png', 1)
        photo(self.source / 'two' / 'photo.png', 2)
        items = self.scan()
        self.library.review([item['id'] for item in items], 'kept')
        destination = self.root / 'output'
        destination.mkdir()
        self.library.export({'destination': str(destination), 'ids': [], 'by_category': True})
        job = wait(self.library)
        self.assertFalse(job['errors'], job)
        output = Path(job['result']['destination'])
        images = list(output.rglob('*.png'))
        self.assertEqual(len(images), 2)
        self.assertEqual({file_hash(p) for p in images}, {file_hash(p) for p in self.source.rglob('*.png')})
        with (output / '整理清单.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 2)
        self.assertEqual(len(list(self.source.rglob('*.png'))), 2)
        with self.assertRaises(ValueError):
            self.library.scan(output)

    def test_export_rejects_modified_source(self):
        path = self.source / 'one.png'
        photo(path)
        item = self.scan()[0]
        photo(path, 42)
        self.library.export({'destination': str(self.root), 'ids': [item['id']]})
        job = wait(self.library)
        self.assertEqual(job['result']['copied'], 0)
        self.assertTrue(job['errors'])

    def test_changed_file_clears_cached_ai(self):
        path = self.source / 'one.png'
        photo(path)
        item = self.scan()[0]
        with closing(self.library.connect()) as db, db:
            db.execute("UPDATE assets SET category='旧分类',embedding=?,model_key='old' WHERE id=?", (b'x', item['id']))
        photo(path, 5)
        self.assertIsNone(self.scan()[0]['category'])
        with closing(self.library.connect()) as db:
            self.assertIsNone(db.execute('SELECT embedding FROM assets').fetchone()[0])

    def test_filters_and_pagination(self):
        for n in range(3):
            photo(self.source / f'{n}.png', n)
        items = self.scan()
        self.library.review([items[0]['id']], 'kept')
        self.assertEqual(self.library.list_assets({'view': 'kept'})['total'], 1)
        self.assertEqual(len(self.library.list_assets({'limit': 2, 'page': 2})['items']), 1)
        self.assertEqual(self.library.list_assets({'q': '1.png'})['total'], 1)

    def test_configuration_validation(self):
        with self.assertRaises(ValueError):
            self.library.save_config({'duplicate_threshold': 100})
        with self.assertRaises(ValueError):
            self.library.save_config({'categories': []})
        self.library.save_config({'quality_threshold': 70})
        self.assertEqual(Library(self.library.data).config['quality_threshold'], 70)

    def test_classification_without_model_is_clear_validation_error(self):
        with self.assertRaisesRegex(ValueError, '模型'):
            self.library.classify()
        self.assertFalse(self.library.get_job()['running'])
        self.assertEqual(self.library.state()['stats']['total'], 0)

    def test_single_job_and_cooperative_cancel(self):
        ready = threading.Event()
        release = threading.Event()
        def operation():
            ready.set()
            release.wait(3)
            self.library.check_cancel()
        self.library.start('test', operation)
        self.assertTrue(ready.wait(2))
        with self.assertRaises(ValueError):
            self.library.start('second', lambda: None)
        self.library.cancel_event.set()
        release.set()
        self.assertIn('取消', wait(self.library)['message'])

    def test_recommendations_do_not_change_review_status(self):
        photo(self.source / 'a.png')
        shutil.copy2(self.source / 'a.png', self.source / 'b.png')
        self.scan()
        suggestions = self.library.recommend()['ids']
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(self.library.state()['stats']['rejected'], 0)

    def test_server_blocks_unauthorized_writes_and_path_traversal(self):
        server = make_server(self.library, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            html = urllib.request.urlopen(url).read().decode()
            self.assertIn('拾光', html)
            self.assertNotIn('__SESSION_TOKEN__', html)
            request = urllib.request.Request(url + '/api/cancel', data=b'{}', headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(request)
            self.assertEqual(context.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(url + '/thumbs/../../config.json')
            self.assertEqual(context.exception.code, 404)
            request = urllib.request.Request(url + '/api/state', headers={'Host':'evil.example'})
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(request)
            self.assertEqual(context.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
