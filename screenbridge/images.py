"""Keep question text legible while respecting the screenshot upload limit."""

import io

from PIL import Image


def encode_question_image(source, byte_limit=8 * 1024 * 1024):
    image = source.convert("RGB")
    image.info.clear()
    image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
    # Screenshots contain sharp text and thin grid lines; preserve them losslessly if possible.
    png = io.BytesIO()
    image.save(png, format="PNG")
    if png.tell() <= byte_limit:
        return png.getvalue()
    for quality in (95, 90, 85):
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, subsampling=0)
        if output.tell() <= byte_limit:
            return output.getvalue()
    # Text screenshots normally fit at native resolution; highly textured images may not.
    while image.width > 1 or image.height > 1:
        image = image.resize(
            (max(1, int(image.width * 0.85)), max(1, int(image.height * 0.85))),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85, subsampling=0)
        if output.tell() <= byte_limit:
            return output.getvalue()
    raise ValueError("图片无法压缩至上传限额，请缩小截图范围")


def image_mime(data):
    return "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
