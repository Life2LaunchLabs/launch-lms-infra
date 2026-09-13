"""Keep the orchestrator and evidence uploader together through restart/shutdown."""
import os
from pathlib import Path
import signal
import subprocess
import time

children = []
stopping = False


def stop(*_):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    children.append(subprocess.Popen(['python3', '/opt/symphony/review_gate.py']))
    children.append(subprocess.Popen(['symphony', '--i-understand-that-this-will-be-running-without-the-usual-guardrails',
                                     '--logs-root', str(Path.home() / 'logs'), str(Path.home() / 'WORKFLOW.md')]))
    while not stopping and all(child.poll() is None for child in children):
        time.sleep(1)
finally:
    requested_stop = stopping
    stop()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
raise SystemExit(0 if requested_stop else 1)
