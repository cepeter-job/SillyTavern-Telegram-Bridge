"""Select a first-message image without fetching untrusted card URLs locally."""

import ipaddress
import re
from urllib.parse import urlsplit

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_URL = re.compile(r"https?://[^\s<>\"'`)\[\]]+", re.IGNORECASE)


def first_image_url(greeting: str) -> str | None:
    for match in _URL.finditer(greeting):
        url = match.group().rstrip(".,;!?")
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme.casefold() != "https"
            or not hostname
            or port not in (None, 443)
            or parsed.username
            or parsed.password
            or any(char in parsed.netloc for char in "\\%")
            or hostname.casefold().rstrip(".") in {"localhost", "localhost.localdomain"}
            or hostname.casefold().rstrip(".").endswith((".localhost", ".local"))
        ):
            continue
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                continue
        path = parsed.path.casefold()
        marked_image = bool(
            re.search(
                r"!\[[^]\n]*\]\(\s*$|<img\b[^>]*\bsrc\s*=\s*['\"]?\s*$|\[img\]\s*$",
                greeting[max(0, match.start() - 200) : match.start()],
                re.IGNORECASE,
            )
        )
        if marked_image or any(path.endswith(extension) for extension in _IMAGE_EXTENSIONS):
            return url
    return None
