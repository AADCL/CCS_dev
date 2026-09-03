#!/usr/bin/env bash
set -euo pipefail
export HOME=/tmp/ccs-user
export PATH=/usr/bin:/bin
export QT_QPA_PLATFORM=offscreen
mkdir -p "$HOME"
app="$HOME/安装 CCS"
installer=/release/CCS-0.23.0-linux-x64.run
for command in python python3 uv ffmpeg; do
    if command -v "$command"; then echo "Unexpected host runtime: $command" >&2; exit 1; fi
done
bash "$installer" --prefix "$app" --yes
test -x "$app/CCS"
test -f "$app/_internal/ccs_monitor/assets/fonts/NotoSansCJK-Regular.ttc"
printf '\n ' >> "$app/config/devices.json"
cp "$app/config/devices.json" "$HOME/config-before.json"
printf 'retain me\n' > "$app/data/keep-me.txt"
bash "$installer" --prefix "$app" --yes
cmp "$HOME/config-before.json" "$app/config/devices.json"
grep -q 'retain me' "$app/data/keep-me.txt"
for case_name in probe gui builtin numpy ransac open3d; do
    "$app/ccs-map-fusion-worker" "/validation/$case_name.json" "$HOME/$case_name-result.json"
    test -s "$HOME/$case_name.pcd"
    cat "$HOME/$case_name-result.json"
    printf '\n'
done
ffmpeg="$app/tools/ffmpeg/bin/ffmpeg"
"$ffmpeg" -hide_banner -loglevel error -re -stream_loop -1 -i /validation/srt_h264.ts -c copy -f mpegts \
    'srt://127.0.0.1:38007?mode=listener&transtype=live&latency=120000' > "$HOME/sender.log" 2>&1 &
sender=$!
trap 'kill "$sender" 2>/dev/null || true' EXIT
sleep 0.5
timeout 20 "$ffmpeg" -hide_banner -loglevel error -probesize 32768 -analyzeduration 200000 \
    -i 'srt://127.0.0.1:38007?mode=caller&transtype=live&latency=120000' \
    -frames:v 1 -f rawvideo -pix_fmt rgb24 "$HOME/frame.rgb"
test "$(wc -c < "$HOME/frame.rgb")" = 9216
kill "$sender" 2>/dev/null || true
wait "$sender" 2>/dev/null || true
trap - EXIT
bash "$app/uninstall.sh" --yes
test ! -e "$app/CCS"
cmp "$HOME/config-before.json" "$app/config/devices.json"
grep -q 'retain me' "$app/data/keep-me.txt"
bash "$installer" --prefix "$app" --yes
cmp "$HOME/config-before.json" "$app/config/devices.json"
"$app/ccs-map-fusion-worker" /validation/builtin.json "$HOME/reinstalled-result.json"
bash "$app/uninstall.sh" --yes
echo 'PASS: offline install, upgrade, frozen GUI/plugins/MQTT/SRT, uninstall and reinstall preserve data.'
