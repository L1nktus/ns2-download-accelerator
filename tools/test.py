#!/usr/bin/env python3
"""Tests use temporary files/local sockets. No real launch agent or Switch changes."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--integration',action='store_true');args=p.parse_args()
env=dict(os.environ);env['PYTHONPATH']=str(ROOT/'src/backend')+os.pathsep+str(ROOT/'tests')
subprocess.run([sys.executable,'-m','unittest','discover','-s',str(ROOT/'tests'),'-p','test_*.py','-v'],env=env,check=True)
if args.integration:subprocess.run([sys.executable,str(ROOT/'tests/integration_recovery.py')],env=env,check=True,timeout=50)
