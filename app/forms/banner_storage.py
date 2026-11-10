from io import BytesIO
from pathlib import Path
import secrets
import struct

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_BANNER_SIZE = 8 * 1024 * 1024
MAX_BANNER_PIXELS = 20_000_000
MAX_EXIF_BYTES = 65_536
IMAGE_FORMATS = {"JPEG": ("jpg", "JPEG"), "PNG": ("png", "PNG"), "WEBP": ("webp", "WEBP")}
ALLOWED_IMAGE_FORMATS = tuple(IMAGE_FORMATS.keys())
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _precheck_png_stream(data):
    if not data.startswith(PNG_SIGNATURE):
        return
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ValueError("The uploaded banner is not a valid image.")
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        raise ValueError("The uploaded banner is not a valid image.")
    if width * height > MAX_BANNER_PIXELS:
        raise ValueError("Banner images must be 20 megapixels or smaller.")
    offset = 8
    data_len = len(data)
    while offset + 8 <= data_len:
        chunk_len = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        if chunk_type in {b"acTL", b"fcTL", b"fdAT"}:
            raise ValueError("Use a JPEG, PNG or WebP banner image.")
        next_offset = offset + 12 + chunk_len
        if next_offset <= offset or next_offset > data_len:
            break
        offset = next_offset
        if chunk_type == b"IEND":
            break


def save_banner(upload):
    data = upload.stream.read(MAX_BANNER_SIZE + 1)
    if not data:
        raise ValueError("Choose a banner image to upload.")
    if len(data) > MAX_BANNER_SIZE:
        raise ValueError("Banner images must be 8 MB or smaller.")

    _precheck_png_stream(data)

    try:
        with Image.open(BytesIO(data), formats=ALLOWED_IMAGE_FORMATS) as source:
            image_format = source.format
            if image_format not in IMAGE_FORMATS:
                raise ValueError("Use a JPEG, PNG or WebP banner image.")
            if source.width * source.height > MAX_BANNER_PIXELS:
                raise ValueError("Banner images must be 20 megapixels or smaller.")
            if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) > 1:
                raise ValueError("Use a JPEG, PNG or WebP banner image.")
            source.verify()
        with Image.open(BytesIO(data), formats=ALLOWED_IMAGE_FORMATS) as source:
            raw_exif = source.info.get("exif")
            if (
                image_format != "JPEG"
                or not isinstance(raw_exif, bytes)
                or len(raw_exif) > MAX_EXIF_BYTES
                or raw_exif.startswith(b"Exif\x00\x00Exif\x00\x00")
            ):
                source.info.pop("exif", None)
            image = ImageOps.exif_transpose(source)
            extension, output_format = IMAGE_FORMATS[image_format]
            if output_format == "JPEG" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            clean_image = Image.new(image.mode, image.size)
            clean_image.paste(image)
            output = BytesIO()
            clean_image.save(output, format=output_format, optimize=True)
            if output.tell() > MAX_BANNER_SIZE:
                raise ValueError("The processed banner image exceeds 8 MB.")
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        if isinstance(error, UnidentifiedImageError) and data.startswith(
            (b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*", b"#define ")
        ):
            raise ValueError("Use a JPEG, PNG or WebP banner image.") from error
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
