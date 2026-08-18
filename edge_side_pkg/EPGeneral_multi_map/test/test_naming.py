import os
import unittest


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PACKAGE))
MISSPELLING_BYTES = bytes((109, 117, 116, 105))
MISSPELLING = MISSPELLING_BYTES.decode("ascii")


class NamingTests(unittest.TestCase):
    def test_only_multi_spelling_remains_in_project_sources(self):
        offenders = []
        for folder, directories, files in os.walk(ROOT):
            for name in files:
                path = os.path.join(folder, name)
                relative = os.path.relpath(path, ROOT)
                if MISSPELLING in relative.lower():
                    offenders.append(relative)
                    continue
                try:
                    with open(path, "rb") as stream:
                        if MISSPELLING_BYTES in stream.read().lower():
                            offenders.append(relative)
                except OSError:
                    continue
        self.assertEqual(offenders, [])
        self.assertEqual(os.path.basename(PACKAGE), "EPGeneral_multi_map")
        for relative in (
                "config/multi_mapping.yaml",
                "launch/epgeneral_multi_map.launch",
                "scripts/epgeneral_multi_map_node.py",
                "src/epgeneral_multi_map/__init__.py"):
            self.assertTrue(os.path.isfile(os.path.join(PACKAGE, *relative.split("/"))))


if __name__ == "__main__":
    unittest.main()
