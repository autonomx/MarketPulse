# Omnix Trading Charting Spike Benchmark

## Purpose

This document defines the repeatable evidence required to accept or reject the chart renderer and drawing interaction approach before production `/trading` work begins.

## Environment record

Every run records:

- commit SHA;
- operating system and browser version;
- CPU, memory, GPU, and display scale;
- Node and package versions;
- chart renderer version;
- dataset generator seed;
- number of charts, bars, indicators, drawings, reconnect cycles, and workspace switches.

## Required scenario

- four charts;
- 5,000 deterministic bars per chart;
- candlestick price series and volume;
- RSI pane;
- crosshair synchronization across at least two intervals;
- synchronized visible ranges;
- one horizontal line;
- one selectable, draggable, and resizable trend line using time/price coordinates;
- simulated partial bars, corrections, duplicates, out-of-order events, disconnect, reconnect, and exact historical gap recovery;
- 50 workspace mount/unmount cycles.

## Measurements

- crosshair propagation p50, p95, and maximum duration;
- visible-range propagation p95;
- initial chart creation time;
- ordinary finalized-bar update time;
- partial-bar update time;
- retained heap before and after workspace cycling;
- active chart, series, observer, listener, and upstream-subscription counts after cleanup;
- missing and duplicate finalized-bar counts after ten reconnect cycles;
- drawing coordinate drift after resize, zoom, pan, reload, and interval switch.

## Initial acceptance targets

- crosshair propagation p95 below 32 ms;
- ordinary updates do not recreate charts;
- no finalized-bar duplicates after ten reconnect cycles;
- exact recovery of every simulated missing finalized bar;
- one upstream subscription for identical chart bindings;
- less than 10% retained-heap growth after 50 workspace switches, unless the recorded baseline justifies a revised threshold;
- zero live chart, observer, listener, and subscription references after unmount;
- no drawing time/price coordinate drift;
- no unhandled exceptions for missing timestamps or different bar densities.

## CI evidence versus local evidence

Provider-free CI validates deterministic data generation, RSI fixtures, gap reconciliation, synchronization guards, drawing coordinate commands, lifecycle cleanup contracts, and architecture boundaries. Browser performance and heap evidence is produced by the local benchmark harness and committed as a JSON artifact before ADR acceptance.

## Current evidence

Status: **pending local benchmark execution**.

The experimental spike and deterministic tests may land before the benchmark result. Production routing remains blocked until this section records a measured result and ADR-0004 is amended to either accept Lightweight Charts or select an alternative.
