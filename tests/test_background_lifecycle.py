import concurrent

from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

import bridge.background as _background

ensure_application_extensions()

import concurrent.futures
import unittest
from unittest.mock import Mock, patch

import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator


class BackgroundLifecycleTests(SettingsTestCase):
    @classmethod
    def setUpClass(cls):
        # Simulate unrelated work already tracked on the same pytest-xdist worker.
        cls._preexisting_future = concurrent.futures.Future()
        with _background._BACKGROUND_STATE_LOCK:
            _background._BACKGROUND_FUTURES.add(cls._preexisting_future)

    @classmethod
    def tearDownClass(cls):
        if not cls._preexisting_future.done():
            cls._preexisting_future.set_result(None)
        with _background._BACKGROUND_STATE_LOCK:
            _background._BACKGROUND_FUTURES.discard(cls._preexisting_future)

    def setUp(self):
        with _background._BACKGROUND_STATE_LOCK:
            self._background_futures_before = _background._BACKGROUND_FUTURES
            self._background_labels_before = _background._BACKGROUND_FUTURE_LABELS
            _background._BACKGROUND_FUTURES = set()
            _background._BACKGROUND_FUTURE_LABELS = {}
        self.addCleanup(self._restore_background_futures)

    def _restore_background_futures(self):
        with _background._BACKGROUND_STATE_LOCK:
            _background._BACKGROUND_FUTURES = self._background_futures_before
            _background._BACKGROUND_FUTURE_LABELS = self._background_labels_before

    def test_drain_background_jobs_observes_tracked_futures(self):
        future = concurrent.futures.Future()
        with _background._BACKGROUND_STATE_LOCK:
            _background._BACKGROUND_FUTURES.add(future)
        try:
            self.assertFalse(_background.drain_background_jobs(timeout=0.0))
            future.set_result(None)
            self.assertTrue(_background.drain_background_jobs(timeout=0.1))
        finally:
            with _background._BACKGROUND_STATE_LOCK:
                _background._BACKGROUND_FUTURES.discard(future)

    def test_begin_shutdown_blocks_new_dispatch_without_destroying_executors(self):
        old_accepting = _background._BACKGROUND_ACCEPTING
        old_dispatcher = _background._DURABLE_BACKLOG_DISPATCHER
        try:
            _background._BACKGROUND_ACCEPTING = True
            _background._DURABLE_BACKLOG_DISPATCHER = lambda: None
            _m_main.begin_background_shutdown()
            self.assertFalse(_background.background_jobs_accepting())
            self.assertIsNone(_background._DURABLE_BACKLOG_DISPATCHER)
            self.assertFalse(_m_memory_curator.submit_background("tts", lambda: None))
        finally:
            _background._BACKGROUND_ACCEPTING = old_accepting
            _background._DURABLE_BACKLOG_DISPATCHER = old_dispatcher

    def test_executor_for_creates_only_requested_pool_and_reuses_it(self):
        old_generation = _background._GENERATION_EXECUTOR
        old_utility = _background._UTILITY_EXECUTOR
        _background._GENERATION_EXECUTOR = None
        _background._UTILITY_EXECUTOR = None
        try:
            generation = Mock()
            utility = Mock()
            with patch.object(
                concurrent.futures,
                "ThreadPoolExecutor",
                side_effect=[generation, utility],
            ) as constructor:
                first = _background._executor_for("generation")
                second = _background._executor_for("retry")
                third = _background._executor_for("tts")

            self.assertIs(first, generation)
            self.assertIs(second, generation)
            self.assertIs(third, utility)
            self.assertEqual(constructor.call_count, 2)
        finally:
            _background._GENERATION_EXECUTOR = old_generation
            _background._UTILITY_EXECUTOR = old_utility

    def test_memory_enrichment_uses_dedicated_single_worker_pool(self):
        old_generation = _background._GENERATION_EXECUTOR
        old_utility = _background._UTILITY_EXECUTOR
        old_memory = getattr(_background, "_MEMORY_EXECUTOR", None)
        _background._GENERATION_EXECUTOR = None
        _background._UTILITY_EXECUTOR = None
        if hasattr(_background, "_MEMORY_EXECUTOR"):
            _background._MEMORY_EXECUTOR = None
        try:
            utility = Mock()
            memory = Mock()
            with patch.object(
                concurrent.futures,
                "ThreadPoolExecutor",
                side_effect=[utility, memory],
            ) as constructor:
                callback = _background._executor_for("callback")
                hindsight = _background._executor_for("hindsight_retain")
                curator = _background._executor_for("memory_curator")
                scene = _background._executor_for("scene_state_refresh")
                npc = _background._executor_for("npc_state_refresh")

            self.assertIs(callback, utility)
            self.assertIs(hindsight, memory)
            self.assertIs(curator, memory)
            self.assertIs(scene, memory)
            self.assertIs(npc, memory)
            self.assertEqual(constructor.call_count, 2)
            self.assertEqual(constructor.call_args_list[1].kwargs["max_workers"], 1)
        finally:
            _background._GENERATION_EXECUTOR = old_generation
            _background._UTILITY_EXECUTOR = old_utility
            if hasattr(_background, "_MEMORY_EXECUTOR"):
                _background._MEMORY_EXECUTOR = old_memory

    def test_background_task_telemetry_logs_rss_delta_without_payload(self):
        samples = iter([(100, 5), (112, 6)])
        secret = "PRIVATE_BACKGROUND_PAYLOAD"
        with (
            patch.object(_background, "_runtime_process_snapshot", side_effect=lambda: next(samples), create=True),
            patch.object(_background.time, "monotonic", side_effect=[10.0, 10.025]),
            self.assertLogs(level="INFO") as captured,
        ):
            result = _background._run_observed_background("memory_curator", lambda value: value, secret)

        log = "\n".join(captured.output)
        self.assertEqual(result, secret)
        self.assertIn("background_start label=memory_curator rss_kib=100 threads=5", log)
        self.assertIn(
            "background_finish label=memory_curator status=succeeded duration_ms=25 "
            "rss_kib=112 rss_delta_kib=12 threads=6",
            log,
        )
        self.assertNotIn(secret, log)

    def test_background_observability_counters_distinguish_running_and_queued(self):
        running = concurrent.futures.Future()
        queued = concurrent.futures.Future()
        running.set_running_or_notify_cancel()
        with _background._BACKGROUND_STATE_LOCK:
            _background._BACKGROUND_FUTURES.update({running, queued})
            _background._BACKGROUND_FUTURE_LABELS[running] = "hindsight_retain"
            _background._BACKGROUND_FUTURE_LABELS[queued] = "memory_curator"
        try:
            counters = _background.background_observability_counters()
            self.assertEqual(counters["background.active_total"], 2)
            self.assertEqual(counters["background.running_total"], 1)
            self.assertEqual(counters["background.queued_total"], 1)
            self.assertEqual(counters["background.hindsight_retain.running"], 1)
            self.assertEqual(counters["background.memory_curator.queued"], 1)
        finally:
            running.cancel()
            queued.cancel()
            with _background._BACKGROUND_STATE_LOCK:
                _background._BACKGROUND_FUTURES.discard(running)
                _background._BACKGROUND_FUTURES.discard(queued)
                _background._BACKGROUND_FUTURE_LABELS.pop(running, None)
                _background._BACKGROUND_FUTURE_LABELS.pop(queued, None)

    def test_shutdown_timeout_logs_active_labels_and_does_not_wait_forever(self):
        old_generation = _background._GENERATION_EXECUTOR
        old_utility = _background._UTILITY_EXECUTOR
        old_accepting = _background._BACKGROUND_ACCEPTING
        generation = Mock()
        future = concurrent.futures.Future()
        try:
            _background._GENERATION_EXECUTOR = generation
            _background._UTILITY_EXECUTOR = None
            _background._BACKGROUND_ACCEPTING = True
            with _background._BACKGROUND_STATE_LOCK:
                _background._BACKGROUND_FUTURES.add(future)
                _background._BACKGROUND_FUTURE_LABELS[future] = "generation"

            with self.assertLogs(level="WARNING") as captured:
                self.assertFalse(_background.shutdown_background_executors(timeout=0.0))

            generation.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
            self.assertIn("generation", "\n".join(captured.output))
        finally:
            if not future.done():
                future.set_result(None)
            _background._GENERATION_EXECUTOR = old_generation
            _background._UTILITY_EXECUTOR = old_utility
            _background._BACKGROUND_ACCEPTING = old_accepting

    def test_shutdown_clears_instantiated_executors_and_is_repeatable(self):
        old_generation = _background._GENERATION_EXECUTOR
        old_utility = _background._UTILITY_EXECUTOR
        old_accepting = _background._BACKGROUND_ACCEPTING
        generation = Mock()
        utility = Mock()
        try:
            _background._GENERATION_EXECUTOR = generation
            _background._UTILITY_EXECUTOR = utility
            _background._BACKGROUND_ACCEPTING = True

            self.assertTrue(_background.shutdown_background_executors(timeout=0.0))
            self.assertIsNone(_background._GENERATION_EXECUTOR)
            self.assertIsNone(_background._UTILITY_EXECUTOR)
            generation.shutdown.assert_called_once_with(
                wait=True,
                cancel_futures=False,
            )
            utility.shutdown.assert_called_once_with(
                wait=True,
                cancel_futures=False,
            )

            self.assertTrue(_background.shutdown_background_executors(timeout=0.0))
        finally:
            _background._GENERATION_EXECUTOR = old_generation
            _background._UTILITY_EXECUTOR = old_utility
            _background._BACKGROUND_ACCEPTING = old_accepting


if __name__ == "__main__":
    unittest.main()
