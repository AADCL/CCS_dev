# Validation image only: no Python, uv or system FFmpeg.
ARG UBUNTU_IMAGE=ubuntu:22.04
FROM ${UBUNTU_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=20 update && \
    apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=20 install -y --no-install-recommends \
    ca-certificates libgl1 libegl1 libopengl0 libglib2.0-0 libdbus-1-3 libfontconfig1 \
    libx11-6 libx11-xcb1 libxext6 libxi6 libxrender1 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xinerama0 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
    libsm6 libice6 libgomp1 libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
ENV QT_QPA_PLATFORM=offscreen
CMD ["bash", "/validation/test_linux_installer.sh"]
