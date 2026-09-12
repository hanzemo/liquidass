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

# 幂等检查改用更精确的标记，避免之前跑过 5 次版本时误判
if "kMaxRetries = 200" in src:
    print(f"SKIP: {PATH} already patched with 200 retries")
    sys.exit(0)

pattern = re.compile(
    r'if\s*\(\s*!\s*\*\s*filterTableSlot\s*\)\s*\{.*?'
    r'registerCustomFilter\s*\(\s*\)\s*;.*?'
    r'return\s+false\s*;\s*\}',
    re.DOTALL,
)

replacement = '''if (!*filterTableSlot) {
        static int sRetry = 0;
        static const int kMaxRetries = 200;
        if (sRetry < kMaxRetries) {
            sRetry++;
            lglog("registerCustomFilter: retry %d/%d slot=%p *slot=%p",
                  sRetry, kMaxRetries, filterTableSlot, *filterTableSlot);
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                           dispatch_get_global_queue(QOS_CLASS_UTILITY, 0),
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
    print(f"WARN: retry block not found in {PATH}, no changes applied")
    sys.exit(0)

src = new_src

with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"patched: retry raised to 200 in {PATH}")
