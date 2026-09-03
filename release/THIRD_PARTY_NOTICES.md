# Third-party components

CCS source code is distributed under the Apache License 2.0 in LICENSE.

The frozen installer contains a CPython runtime, PySide6/Qt, NumPy, VisPy,
Open3D, amqtt and their dependencies. The exact installed distribution versions
and the license metadata collected during the build are in
licenses/python-packages.json. License files distributed with the wheels are
copied to licenses/python/. The software retains the individual upstream
licenses; the CCS license does not replace them.

FFmpeg is a separate executable. Its exact source, version, archive checksum,
and upstream distribution notices are in tools/ffmpeg/build-input.json and
tools/ffmpeg/licenses/. Windows uses the Gyan essentials build (GPLv3);
Linux uses BtbN's LGPL variant. Build definitions and source information:
https://www.gyan.dev/ffmpeg/builds/
https://github.com/BtbN/FFmpeg-Builds
https://ffmpeg.org/download.html

Portable and edge archives do not embed these binary runtimes. Their dependency
installation obtains the corresponding upstream distributions and notices.

When redistributing a release, retain these files and the corresponding source
and build information for all bundled third-party components.
