"""Turn WINDOWSICON.jfif into icon.ico (exe + window icon) and icon.png (fallback)."""
from PIL import Image

img = Image.open('WINDOWSICON.jfif').convert('RGBA')
side = max(img.size)
square = Image.new('RGBA', (side, side), (0, 0, 0, 0))   # pad to a square so nothing is stretched
square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
square.save('icon.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
square.resize((256, 256), Image.LANCZOS).save('icon.png')
print('Wrote icon.ico and icon.png')