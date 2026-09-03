ARG UBUNTU_IMAGE=ubuntu:20.04
FROM ${UBUNTU_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=20 update && \
    apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=20 install -y --no-install-recommends \
    ca-certificates curl binutils build-essential xz-utils tar \
    libgl1 libegl1 libopengl0 libglib2.0-0 libdbus-1-3 libfontconfig1 \
    libx11-6 libx11-xcb1 libxext6 libxi6 libxrender1 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xinerama0 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
    libsm6 libice6 libgomp1 libusb-1.0-0 fonts-noto-cjk xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
ADD https://astral.sh/uv/0.12.9/install.sh /tmp/install-uv.sh
RUN sh /tmp/install-uv.sh && /root/.local/bin/uv python install 3.10.19
ENV PATH="/root/.local/bin:$PATH" QT_QPA_PLATFORM=offscreen
WORKDIR /workspace
# Mount source read-only at /source; copy just build inputs into container storage.
CMD ["bash", "-lc", "mkdir -p /workspace && cp -a /source/ccs_monitor /source/config /source/icons /source/examples /source/edge_side_pkg /source/docs /source/release /source/scripts /workspace/ && cp /source/run.py /source/pyproject.toml /source/uv.lock /source/.python-version /source/requirements.txt /source/README.md /source/需求分析.md /source/CHANGELOG.md /source/LICENSE /workspace/ && uv sync --locked --group release && uv run --no-sync python scripts/build_release.py --target all && cp /workspace/dist/* /output/"]
