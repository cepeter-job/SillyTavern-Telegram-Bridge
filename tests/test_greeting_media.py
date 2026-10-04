import pytest

from bridge.greeting_media import first_image_url


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Read https://example.org/story; see https://cdn.example.org/pic.webp?token=1",
            "https://cdn.example.org/pic.webp?token=1",
        ),
        ("![portrait](https://cdn.example.org/image?id=1)", "https://cdn.example.org/image?id=1"),
        ("<img src='https://cdn.example.org/image?id=2'>", "https://cdn.example.org/image?id=2"),
        ("[img]https://cdn.example.org/image?id=3[/img]", "https://cdn.example.org/image?id=3"),
        ("https://example.org/readme", None),
        ("http://cdn.example.org/pic.png", None),
        ("https://127.0.0.1/internal.png", None),
        ("https://169.254.169.254/metadata.png", None),
        ("https://example.local/photo.png", None),
        ("https://cdn.example.org:8080/photo.png", None),
        ("file:///etc/passwd", None),
        ("![portrait](https://user:secret@example.org/picture.png)", None),
    ],
)
def test_first_image_url(message, expected):
    assert first_image_url(message) == expected
