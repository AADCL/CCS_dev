import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import yaml
from markdown_it import MarkdownIt

from scripts.release_documentation import document_links, local_target, rewrite_links

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "edge_side_pkg"
REFERENCE = EDGE / "documents/INTERFACE_REFERENCE.md"


def leaf_keys(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaf_keys(child, prefix + "." + key if prefix else key)
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        for child in value:
            yield from leaf_keys(child, prefix + "[]")
    else:
        yield prefix


def heading_ids(text):
    identifiers = set()
    tokens = MarkdownIt().parse(text)
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        children = tokens[index + 1].children or []
        title = "".join(child.content for child in children
                        if child.type in ("text", "code_inline"))
        base = re.sub(r"[^\w\s-]", "", title.lower()).replace(" ", "-")
        slug, suffix = base, 0
        while slug in identifiers:
            suffix += 1
            slug = base + "-" + str(suffix)
        identifiers.add(slug)
    identifiers.update(re.findall(r'id=["\']([^"\']+)["\']', text))
    return identifiers


class EdgeDocumentationTests(unittest.TestCase):
    def test_all_35_yaml_files_have_parameter_coverage(self):
        reference = REFERENCE.read_text(encoding="utf-8")
        count = 0
        for default in (EDGE / "EPGeneral_device_config/config").glob("*.yaml"):
            heading = re.search(r"(?m)^## \d+\. " + re.escape(default.name) + r"$", reference)
            self.assertIsNotNone(heading, default.name)
            end = reference.find("\n## ", heading.end())
            section = reference[heading.end():end if end != -1 else len(reference)]
            documented = set(re.findall(r"`([A-Za-z0-9_.\[\]]+)`", section))
            paths = [default] + list((EDGE / "deploy").glob("*/config/" + default.name))
            self.assertEqual(len(paths), 5, default.name)
            for path in paths:
                count += 1
                config = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertFalse(set(leaf_keys(config)) - documented,
                                 f"{path}: {set(leaf_keys(config)) - documented}")
        self.assertEqual(count, 35)

    def test_all_package_readmes_and_versions_are_navigable(self):
        overview = (EDGE / "README.md").read_text(encoding="utf-8")
        manifests = list(EDGE.glob("*/package.xml"))
        self.assertEqual(len(manifests), 8)
        for manifest in manifests:
            tree = ElementTree.parse(manifest).getroot()
            text = (manifest.parent / "README.md").read_text(encoding="utf-8")
            self.assertIn(tree.findtext("version"), overview)
            self.assertIn("USER_MANUAL.md", text)
            self.assertIn("INTERFACE_REFERENCE.md", text)

    def test_current_documentation_local_links_and_anchors(self):
        paths = [ROOT / "README.md", ROOT / "docs/USER_GUIDE.md",
                 ROOT / "docs/EDGE_DEVICE_INTERFACES.md", ROOT / "docs/RELEASING.md",
                 EDGE / "README.md", REFERENCE, EDGE / "documents/USER_MANUAL.md"]
        paths += list(EDGE.glob("*/README.md"))
        paths += list((EDGE / "deploy").glob("*/DEPLOYMENT.md"))
        paths += list((EDGE / "documents").glob("*_DEPLOYMENT.md"))
        for path in paths:
            source = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            for link in document_links(text):
                parts = urlsplit(link)
                if parts.scheme or parts.netloc:
                    continue
                target = local_target(source, link) or source
                target_path = ROOT / target
                self.assertTrue(target_path.exists(), f"{source}: {link}")
                if parts.fragment and target_path.suffix == ".md":
                    self.assertIn(unquote(parts.fragment),
                                  heading_ids(target_path.read_text(encoding="utf-8")),
                                  f"{source}: {link}")

    def test_script_environment_and_public_launch_arguments_are_described(self):
        text = REFERENCE.read_text(encoding="utf-8")
        for script in (EDGE / "deploy").glob("*/start_ccs_edge_dev.sh"):
            for name in set(re.findall(r"\$\{(CCS_[A-Z_]+)", script.read_text(encoding="utf-8"))):
                self.assertIn(name, text, str(script))
        for launch in EDGE.glob("*/launch/*.launch"):
            for arg in ElementTree.parse(launch).getroot().findall("arg"):
                self.assertIn(arg.attrib["name"], text, str(launch))


class ReleaseDocumentationLinkTests(unittest.TestCase):
    def test_relocates_inline_images_titles_and_reference_links(self):
        text = '[Guide](../edge_side_pkg/documents/USER_MANUAL.md#start "Manual")\n'
        text += '[API][api]\n\n[api]: <../edge_side_pkg/documents/INTERFACE_REFERENCE.md>\n'
        text += '![Icon](../icons/app_icons/a.svg)\n<img src="../icons/app_icons/a.svg">\n'
        mapping = {
            "edge_side_pkg/documents/USER_MANUAL.md": "docs/edge/documents/USER_MANUAL.md",
            "edge_side_pkg/documents/INTERFACE_REFERENCE.md": "docs/edge/documents/INTERFACE_REFERENCE.md",
            "icons/app_icons/a.svg": "_internal/icons/app_icons/a.svg",
        }
        result = rewrite_links(text, "docs/USER_GUIDE.md", "docs/USER_GUIDE.md",
                               mapping, "https://example.test/blob/tag/")
        links = document_links(result)
        self.assertIn("edge/documents/USER_MANUAL.md#start", links)
        self.assertIn("edge/documents/INTERFACE_REFERENCE.md", links)
        self.assertEqual(links.count("../_internal/icons/app_icons/a.svg"), 2)

    def test_omitted_sources_use_versioned_url_and_code_is_untouched(self):
        text = '[Source](../src/node.py)\n~~~bash\n[Source](../src/node.py)\n~~~\n'
        text += '`[Source](../src/node.py)`\n'
        result = rewrite_links(text, "docs/README.md", "docs/README.md", {},
                               "https://example.test/blob/pre-release-v0.23.1/")
        self.assertIn("[Source](https://example.test/blob/pre-release-v0.23.1/src/node.py)", result)
        self.assertIn("~~~bash\n[Source](../src/node.py)\n~~~", result)
        self.assertIn("`[Source](../src/node.py)`", result)


if __name__ == "__main__":
    unittest.main()
