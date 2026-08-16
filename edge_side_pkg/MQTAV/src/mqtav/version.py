"""Read the package version from the canonical ROS package manifest."""

from pathlib import Path
from xml.etree import ElementTree


def _version_from_xml(path):
    root = ElementTree.parse(str(path)).getroot()
    version = root.findtext("version")
    if not version:
        raise RuntimeError("package.xml does not contain a version")
    return version.strip()


def get_version(package_xml=None):
    """Return the version from package.xml without maintaining a Python copy."""
    if package_xml is not None:
        return _version_from_xml(Path(package_xml))

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "package.xml"
        if candidate.is_file():
            return _version_from_xml(candidate)

    try:
        import rospkg

        return _version_from_xml(Path(rospkg.RosPack().get_path("mqtav")) / "package.xml")
    except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError("cannot locate mqtav package.xml") from exc
