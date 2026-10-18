"""Public profile images: bounded decode, canonical pixels, no source metadata."""
import base64
import io
import re
from PIL import Image, ImageOps, UnidentifiedImageError

AVATARS = ('bunny', 'cat', 'bear', 'flower', 'moon', 'sparkles')
COLORS = ('#ffe4ec', '#eae5ff', '#e2f1e8', '#fff0d6', '#dfefff')


def normalize_avatar(value):
    if not isinstance(value, str):
        raise ValueError('头像格式不正确。')
    if value in AVATARS:
        return value
    if len(value) > 180000:
        raise ValueError('头像图片过大，请在客户端重新裁剪。')
    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise ValueError('请选择 PNG 或 JPEG 头像图片。')
    try:
        raw = base64.b64decode(match[2], validate=True)
        if len(raw) > 128 * 1024:
            raise ValueError('头像图片过大，请在客户端重新裁剪。')
        with Image.open(io.BytesIO(raw)) as image:
            expected = 'PNG' if match[1] == 'png' else 'JPEG'
            if image.format != expected or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('请选择静态 PNG 或 JPEG 图片。')
            if not (16 <= image.width <= 1024 and 16 <= image.height <= 1024):
                raise ValueError('头像尺寸应为 16 至 1024 像素，请在客户端重新裁剪。')
            image.load()
            pixels = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGBA'), (256, 256),
                                  method=Image.Resampling.LANCZOS)
            clean = Image.new('RGB', (256, 256), '#fff8fa')
            clean.paste(pixels, mask=pixels.getchannel('A'))
            output = io.BytesIO()
            clean.save(output, 'JPEG', quality=85, optimize=True)
        return 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('无法读取头像图片，请重新选择。') from error
