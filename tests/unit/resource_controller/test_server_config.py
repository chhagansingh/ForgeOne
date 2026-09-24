"""Protected server configuration -- cache-budget enforcement and argv safety.

No server is started and no model is loaded; these are static validations of the
command line the controller would build.
"""

from __future__ import annotations

import unittest

from services.resource_controller.server_config import (
    CacheBudget,
    ProtectedServerConfig,
    ServerConfigError,
    UnsupportedServingPath,
    UnsupportedServerVersion,
    serving_path_is_bounded,
    supported_flags_from_help,
    verify_server_support,
)

from .support import GB

PY = "/usr/bin/python3"

#: Real --help excerpt shape from mlx-lm 0.31.3.
MLX_HELP = """
  --model MODEL
  --host HOST
  --port PORT
  --prompt-cache-size PROMPT_CACHE_SIZE
  --prompt-cache-bytes PROMPT_CACHE_BYTES
  --seed SEED
"""


def make_cache(**kw):
    base = dict(
        retained_cache_bytes=2 * GB,
        max_sequences=8,
        active_kv_bytes=int(2.5 * GB),
        weights_bytes=int(2.2 * GB),
        transient_reserve_bytes=int(2.0 * GB),
    )
    base.update(kw)
    return CacheBudget(**base)


def make_config(**kw):
    base = dict(
        executable=PY,
        model="mlx-community/Qwen3-4B-Instruct-2507-4bit",
        port=8082,
        cache=make_cache(),
    )
    base.update(kw)
    return ProtectedServerConfig(**base)


class CacheBudgetTests(unittest.TestCase):
    def test_explicit_byte_budget_is_required(self):
        """Case 4: a zero byte budget is not a budget."""
        with self.assertRaises(ServerConfigError) as ctx:
            make_cache(retained_cache_bytes=0)
        self.assertIn("retained_cache_bytes", str(ctx.exception))

    def test_explicit_sequence_budget_is_required(self):
        """Case 4: a count limit alone is not a memory limit."""
        with self.assertRaises(ServerConfigError):
            make_cache(max_sequences=0)

    def test_cache_is_bounded_only_when_both_present(self):
        self.assertTrue(make_cache().cache_is_bounded)

    def test_cache_and_active_allocations_are_distinct(self):
        """Case 22: the cache ceiling must not be mistaken for a total ceiling.

        A retained-cache ceiling says nothing about active KV, weights or
        transient prefill. Changing the cache budget must leave those three
        untouched, and the cache must not be silently folded into their total.
        """
        b = make_cache()
        self.assertEqual(
            b.total_separately_accounted_bytes(),
            b.active_kv_bytes + b.weights_bytes + b.transient_reserve_bytes,
        )

        # The retained cache is a separate budget, not part of that total.
        self.assertNotEqual(b.total_separately_accounted_bytes(), b.retained_cache_bytes)
        self.assertGreater(
            b.total_separately_accounted_bytes() + b.retained_cache_bytes,
            b.total_separately_accounted_bytes(),
        )

        # Changing the cache budget must not move the other three.
        b2 = make_cache(retained_cache_bytes=1 * GB)
        self.assertEqual(
            b2.total_separately_accounted_bytes(), b.total_separately_accounted_bytes()
        )
        self.assertNotEqual(b2.retained_cache_bytes, b.retained_cache_bytes)

    def test_negative_values_rejected(self):
        for field in ("retained_cache_bytes", "max_sequences", "active_kv_bytes",
                      "weights_bytes", "transient_reserve_bytes"):
            with self.subTest(field=field):
                with self.assertRaises(ServerConfigError):
                    make_cache(**{field: -1})


class ProtectedServerConfigTests(unittest.TestCase):
    def test_correct_argv_is_built(self):
        """Case 6: argv must carry both cache flags explicitly."""
        argv = make_config().build_argv()
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], PY)
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "mlx_lm.server")
        self.assertIn("--prompt-cache-bytes", argv)
        self.assertEqual(argv[argv.index("--prompt-cache-bytes") + 1], str(2 * GB))
        self.assertIn("--prompt-cache-size", argv)
        self.assertEqual(argv[argv.index("--prompt-cache-size") + 1], "8")
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")

    def test_argv_is_a_list_not_a_shell_string(self):
        argv = make_config().build_argv()
        self.assertTrue(all(isinstance(a, str) for a in argv))
        self.assertFalse(any(" " in a and a.startswith("-") for a in argv))

    def test_non_loopback_binding_rejected(self):
        """Case 13."""
        for host in ("0.0.0.0", "192.168.1.5", "::"):
            with self.subTest(host=host):
                with self.assertRaises(ServerConfigError):
                    make_config(host=host)

    def test_unapproved_executable_rejected(self):
        with self.assertRaises(ServerConfigError):
            make_config(executable="/bin/sh")
        with self.assertRaises(ServerConfigError):
            make_config(executable="bash")

    def test_port_range_validated(self):
        for port in (0, 80, 70000, "8082"):
            with self.subTest(port=port):
                with self.assertRaises(ServerConfigError):
                    make_config(port=port)

    def test_unbounded_serving_path_rejected(self):
        """Case 5: --seed escapes the byte ceiling (server.py:685-686)."""
        with self.assertRaises(UnsupportedServingPath) as ctx:
            make_config(extra_args=("--seed", "42"))
        self.assertIn("non-batched", str(ctx.exception))

    def test_seed_equals_form_also_rejected(self):
        with self.assertRaises(UnsupportedServingPath):
            make_config(extra_args=("--seed=42",))

    def test_unbounded_path_can_only_be_opted_into_explicitly(self):
        cfg = make_config(extra_args=("--seed", "42"), allow_unbounded_serving_path=True)
        self.assertIn("--seed", cfg.build_argv())
        self.assertTrue(cfg.as_dict()["unbounded_path_allowed"])

    def test_newline_and_nul_rejected(self):
        with self.assertRaises(ServerConfigError):
            make_config(extra_args=("--temp\n--host",))
        with self.assertRaises(ServerConfigError):
            make_config(extra_args=("--temp\x00",))


class ServingPathTests(unittest.TestCase):
    def test_bounded_path_reported(self):
        ok, why = serving_path_is_bounded(make_config().build_argv())
        self.assertTrue(ok, why)

    def test_seed_makes_path_unbounded(self):
        ok, why = serving_path_is_bounded(["--seed", "1", "--prompt-cache-bytes", "100"])
        self.assertFalse(ok)
        self.assertIn("--seed", why)

    def test_missing_byte_flag_makes_path_unbounded(self):
        ok, why = serving_path_is_bounded(["--host", "127.0.0.1"])
        self.assertFalse(ok)
        self.assertIn("--prompt-cache-bytes", why)


class VersionSupportTests(unittest.TestCase):
    def test_help_parsing_finds_flags(self):
        flags = supported_flags_from_help(MLX_HELP)
        self.assertIn("--prompt-cache-bytes", flags)
        self.assertIn("--prompt-cache-size", flags)

    def test_installed_server_supports_required_flags(self):
        verify_server_support(MLX_HELP)  # must not raise

    def test_missing_flag_fails_closed(self):
        """Case 5: unsupported cache enforcement must block, not degrade."""
        old_help = "  --model MODEL\n  --host HOST\n  --port PORT\n"
        with self.assertRaises(UnsupportedServerVersion) as ctx:
            verify_server_support(old_help)
        self.assertIn("--prompt-cache-bytes", str(ctx.exception))

    def test_non_string_help_rejected(self):
        with self.assertRaises(ServerConfigError):
            supported_flags_from_help(None)  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
