from io import BytesIO
from pathlib import Path
import secrets

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_BANNER_SIZE = 8 * 1024 * 1024
MAX_BANNER_PIXELS = 20_000_000
IMAGE_FORMATS = {"JPEG": ("jpg", "JPEG"), "PNG": ("png", "PNG"), "WEBP": ("webp", "WEBP")}


def save_banner(upload):
    data = upload.stream.read(MAX_BANNER_SIZE + 1)
    if not data:
        raise ValueError("Choose a banner image to upload.")
    if len(data) > MAX_BANNER_SIZE:
        raise ValueError("Banner images must be 8 MB or smaller.")

    try:
        with Image.open(BytesIO(data)) as source:
            image_format = source.format
            if image_format not in IMAGE_FORMATS:
                raise ValueError("Use a JPEG, PNG or WebP banner image.")
            if source.width * source.height > MAX_BANNER_PIXELS:
                raise ValueError("Banner images must be 20 megapixels or smaller.")
            source.verify()
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            extension, output_format = IMAGE_FORMATS[image_format]
            if output_format == "JPEG" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format=output_format, optimize=True)
            if output.tell() > MAX_BANNER_SIZE:
                raise ValueError("The processed banner image exceeds 8 MB.")
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The uploaded banner is not a valid image.") from error

    filename = f"{secrets.token_hex(24)}.{extension}"
    root = Path(current_app.config["FORM_BANNER_STORAGE_PATH"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    try:
        path.write_bytes(output.getvalue())
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return filename


def remove_banner(filename):
    if not filename:
        return
    root = Path(current_app.config["FORM_BANNER_STORAGE_PATH"]).resolve()
    path = (root / filename).resolve()
    if root not in path.parents:
        raise RuntimeError("The form banner path escaped its private storage directory.")
    path.unlink(missing_ok=True)
