#!/bin/bash
# Cron entrypoint — activates venv and runs the digest pipeline.
# Stdout and stderr are both written to data/digest.log by cron.

cd /opt/compliance-digest
source venv/bin/activate
python main.py
