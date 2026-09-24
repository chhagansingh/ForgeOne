"""Port probing -- the netstat + bind lesson from the P0 incident."""

from __future__ import annotations

import socket
import unittest

from services.resource_controller.ports import (
    PortStatus,
    PortUnavailable,
    RealPortProbe,
    StaticPortProbe,
    require_free_port,
)


def free_high_port() -> int:
    """Ask the OS for an unused port, then release it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class StaticProbeTests(unittest.TestCase):
    def test_occupied_port_raises(self):
        """Case 12: an occupied port must block startup."""
        probe = StaticPortProbe(free=False, detail="held by another process")
        with self.assertRaises(PortUnavailable) as ctx:
            require_free_port(probe, "127.0.0.1", 8082)
        self.assertIn("not available", str(ctx.exception))

    def test_free_port_passes_and_reports(self):
        probe = StaticPortProbe(free=True)
        status = require_free_port(probe, "127.0.0.1", 8082)
        self.assertIsInstance(status, PortStatus)
        self.assertFalse(status.occupied)
        self.assertEqual(probe.calls, [("127.0.0.1", 8082)])


class RealProbeTests(unittest.TestCase):
    def test_free_port_detected(self):
        probe = RealPortProbe()
        status = probe.check("127.0.0.1", free_high_port())
        self.assertTrue(status.free, status.detail)
        self.assertTrue(status.bind_ok)

    def test_occupied_port_detected_by_bind(self):
        """A real listener must be reported as occupied."""
        port = free_high_port()
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
        try:
            status = RealPortProbe().check("127.0.0.1", port)
            self.assertFalse(status.free, "an actively bound port must not read as free")
            self.assertFalse(status.bind_ok)
            with self.assertRaises(PortUnavailable):
                require_free_port(RealPortProbe(), "127.0.0.1", port)
        finally:
            holder.close()

    def test_netstat_unavailable_fails_closed(self):
        """If one signal cannot be obtained, refuse rather than assume free."""

        class Boom:
            def __call__(self, *a, **kw):
                raise OSError("netstat unavailable")

        status = RealPortProbe(runner=Boom()).check("127.0.0.1", free_high_port())
        self.assertFalse(status.free)
        self.assertIn("fail closed", status.detail)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
