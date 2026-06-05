#!/bin/bash
cd "$(dirname "$0")"
npx vite --port 5899 --host 127.0.0.1 > vite.log 2>&1 &
echo $! > vite.pid
sleep 3
echo "Vite started on http://127.0.0.1:5899"
