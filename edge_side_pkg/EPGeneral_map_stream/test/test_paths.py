import os


def device_config_path(package_root):
    parent = os.path.dirname(package_root)
    for directory in ("epgeneral_device_config", "EPGeneral_device_config"):
        candidate = os.path.join(parent, directory, "config", "device.yaml")
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(parent, "epgeneral_device_config", "config", "device.yaml")
