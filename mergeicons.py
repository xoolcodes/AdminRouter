"""Merge <prefix>_16x16.ico, <prefix>_32x32.ico and <prefix>_48x48.ico into one multi-size .ico.

usage: python merge_icons.py <prefix> <output.ico>
Writes nothing (and exits 0) if none of the source files exist.
"""
import os
import sys

from PIL import Image

SIZES = (48, 32, 16)


def load(path, size):
    try:
        image = Image.open(path)
        image.size = (size, size)      # pick the matching frame if the file has several
        image.load()
        return image.convert('RGBA')
    except Exception:  # noqa: BLE001 - file holds another size: scale it
        return Image.open(path).convert('RGBA').resize((size, size), Image.LANCZOS)


def main():
    prefix, out = sys.argv[1], sys.argv[2]
    frames = [load(f'{prefix}_{s}x{s}.ico', s) for s in SIZES if os.path.exists(f'{prefix}_{s}x{s}.ico')]
    if not frames:
        print(f'No {prefix}_*.ico files found')
        return
    frames[0].save(out, format='ICO', sizes=[f.size for f in frames], append_images=frames[1:])
    print(f'Wrote {out} with sizes {[f.size for f in frames]}')


if __name__ == '__main__':
    main()