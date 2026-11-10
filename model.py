"""Lazy, offline-only SigLIP 2 inference. No remote model code or uploads."""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
from importlib.metadata import version
import json
from pathlib import Path

import numpy as np

DEFAULT_CATEGORIES = [
    {'name': '人像', 'prompt': 'a portrait photograph of a person'},
    {'name': '风光', 'prompt': 'a landscape photograph of mountains, ocean or nature'},
    {'name': '城市建筑', 'prompt': 'a photograph of city streets and architecture'},
    {'name': '动物', 'prompt': 'a photograph of an animal or a bird'},
    {'name': '植物', 'prompt': 'a close-up photograph of flowers and plants'},
    {'name': '美食', 'prompt': 'a photograph of food and drinks'},
    {'name': '静物', 'prompt': 'a still life photograph of objects and products'},
    {'name': '活动纪实', 'prompt': 'a documentary photograph of a group event or celebration'},
]


def prepare_transformers():
    # SigLIP's Pillow processors need no torchvision operators. Avoid importing an
    # unrelated optional torchvision installation (e.g. one lacking its NMS DLL).
    import transformers.utils.import_utils as imports
    imports._torchvision_available = False


class LocalModel:
    def __init__(self):
        self.loaded_key = None
        self.model = self.processor = self.torch = None
        self.device = None

    @staticmethod
    def status(config):
        dependencies = {name: importlib.util.find_spec(name) is not None
                        for name in ('torch', 'transformers', 'rawpy')}
        path = Path(config.get('model_path', ''))
        configured = bool(config.get('model_path')) and (path / 'config.json').is_file()
        weights_ready = False
        problem = '尚未配置模型，请先下载并选择本地 SigLIP 2 模型目录'
        if configured:
            weights_ready = (path / 'model.safetensors').is_file()
            index = path / 'model.safetensors.index.json'
            if not weights_ready and index.is_file():
                try:
                    shards = json.loads(index.read_text('utf-8')).get('weight_map', {})
                    weights_ready = bool(shards) and all((path / file).is_file() for file in set(shards.values()))
                except (OSError, ValueError, TypeError):
                    pass
            problem = '' if weights_ready else '模型权重尚未下载完整，请等待下载完成（缺少 safetensors 权重或分片）'
        transformer_version = version('transformers') if dependencies['transformers'] else ''
        compatible = bool(transformer_version) and tuple(int(n) for n in transformer_version.split('.')[:2]) >= (4, 51)
        if not dependencies['torch'] or not compatible:
            problem = '推理依赖未就绪，需要 PyTorch 和 Transformers>=4.51'
        return {'dependencies': dependencies, 'configured': configured, 'transformers_version': transformer_version,
                'weights_ready': weights_ready, 'problem': problem,
                'path': config.get('model_path', ''),
                'ready': configured and weights_ready and dependencies['torch'] and compatible}

    @staticmethod
    def key(config):
        folder = Path(config['model_path']).resolve()
        payload = str(folder) + (folder / 'config.json').read_text(encoding='utf-8') + version('transformers')
        for file in sorted(folder.iterdir()):
            if file.suffix in {'.safetensors', '.json', '.model'} and file.is_file():
                stat = file.stat()
                payload += f'|{file.name}:{stat.st_size}:{stat.st_mtime_ns}'
        return hashlib.sha256(payload.encode()).hexdigest()

    def ensure(self, config):
        status = self.status(config)
        if not status['ready']:
            raise ValueError(status['problem'])
        key = self.key(config) + ':' + config['device']
        if self.loaded_key == key:
            return
        try:
            import torch
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 4551:
                raise ValueError('Windows 阻止了当前 Conda 环境的 PyTorch DLL，请使用可运行 PyTorch 的已有环境启动；当前推荐 matanyone2。' + str(exc)) from exc
            raise
        prepare_transformers()
        from transformers import AutoModel, AutoProcessor
        metadata = json.loads((Path(config['model_path']) / 'config.json').read_text(encoding='utf-8'))
        if metadata.get('model_type') not in {'siglip', 'siglip2'}:
            raise ValueError('请选择 SigLIP 2 模型：固定分辨率版使用 siglip 架构，NaFlex 版使用 siglip2 架构')
        device = config['device']
        if device == 'auto':
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
        if device == 'cuda' and not torch.cuda.is_available():
            raise ValueError('当前环境无法使用 CUDA，请选择 CPU 或自动')
        if self.model is not None:
            self.model = self.processor = None
            self.loaded_key = None
            import gc
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        processor = AutoProcessor.from_pretrained(config['model_path'], local_files_only=True,
                                                   trust_remote_code=False)
        model = AutoModel.from_pretrained(config['model_path'], local_files_only=True,
                                          trust_remote_code=False, use_safetensors=True)
        model.eval().to(device)
        self.torch, self.processor, self.model = torch, processor, model
        self.device, self.loaded_key = device, key

    def _features(self, inputs, kind):
        method = self.model.get_image_features if kind == 'image' else self.model.get_text_features
        allowed = inspect.signature(method).parameters
        inputs = {key: value.to(self.device) for key, value in inputs.items() if key in allowed}
        with self.torch.inference_mode():
            result = method(**inputs)
        if not self.torch.is_tensor(result):
            result = result.pooler_output
        result = result / result.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        return result.detach().float().cpu().numpy()

    def image(self, image):
        return self._features(self.processor.image_processor(images=image, return_tensors='pt'), 'image')[0]

    def text(self, texts):
        # SigLIP requires max-length padding for reproducible text representations.
        return self._features(self.processor.tokenizer(texts, padding='max_length', truncation=True,
                              max_length=self.model.config.text_config.max_position_embeddings,
                              return_tensors='pt'), 'text')
