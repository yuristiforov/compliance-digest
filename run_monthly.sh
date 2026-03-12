#!/bin/bash
cd /root/compliance-digest
source venv/bin/activate
python analyzer.py monthly >> data/digest.log 2>&1
