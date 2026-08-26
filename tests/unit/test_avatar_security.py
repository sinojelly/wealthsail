"""Avatar validation/re-encoding tests."""

from io import BytesIO

import pytest
from PIL import Image

from backend.errors import ValidationError_
from backend.identity.service import normalize_avatar_image


def _image_bytes(format_: str = "PNG", size: tuple[int, int] = (64, 64)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (30, 120, 180)).save(output, format=format_)
    return output.getvalue()


def test_avatar_is_decoded_and_reencoded_to_bounded_webp() -> None:
    data, extension = normalize_avatar_image(_image_bytes(), "image/png")
    image = Image.open(BytesIO(data))

    assert extension == "webp"
    assert image.format == "WEBP"
    assert image.width <= 1024 and image.height <= 1024


def test_avatar_rejects_spoofed_image_content() -> None:
    with pytest.raises(ValidationError_, match="无法解码"):
        normalize_avatar_image(b"<script>alert(1)</script>", "image/png")
