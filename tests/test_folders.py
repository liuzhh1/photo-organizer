from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401

import os
import tempfile
import unittest
from unittest.mock import patch

from folders import FolderBrowser, folder_error, normalize_path, require_directory


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.browser = FolderBrowser(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_unc_and_smb_addresses_preserve_server_share_and_unicode(self):
        expected = r'\\nas.example\photos\旅行\示例相册'
        for value in [expected, '  "' + expected + '"  ',
                      '//nas.example/photos/旅行/示例相册',
                      'smb://nas.example/photos/%E6%97%85%E8%A1%8C/%E7%A4%BA%E4%BE%8B%E7%9B%B8%E5%86%8C',
                      'file://nas.example/photos/旅行/示例相册']:
            self.assertEqual(normalize_path(value), expected)

    def test_empty_and_credentials_in_uri_rejected(self):
        for value in ['', '   ', 'smb://user:secret@nas/photos']:
            with self.assertRaises(ValueError):
                normalize_path(value)

    def test_folder_listing_parent_filter_and_pagination(self):
        for name in ['A照片', 'B照片', 'C照片', '.hidden']:
            (self.root / name).mkdir()
        (self.root / 'image.jpg').write_bytes(b'not relevant')
        listing = self.browser.browse(str(self.root), limit=2)
        self.assertEqual(listing['total'], 3)
        self.assertEqual([item['name'] for item in listing['folders']], ['A照片', 'B照片'])
        self.assertEqual(listing['parent'], str(self.root.resolve().parent))
        self.assertTrue(listing['selectable'])
        self.assertEqual(self.browser.browse(str(self.root), offset=2, limit=2)['folders'][0]['name'], 'C照片')
        self.assertEqual(self.browser.browse(str(self.root), query='B照')['total'], 1)

    def test_home_locations_require_no_network_enumeration(self):
        listing = self.browser.browse()
        self.assertFalse(listing['selectable'])
        self.assertIsNone(listing['parent'])
        self.assertTrue(any(item['kind'] == 'home' for item in listing['locations']))

    def test_selected_folder_remembered_without_duplicates(self):
        self.browser.remember(str(self.root))
        self.browser.remember(str(self.root))
        self.assertEqual(FolderBrowser(self.root).recent(), [str(self.root.resolve())])
        self.assertTrue(any(item['kind'] == 'recent' for item in self.browser.locations()))

    def test_directory_permission_error_is_reported(self):
        with patch('folders.os.scandir', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError, '权限'):
                self.browser.browse(str(self.root))
        self.assertIn('资源管理器', str(folder_error(PermissionError(), r'\\nas\photos')))
        with self.assertRaisesRegex(ValueError, '不存在'):
            require_directory(str(self.root / 'missing'))

    @unittest.skipUnless(os.name == 'nt', 'Windows share roots')
    def test_unc_server_lists_shares_and_cannot_be_selected(self):
        shares = [{'name': 'photos', 'path': r'\\nas.example\photos'}]
        with patch('folders.windows_shares', return_value=shares) as query:
            listing = self.browser.browse(r'\\nas.example')
        query.assert_called_once_with(r'\\nas.example')
        self.assertEqual(listing['folders'], shares)
        self.assertFalse(listing['selectable'])
        self.assertEqual(listing['parent'], '')


if __name__ == '__main__':
    unittest.main()
