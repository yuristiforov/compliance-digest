#!/bin/bash
# Cron entrypoint — activates venv and runs the digest pipeline.
# Stdout and stderr are both written to data/digest.log by cron.

cd /root/compliance-digest
source venv/bin/activate
python main.py >> data/digest.log 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "Compliance Digest FAILED on $(date) with exit code $EXIT_CODE. Check /root/compliance-digest/data/digest.log" | \
    mail -s "Compliance Digest -- run failed" stifor96@gmail.com 2>/dev/null || true
else
    echo "$(date -u '+%Y-%m-%d %H:%M UTC') -- OK" >> /root/compliance-digest/data/heartbeat.log
fi
