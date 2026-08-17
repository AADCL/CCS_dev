import os

import numpy as np


class PcdError(ValueError):
    pass


_NUMPY_TYPES = {
    ("F", 4): "<f4",
    ("F", 8): "<f8",
    ("I", 1): "<i1",
    ("I", 2): "<i2",
    ("I", 4): "<i4",
    ("I", 8): "<i8",
    ("U", 1): "<u1",
    ("U", 2): "<u2",
    ("U", 4): "<u4",
    ("U", 8): "<u8",
}


def _read_header(handle):
    header = {}
    while True:
        raw = handle.readline()
        if not raw:
            raise PcdError("PCD header is missing DATA")
        try:
            line = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise PcdError("PCD header must be ASCII") from exc
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        key = parts[0].upper()
        header[key] = parts[1:]
        if key == "DATA":
            break

    try:
        fields = header["FIELDS"]
        sizes = [int(value) for value in header["SIZE"]]
        types = [value.upper() for value in header["TYPE"]]
        counts = [int(value) for value in header.get("COUNT", ["1"] * len(fields))]
        points = int(header.get("POINTS", [str(int(header["WIDTH"][0]) * int(header["HEIGHT"][0]))])[0])
        data_kind = header["DATA"][0].lower()
    except (KeyError, IndexError, ValueError) as exc:
        raise PcdError("PCD header is incomplete or invalid") from exc
    if not fields or len(set(fields)) != len(fields):
        raise PcdError("PCD FIELDS must be unique")
    if not (len(fields) == len(sizes) == len(types) == len(counts)):
        raise PcdError("PCD FIELDS/SIZE/TYPE/COUNT lengths differ")
    if any(count < 1 for count in counts) or points < 1:
        raise PcdError("PCD point and COUNT values must be positive")
    try:
        xyz_fields = [fields.index(name) for name in ("x", "y", "z")]
    except ValueError as exc:
        raise PcdError("PCD FIELDS must contain x, y and z") from exc
    if any(counts[index] != 1 for index in xyz_fields):
        raise PcdError("PCD x, y and z fields must each have COUNT 1")
    return fields, sizes, types, counts, points, data_kind, xyz_fields


def load_xyz(path, max_points):
    try:
        with open(path, "rb") as handle:
            fields, sizes, types, counts, points, data_kind, xyz_fields = _read_header(handle)
            if points > max_points:
                raise PcdError("PCD point count %d exceeds limit %d" % (points, max_points))
            if data_kind == "ascii":
                array = _load_ascii(handle, counts, points, xyz_fields)
            elif data_kind == "binary":
                array = _load_binary(handle, fields, sizes, types, counts, points)
            else:
                raise PcdError("PCD DATA %s is unsupported; use ascii or binary" % data_kind)
    except PcdError:
        raise
    except OSError as exc:
        raise PcdError("cannot read PCD %s: %s" % (path, exc))
    if array.shape != (points, 3) or not np.isfinite(array).all():
        raise PcdError("PCD must contain exactly %d finite XYZ points" % points)
    return array.astype(np.float64, copy=False)


def _load_ascii(handle, counts, points, xyz_fields):
    try:
        rows = np.loadtxt(handle, dtype=np.float64, ndmin=2)
    except (ValueError, UnicodeError) as exc:
        raise PcdError("invalid ASCII PCD data") from exc
    columns = sum(counts)
    if rows.shape != (points, columns):
        raise PcdError("ASCII PCD row/column count does not match header")
    offsets = np.cumsum([0] + counts[:-1])
    return rows[:, [int(offsets[index]) for index in xyz_fields]]


def _load_binary(handle, fields, sizes, types, counts, points):
    dtype_fields = []
    for name, size, kind, count in zip(fields, sizes, types, counts):
        numpy_type = _NUMPY_TYPES.get((kind, size))
        if numpy_type is None:
            raise PcdError("unsupported PCD field type %s/%s" % (kind, size))
        dtype_fields.append((name, numpy_type) if count == 1 else (name, numpy_type, (count,)))
    dtype = np.dtype(dtype_fields)
    raw = handle.read()
    expected = dtype.itemsize * points
    if len(raw) != expected:
        raise PcdError("binary PCD byte count does not match header")
    records = np.frombuffer(raw, dtype=dtype, count=points)
    return np.column_stack([records[name] for name in ("x", "y", "z")])


def write_binary_xyz(path, points):
    array = np.asarray(points, dtype="<f4")
    if array.ndim != 2 or array.shape[1] != 3 or len(array) == 0 or not np.isfinite(array).all():
        raise PcdError("PCD output requires non-empty finite XYZ points")
    header = (
        "VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
        "WIDTH %d\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS %d\nDATA binary\n"
        % (len(array), len(array))
    ).encode("ascii")
    try:
        with open(path, "wb") as handle:
            handle.write(header)
            handle.write(array.tobytes(order="C"))
    except OSError as exc:
        raise PcdError("cannot write PCD %s: %s" % (path, exc))
    return os.path.abspath(path)
