#!/bin/bash
# TennisIQ — Overnight Run Setup
# Prevents laptop sleep during long agent loop runs.
# Run: bash scripts/overnight_setup.sh

sudo pmset -a sleep 0
sudo pmset -a disksleep 0
echo "Overnight mode ON — laptop will not sleep"
echo "Remember: plug in charger, place on hard flat surface, close all apps except terminal"
