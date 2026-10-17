#!/bin/sh
# Install as ~/.local/bin/wordcut; used by the desktop entries.
set -eu
url="http://127.0.0.1:${WORDCUT_PORT:-18447}/"
case "${1:-start}" in
 stop) systemctl --user stop wordcut.service ;;
 start)
  systemctl --user start wordcut.service
  /usr/bin/python3 - "$url" <<'PY'
import sys,time,urllib.request
for _ in range(40):
 try:
  urllib.request.urlopen(sys.argv[1]+'healthz',timeout=.5);break
 except OSError:time.sleep(.25)
else:raise SystemExit('Wordcut did not start. Check wordcut.service.')
PY
  xdg-open "$url"
  ;;
 *) echo 'Usage: wordcut [start|stop]' >&2;exit 2 ;;
esac
