# Issue #33: Inventory counts go stale during flash sales

## Problem
During flash sales the inventory shown to users was wrong for up to a few
minutes, causing oversells.

## Root cause
The Redis cache TTL for inventory was set to 300s. That is far too long for
high-velocity SKUs.

## Resolution
Gotcha to remember: **inventory cache TTL must stay under 60 seconds**, and
hot SKUs should be invalidated on write. Do not raise this TTL for "performance"
without accounting for oversell risk.
