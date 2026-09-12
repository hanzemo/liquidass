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

if "LG_SCAN:" in src:
    print(f"SKIP: {PATH} already patched")
    sys.exit(0)

# 原块：只匹配 if (!*filterTableSlot) { ... return false; }
pattern = re.compile(
    r'if\s*\(\s*!\s*\*\s*filterTableSlot\s*\)\s*\{.*?return\s+false\s*;\s*\}',
    re.DOTALL,
)

replacement = '''if (!*filterTableSlot) {
        // LG_SCAN: 不动重试，只打印诊断信息
        lglog("LG_SCAN: slot=%p *slot=%p",
              filterTableSlot, *filterTableSlot);

        // 扫描 slot 前后 16 个 8 字节，看哪个位置有非零值
        void **base = filterTableSlot;
        for (int i = -8; i <= 8; i++) {
            void **candidate = base + i;
            void *value = *candidate;
            if (value != NULL) {
                lglog("LG_SCAN: [%+d] %p -> %p  <== 非零",
                      i, candidate, value);
            }
        }
        lglog("LG_SCAN: scan done");

        // 保持原重试逻辑不变
        lglog("registerCustomFilter: filter_table null, retrying in 250ms");
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0),
                       ^{ registerCustomFilter(); });
        return false;
    }'''

new_src, n = pattern.subn(replacement, src, count=1)
if n == 0:
    print(f"WARN: retry block not found in {PATH}")
    sys.exit(0)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(new_src)

print(f"patched: LG_SCAN logging inserted in {PATH}")
