#!/usr/bin/env bash
# Vendor portable libs into the wheel. Compositor libraries stay on the host.
set -euo pipefail
dest_dir=$1
wheel=$2
exec auditwheel repair \
  --exclude libwayland-client.so.0 \
  --exclude libwayland-cursor.so.0 \
  --exclude libwayland-egl.so.0 \
  --exclude libxkbcommon.so.0 \
  --exclude libxkbcommon-x11.so.0 \
  --exclude libX11.so.6 \
  --exclude libX11-xcb.so.1 \
  --exclude libXcursor.so.1 \
  --exclude libXrandr.so.2 \
  --exclude libXi.so.6 \
  --exclude libxcb.so.1 \
  -w "$dest_dir" \
  "$wheel"
