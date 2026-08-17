import io
import math

import yaml


class ConfigError(ValueError):
    pass


def _mapping(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError("%s must be a mapping" % key)
    return value


def _number(parent, key, minimum=0.0, allow_minimum=False):
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s must be numeric" % key)
    number = float(value)
    if not math.isfinite(number) or number < minimum or (number == minimum and not allow_minimum):
        raise ConfigError("%s is outside the allowed range" % key)
    return number


def _integer(parent, key, minimum):
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError("%s must be an integer >= %d" % (key, minimum))
    return value


def load_config(path):
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            root = yaml.safe_load(stream)
    except (IOError, yaml.YAMLError) as exc:
        raise ConfigError("cannot read config %s: %s" % (path, exc))
    if not isinstance(root, dict) or root.get("schema_version") != 1:
        raise ConfigError("config schema_version must be 1")

    registration = _mapping(root, "registration")
    quality = _mapping(root, "quality")
    fusion = _mapping(root, "fusion")
    limits = _mapping(root, "limits")
    enabled = registration.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError("registration.enabled must be boolean")

    source_fitness = _number(quality, "min_source_fitness", 0.0, True)
    target_fitness = _number(quality, "min_target_fitness", 0.0, True)
    if source_fitness > 1.0 or target_fitness > 1.0:
        raise ConfigError("fitness thresholds must not exceed 1")

    return {
        "registration_enabled": enabled,
        "max_sample_points": _integer(registration, "max_sample_points", 3),
        "max_iterations": _integer(registration, "max_iterations", 1),
        "max_correspondence_distance_m": _number(
            registration, "max_correspondence_distance_m"
        ),
        "convergence_translation_m": _number(
            registration, "convergence_translation_m", 0.0, True
        ),
        "convergence_rotation_deg": _number(
            registration, "convergence_rotation_deg", 0.0, True
        ),
        "min_correspondences": _integer(quality, "min_correspondences", 3),
        "min_source_fitness": source_fitness,
        "min_target_fitness": target_fitness,
        "max_inlier_rmse_m": _number(quality, "max_inlier_rmse_m"),
        "max_cycle_translation_error_m": _number(
            quality, "max_cycle_translation_error_m", 0.0, True
        ),
        "max_cycle_rotation_error_deg": _number(
            quality, "max_cycle_rotation_error_deg", 0.0, True
        ),
        "output_voxel_size_m": _number(fusion, "output_voxel_size_m"),
        "max_input_points_per_map": _integer(limits, "max_input_points_per_map", 1),
        "max_output_voxels": _integer(limits, "max_output_voxels", 1),
    }
