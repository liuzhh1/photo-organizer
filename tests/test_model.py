"""Real Transformers inference on a tiny RANDOM model; verifies plumbing, not accuracy."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401

import importlib.util
import tempfile
import time
import unittest
from contextlib import closing
from importlib.metadata import version

import numpy as np
from PIL import Image

from library import Library
from model import LocalModel, prepare_transformers

AVAILABLE = (importlib.util.find_spec('torch') is not None
             and importlib.util.find_spec('transformers') is not None
             and tuple(int(n) for n in version('transformers').split('.')[:2]) >= (4, 51))


@unittest.skipUnless(AVAILABLE, 'project-local SigLIP 2 dependencies are optional')
class Siglip2InferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare_transformers()
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        from transformers import (PreTrainedTokenizerFast, Siglip2Config, Siglip2Model,
                                  Siglip2ImageProcessor, Siglip2Processor)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.folder = cls.root / 'tiny-siglip2-random'
        vocab = {'[PAD]': 0, '[UNK]': 1, '</s>': 2, 'a': 3, 'red': 4,
                 'blue': 5, 'photo': 6, 'landscape': 7, 'portrait': 8}
        underlying = Tokenizer(WordLevel(vocab=vocab, unk_token='[UNK]'))
        underlying.pre_tokenizer = Whitespace()
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=underlying, unk_token='[UNK]',
                                           pad_token='[PAD]', eos_token='</s>', model_max_length=64)
        processor = Siglip2Processor(Siglip2ImageProcessor(patch_size=4, max_num_patches=16), tokenizer)
        config = Siglip2Config(
            text_config={'vocab_size': len(vocab), 'hidden_size': 32, 'intermediate_size': 64,
                         'num_hidden_layers': 1, 'num_attention_heads': 4,
                         'max_position_embeddings': 64, 'pad_token_id': 0, 'eos_token_id': 2},
            vision_config={'hidden_size': 32, 'intermediate_size': 64,
                           'num_hidden_layers': 1, 'num_attention_heads': 4,
                           'patch_size': 4, 'num_patches': 16})
        Siglip2Model(config).save_pretrained(cls.folder, safe_serialization=True)
        processor.save_pretrained(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def wait(self, library):
        deadline = time.monotonic() + 60
        while library.get_job()['running']:
            if time.monotonic() > deadline:
                self.fail(str(library.get_job()))
            time.sleep(.02)
        self.assertFalse(library.get_job()['errors'], library.get_job())

    def test_offline_loading_normalized_vectors_classification_and_search(self):
        library = Library(self.root / 'catalog')
        library.save_config({'model_path': str(self.folder), 'device': 'cpu', 'categories': [
            {'name': '红色', 'prompt': 'a red photo'}, {'name': '蓝色', 'prompt': 'a blue photo'}]})
        source = self.root / 'pictures'
        source.mkdir(exist_ok=True)
        Image.new('RGB', (1000, 900), 'red').save(source / 'red.jpg')
        Image.new('RGB', (1000, 900), 'blue').save(source / 'blue.jpg')
        library.scan(str(source))
        self.wait(library)
        library.classify()
        self.wait(library)
        self.assertEqual(library.state()['stats']['classified'], 2)
        with closing(library.connect()) as db:
            for row in db.execute('SELECT embedding,similarity FROM assets'):
                vector = np.frombuffer(row['embedding'], dtype=np.float32)
                self.assertEqual(vector.shape, (32,))
                self.assertAlmostEqual(float(np.linalg.norm(vector)), 1, places=5)
                self.assertTrue(np.isfinite(row['similarity']))
        library.search('a red photo')
        self.wait(library)
        results = library.get_job()['result']['items']
        self.assertEqual(len(results), 2)
        self.assertGreaterEqual(results[0]['search_similarity'], results[1]['search_similarity'])

    def test_model_key_changes_when_weight_file_changes(self):
        config = {'model_path': str(self.folder)}
        old = LocalModel.key(config)
        file = self.folder / 'extra.safetensors'
        try:
            file.write_bytes(b'fingerprint only; never loaded')
            self.assertNotEqual(old, LocalModel.key(config))
        finally:
            file.unlink(missing_ok=True)

    def test_partial_model_directory_is_not_ready(self):
        folder = self.root / 'incomplete-model'
        folder.mkdir(exist_ok=True)
        (folder / 'config.json').write_text('{"model_type":"siglip"}', encoding='utf-8')
        status = LocalModel.status({'model_path': str(folder)})
        self.assertTrue(status['configured'])
        self.assertFalse(status['ready'])
        self.assertIn('权重', status['problem'])

    def test_cuda_offline_inference_if_available(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest('CUDA unavailable')
        model = LocalModel()
        model.ensure({'model_path': str(self.folder), 'device': 'cuda'})
        vector = model.image(Image.new('RGB', (80, 60), 'green'))
        texts = model.text(['a red photo', 'a blue photo'])
        self.assertEqual(model.device, 'cuda')
        self.assertEqual(vector.shape, (32,))
        self.assertEqual(texts.shape, (2, 32))
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1, places=5)
        self.assertTrue(np.isfinite(texts).all())

    def test_rejected_missing_photos_do_not_block_classification(self):
        library = Library(self.root / 'rejected-catalog')
        library.save_config({'model_path': str(self.folder), 'device': 'cpu'})
        folder = self.root / 'rejected-pictures'
        folder.mkdir(exist_ok=True)
        path = folder / 'rejected.jpg'
        Image.new('RGB', (900, 900), 'red').save(path)
        library.scan(str(folder))
        self.wait(library)
        item = library.list_assets({})['items'][0]
        library.review([item['id']], 'rejected')
        path.unlink()
        library.classify()
        self.wait(library)
        self.assertEqual(library.get_job()['total'], 0)
        self.assertEqual(library.state()['stats']['rejected'], 1)

    def test_fixed_resolution_siglip2_architecture(self):
        from transformers import SiglipConfig, SiglipModel, SiglipProcessor, SiglipImageProcessor
        folder = self.root / 'fixed-resolution-siglip2-random'
        tokenizer = LocalModel()
        tokenizer.ensure({'model_path': str(self.folder), 'device': 'cpu'})
        processor = SiglipProcessor(SiglipImageProcessor(size={'height': 16, 'width': 16}),
                                    tokenizer.processor.tokenizer)
        config = SiglipConfig(
            text_config={'vocab_size': 9, 'hidden_size': 32, 'intermediate_size': 64,
                         'num_hidden_layers': 1, 'num_attention_heads': 4,
                         'max_position_embeddings': 64, 'pad_token_id': 0, 'eos_token_id': 2},
            vision_config={'hidden_size': 32, 'intermediate_size': 64,
                           'num_hidden_layers': 1, 'num_attention_heads': 4,
                           'patch_size': 4, 'image_size': 16})
        SiglipModel(config).save_pretrained(folder, safe_serialization=True)
        processor.save_pretrained(folder)
        model = LocalModel()
        model.ensure({'model_path': str(folder), 'device': 'cpu'})
        self.assertEqual(model.image(Image.new('RGB', (80, 60), 'red')).shape, (32,))
        self.assertEqual(model.text(['a photo']).shape, (1, 32))


if __name__ == '__main__':
    unittest.main()
