"""Local image decoding, conservative quality signals, and perceptual hashing."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

RAWS = {'.nef', '.nrw', '.arw', '.cr2', '.cr3', '.dng', '.raf', '.rw2', '.orf', '.pef', '.srw'}
SUPPORTED = RAWS | {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}


def open_image(path: Path) -> Image.Image:
    if path.suffix.lower() in RAWS:
        try:
            import rawpy
        except ImportError as exc:
            raise ValueError('读取 RAW 需要 rawpy，请按说明安装可选 RAW 依赖') from exc
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
                image = (Image.open(io.BytesIO(thumb.data)).copy()
                         if thumb.format == rawpy.ThumbFormat.JPEG
                         else Image.fromarray(thumb.data))
            except rawpy.LibRawNoThumbnailError:
                image = Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True))
        return ImageOps.exif_transpose(image).convert('RGB')
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert('RGB')


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def analyze(image: Image.Image) -> dict:
    width, height = image.size
    sample = ImageOps.grayscale(image)
    sample.thumbnail((1024, 1024))
    gray = np.asarray(sample, dtype=np.float32)
    laplace = (gray[1:-1, :-2] + gray[1:-1, 2:] + gray[:-2, 1:-1]
               + gray[2:, 1:-1] - 4 * gray[1:-1, 1:-1])
    sharpness = float(laplace.var()) if laplace.size else 0.0
    dark = float((gray < 12).mean())
    bright = float((gray > 245).mean())
    brightness, contrast = float(gray.mean()), float(gray.std())
    flags = []
    if sharpness < 65:
        flags.append('可能模糊')
    if dark > .42 or brightness < 38:
        flags.append('可能欠曝')
    if bright > .42 or brightness > 222:
        flags.append('可能过曝')
    if min(width, height) < 800:
        flags.append('分辨率偏低')
    detail_score = np.clip(35 + 20 * np.log10(max(sharpness, 1)), 0, 100)
    exposure_score = np.clip(100 - max(dark, bright) * 85, 0, 100)
    resolution_score = min(100, min(width, height) / 12)
    quality = round(float(.55 * detail_score + .30 * exposure_score + .15 * resolution_score))

    small = np.asarray(ImageOps.grayscale(image).resize((32, 32)), dtype=np.float32)
    indices = np.arange(32)
    basis = np.cos(np.pi * (2 * indices[None, :] + 1) * indices[:, None] / 64)
    coefficients = (basis @ small @ basis.T)[:8, :8].flatten()[1:]
    bits = coefficients > np.median(coefficients)
    phash = format(sum(int(bit) << n for n, bit in enumerate(bits)), '016x')
    return dict(width=width, height=height, sharpness=round(sharpness, 2),
                brightness=round(brightness, 2), contrast=round(contrast, 2),
                dark_ratio=round(dark, 4), bright_ratio=round(bright, 4),
                quality=quality, flags=flags, phash=phash)


class BKTree:
    """Hamming-distance index; avoids comparing every pair of photographs."""
    def __init__(self):
        self.root = None

    def add(self, value: int, item: dict):
        if self.root is None:
            self.root = (value, [item], {})
            return
        node = self.root
        while True:
            distance = (value ^ node[0]).bit_count()
            if distance == 0:
                node[1].append(item)
                return
            if distance not in node[2]:
                node[2][distance] = (value, [item], {})
                return
            node = node[2][distance]

    def query(self, value: int, radius: int):
        pending = [self.root] if self.root else []
        while pending:
            other, items, children = pending.pop()
            distance = (value ^ other).bit_count()
            if distance <= radius:
                yield from items
            pending.extend(child for edge, child in children.items()
                           if distance - radius <= edge <= distance + radius)


def duplicate_groups(rows: list[dict], threshold: int = 6) -> dict[str, tuple[str, str]]:
    """Connected components; 'similar' always requires human review."""
    parents = {row['id']: row['id'] for row in rows}

    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def union(a, b):
        parents[find(a)] = find(b)

    exact, tree = {}, BKTree()
    for row in rows:
        if row['sha256'] in exact:
            union(row['id'], exact[row['sha256']])
        else:
            exact[row['sha256']] = row['id']
        # Uniform images have degenerate hashes; do not merge them perceptually.
        if row['contrast'] < 5:
            continue
        value = int(row['phash'], 16)
        for other in tree.query(value, threshold):
            aspect = row['width'] / max(row['height'], 1)
            other_aspect = other['width'] / max(other['height'], 1)
            if abs(aspect - other_aspect) < .08 and abs(row['brightness'] - other['brightness']) < 35:
                union(row['id'], other['id'])
        tree.add(value, row)
    groups = {}
    for row in rows:
        groups.setdefault(find(row['id']), []).append(row)
    result = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        group_id = min(item['id'] for item in group)
        kind = 'exact' if len({item['sha256'] for item in group}) == 1 else 'similar'
        for item in group:
            result[item['id']] = (group_id, kind)
    return result
