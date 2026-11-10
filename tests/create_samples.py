"""Generate test illustrations only. Never seed the user's production catalog."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401
import shutil
from PIL import Image, ImageDraw, ImageFilter


def main():
    root = Path(__file__).resolve().parents[1] / 'qa'
    source = root / 'samples'
    source.mkdir(parents=True, exist_ok=True)
    (root / 'exports').mkdir(exist_ok=True)
    palettes = [('#dfe7dc', '#b4c5a2', '#6d8a6d', '#3c5f50'),
                ('#dfe7eb', '#bdc7bc', '#6f8c8b', '#3b6469'),
                ('#e9dbd0', '#d4b5a3', '#a4856c', '#675d4d'),
                ('#e6dfc4', '#cbc595', '#96a779', '#5d7b60'),
                ('#cad9e5', '#a7bfce', '#6689a5', '#355e81'),
                ('#ead8c4', '#ceb994', '#b19673', '#7c7860')]
    for i, colors in enumerate(palettes):
        image = Image.new('RGB', (1440, 1080), colors[0])
        draw = ImageDraw.Draw(image)
        draw.ellipse((950, 100, 1090, 240), fill='#f7f0d7')
        draw.polygon([(0, 700), (420, 260 + i * 20), (820, 750), (1100, 400), (1440, 650), (1440, 1080), (0, 1080)], fill=colors[1])
        draw.polygon([(0, 950), (500, 560 - i * 10), (980, 1080), (1440, 700), (1440, 1080), (0, 1080)], fill=colors[2])
        draw.polygon([(0, 1050), (850, 810), (1200, 1020), (1440, 900), (1440, 1080), (0, 1080)], fill=colors[3])
        draw.text((50, 1020), f'TEST FRAME {i + 1:02} / GENERATED QA FIXTURE', fill='#ffffff')
        image.save(source / f'DSC_{1001 + i}.jpg', quality=95)
    shutil.copy2(source / 'DSC_1001.jpg', source / 'DSC_1001_copy.jpg')
    Image.new('RGB', (640, 480), '#181c1a').save(source / 'DSC_1007_dark.jpg')
    print(source)


if __name__ == '__main__':
    main()
