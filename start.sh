#!/bin/bash
cd "$(dirname "$0")"

PIDFILE=".bot.pid"
BIN=.venv/bin/python
LOGDIR=log
mkdir -p "$LOGDIR"
LOGFILE="${LOGDIR}/claude_bot.$(date +%Y%m%d)"

# Kill existing process
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing bot (pid=$OLD_PID)..."
        kill "$OLD_PID"
        sleep 2
        kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID"
    fi
    rm -f "$PIDFILE"
fi

# Also kill any orphan bot.py processes
pkill -f "${BIN} bot.py" 2>/dev/null

nohup ${BIN} bot.py >>"$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "Bot started (pid=$!, log=$LOGFILE)"
