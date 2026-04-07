#!/bin/bash
cd /root/projects/compliance-digest
source venv/bin/activate
if ! command -v python &>/dev/null; then
    echo "ERROR: python not found after venv activate" | mail -s "compliance-digest BROKEN" stifor96@gmail.com 2>/dev/null || true
    exit 1
fi
python analyzer.py monthly >> data/digest.log 2>&1
