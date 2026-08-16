import subprocess
import unittest

from ccs_monitor.ping_service import build_ping_command, ping_ip


class PingServiceTests(unittest.TestCase):
    def test_windows_command(self):
        self.assertEqual(
            build_ping_command("127.0.0.1", "win32"),
            ["ping", "-n", "1", "-w", "1500", "127.0.0.1"],
        )

    def test_unix_ipv6_command(self):
        self.assertEqual(
            build_ping_command("::1", "linux"),
            ["ping", "-6", "-c", "1", "-W", "2", "::1"],
        )

    def test_success_and_failure(self):
        success = ping_ip("127.0.0.1", runner=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0))
        failure = ping_ip("127.0.0.1", runner=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1))
        self.assertTrue(success.reachable)
        self.assertFalse(failure.reachable)

    def test_timeout(self):
        def timeout_runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 3)

        self.assertFalse(ping_ip("127.0.0.1", runner=timeout_runner).reachable)

    def test_invalid_ip_never_calls_runner(self):
        def unexpected_runner(*args, **kwargs):
            raise AssertionError("runner should not be called")

        result = ping_ip("example.com", runner=unexpected_runner)
        self.assertFalse(result.reachable)


if __name__ == "__main__":
    unittest.main()

