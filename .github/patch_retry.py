#!/usr/bin/env python3
import os
import re
import sys

CANDIDATES = [
    "LiquidAssBackboardd/Tweak.mm",
    "LiquidAssBackboardd/Tweak.x",
    "Sources/Tweak.mm",
    "Tweak.mm",
    "Tweak.x",
]

PATH = None
for p in CANDIDATES:
    if os.path.exists(p):
        PATH = p
        break

if PATH is None:
    print("SKIP: no Tweak file found")
    sys.exit(0)

with open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

if "registerCustomFilter" not in src:
    print(f"SKIP: {PATH} has no registerCustomFilter")
    sys.exit(0)

if "g_cachedFilterTable" in src:
    print(f"SKIP: {PATH} already patched")
    sys.exit(0)

pattern = re.compile(
    r'if\s*\(\s*!\s*\*\s*filterTableSlot\s*\)\s*\{.*?'
    r'registerCustomFilter\s*\(\s*\)\s*;.*?'
    r'return\s+false\s*;\s*\}',
    re.DOTALL,
)

replacement = '''if (!*filterTableSlot) {
        static int sRetry = 0;
        static const int kMaxRetries = 5;
        if (sRetry < kMaxRetries) {
            sRetry++;
            lglog("registerCustomFilter: retry %d/%d slot=%p *slot=%p",
                  sRetry, kMaxRetries, filterTableSlot, *filterTableSlot);
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                           dispatch_get_main_queue(),
                           ^{ registerCustomFilter(); });
        } else {
            lglog("registerCustomFilter: gave up after %d retries, *slot=%p",
                  kMaxRetries, *filterTableSlot);
        }
        return false;
    }

    static void *g_cachedFilterTable = nullptr;
    if (!g_cachedFilterTable) {
        g_cachedFilterTable = *filterTableSlot;
        if (g_cachedFilterTable) {
            lglog("registerCustomFilter: cached filter table=%p (slot=%p)",
                  g_cachedFilterTable, filterTableSlot);
        }
    }'''

new_src, n = pattern.subn(replacement, src, count=1)
if n == 0:
    print(f"WARN: retry block not found in {PATH}")
    sys.exit(0)

src = new_src

with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"patched: retry capped at 5 in {PATH}")
