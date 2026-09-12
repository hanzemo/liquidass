#!/usr/bin/env python3
import re
import sys

PATH = "LiquidAssBackboardd/Tweak.mm"

with open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

# ---------- 1. 函数入口插入缓存声明 ----------
anchor_fn = "static bool registerCustomFilter(void) {\n"
if anchor_fn not in src:
    sys.exit("ERROR: registerCustomFilter anchor not found")

if "g_cachedFilterTableSlot" in src:
    sys.exit("ERROR: file already patched")

cache_decl = (
    "static bool registerCustomFilter(void) {\n"
    "    static void **g_cachedFilterTableSlot = nullptr;\n"
    "    static void  *g_cachedFilterTable = nullptr;\n"
)
src = src.replace(anchor_fn, cache_decl, 1)

# ---------- 2. 用精确字符串替换 ----------
old_block = '''    if (!*filterTableSlot) {
        lglog("registerCustomFilter: filter_table null, retrying in 250ms");
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0),
                       ^{ registerCustomFilter(); });
        return false;
    }'''

new_block = '''    if (!*filterTableSlot) {
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

    if (!g_cachedFilterTable) {
        g_cachedFilterTable = *filterTableSlot;
        if (g_cachedFilterTable) {
            lglog("registerCustomFilter: cached filter table=%p (slot=%p)",
                  g_cachedFilterTable, filterTableSlot);
        }
    }'''

if old_block not in src:
    sys.exit("ERROR: retry block not found (exact match failed)")

src = src.replace(old_block, new_block, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print("patched: retry capped at 5, log added, table cached")
