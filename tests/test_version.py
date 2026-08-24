import re
import unittest

from ccs_monitor import __version__


class VersionTests(unittest.TestCase):
    def test_version_is_semantic_triplet(self):
        self.assertEqual(__version__, "0.18.3")
        self.assertRegex(__version__, re.compile(r"^\d+\.\d+\.\d+$"))


if __name__ == "__main__":
    unittest.main()
