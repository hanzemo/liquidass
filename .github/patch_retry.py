#!/usr/bin/env python3
"""
Patch LiquidAssBackboardd/Tweak.mm:
  1. 在 registerCustomFilter 开头插入缓存声明
  2. 把 filter_table null 的无限重试改成上限 5 次
  3. 在重试日志里打印 slot 和 *slot 的真实值
任何一步匹配失败都直接退出，避免构建出错误的产物。
"""
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
    sys.exit("ERROR: file already patched (g_cachedFilterTableSlot present)")

cache_decl = (
    "static bool registerCustomFilter(void) {\n"
    "    static void **g_cachedFilterTableSlot = nullptr;\n"
    "    static void  *g_cachedFilterTable = nullptr;\n"
)
src = src.replace(anchor_fn, cache_decl, 1)

# ---------- 2. 替换无限重试块 ----------
pattern = re.compile(
    r'if \(!\*filterTableSlot\) \{\s*'
    r'lglog\("registerCustomFilter: filter_table null, retrying in 250ms"\);\s*'
    r'dispatch_after\([^;]+;\s*'
    r'return false;\s*\}',
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

    // --- cache the resolved table once it becomes non-null ---
    if (!g_cachedFilterTable) {
        g_cachedFilterTable = *filterTableSlot;
        if (g_cachedFilterTable) {
            lglog("registerCustomFilter: cached filter table=%p (slot=%p)",
                  g_cachedFilterTable, filterTableSlot);
        }
    }'''

src, n = pattern.subn(replacement, src, count=1)
if n == 0:
    sys.exit("ERROR: retry block regex did not match")

with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print("patched: retry capped at 5, log added, table cached")
