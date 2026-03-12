#!/bin/bash
# TennisIQ — Overnight Run Teardown
# Restores normal sleep settings after overnight runs.
# Run: bash scripts/overnight_teardown.sh

sudo pmset -a sleep 5
sudo pmset -a disksleep 10
echo "Overnight mode OFF — normal sleep restored"
