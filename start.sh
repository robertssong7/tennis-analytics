#!/bin/sh
pip3 install -r requirements.txt
python3 -m uvicorn src.api.main:app --host 0.0.0.0 --port 8080
