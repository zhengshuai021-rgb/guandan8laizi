#!/bin/bash
# 八癞子掼蛋 Web UI 启动脚本
cd "$(dirname "$0")"
PIDFILE="./web_server.pid"
LOGFILE="./web_server.log"

if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
    echo "Server already running (PID $(cat $PIDFILE))"
    exit 0
fi

nohup python3 web_server.py >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "Started 八癞子掼蛋 Web UI (PID $!)"
echo "Access: https://kamika.info/guandan8laizi/"
