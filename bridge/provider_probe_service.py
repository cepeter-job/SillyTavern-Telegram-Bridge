"""Application-scoped manual probes; no automatic polling or runtime-health mutation."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from itertools import islice

from bridge.provider_catalog import load_provider_catalog, merge_model_catalog
from bridge.provider_catalog_cache import read_model_cache
from bridge.settings import AppSettings


class ProviderProbeService:
    def __init__(
        self,
        *,
        app_settings: AppSettings,
        probe_backend: Callable[[str, object], tuple[str, str, str]],
    ) -> None:
        self._settings = app_settings
        self._probe_backend = probe_backend
        self._sweep_lock = threading.Lock()

    def _probe(self, item: tuple[str, object]) -> tuple[str, str, str]:
        provider_id, spec = item
        try:
            return self._probe_backend(provider_id, spec)
        except Exception:
            name = str(spec.get("name") or provider_id) if isinstance(spec, Mapping) else provider_id
            return provider_id, name, "probe failed (request_failed)"

    def check(self, provider_id: str | None = None) -> list[tuple[str, str, str]]:
        # Serialize entire sweeps so simultaneous panel users cannot multiply the worker cap.
        with self._sweep_lock:
            providers = load_provider_catalog(app_settings=self._settings)
            known = merge_model_catalog(providers, read_model_cache(app_settings=self._settings))
            items = list(
                islice(
                    (
                        (current_id, known.get(current_id, spec))
                        for current_id, spec in providers.items()
                        if provider_id is None or current_id == provider_id
                    ),
                    1024,
                )
            )
            if not items:
                return []
            with ThreadPoolExecutor(max_workers=min(3, len(items)), thread_name_prefix="provider-probe") as pool:
                return list(pool.map(self._probe, items))
