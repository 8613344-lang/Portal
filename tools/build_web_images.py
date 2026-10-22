"""Build committed WebP sizes for the site's decorative illustrations."""
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1] / 'static' / 'images'


def main():
    target = ROOT / 'web'
    target.mkdir(exist_ok=True)
    for source, stem, widths in [('storybook-hero.webp', 'hero', (480, 960, 1440)),
                                  ('sample-space.webp', 'space', (320, 640)),
                                  ('sample-forest.webp', 'forest', (320, 640))]:
        with Image.open(ROOT / source) as original:
            for width in widths:
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((width, width*2), Image.Resampling.LANCZOS)
                path = target / f'{stem}-{width}.webp'
                image.save(path, 'WEBP', quality=80, method=6)
                print(f'{path.name}: {image.width}x{image.height}, {path.stat().st_size} bytes')
                image.close()


if __name__ == '__main__': main()
