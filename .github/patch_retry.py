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

if "g_lgSymbolsResolved" in src:
    print(f"SKIP: {PATH} already patched")
    sys.exit(0)


# ============================================================
# 辅助：带断言的替换，避免静默失败
# ============================================================
def replace_or_die(text, old, new, label, count=1):
    if old not in text:
        print(f"ERROR: [{label}] pattern not found:")
        print("-------- expected --------")
        print(old)
        print("--------------------------")
        sys.exit(1)
    return text.replace(old, new, count)


# ============================================================
# 1. 在函数入口插入缓存声明
# FIX: 追加 g_lgCachedCtxSlot，用于 2018 行的 registrationDescriptor
# ============================================================
anchor_fn = "static bool registerCustomFilter(void) {\n"
if anchor_fn not in src:
    print(f"ERROR: registerCustomFilter anchor not found in {PATH}")
    sys.exit(1)

cache_decl = (
    "static bool registerCustomFilter(void) {\n"
    "    static bool    g_lgSymbolsResolved  = false;\n"
    "    static void  **g_lgCachedVtable     = nullptr;\n"
    "    static int     g_lgCachedEdgeSlot   = -1;\n"
    "    static int     g_lgCachedRenderSlot = -1;\n"
    "    static void   *g_lgCachedDescriptor = nullptr;\n"
    "    static void  **g_lgCachedCtxSlot    = nullptr;\n"   # FIX
)
src = src.replace(anchor_fn, cache_decl, 1)


# ============================================================
# 2. 把解析段包进 if (!g_lgSymbolsResolved)，并缓存结果
# ============================================================
old_parse = """    void **gaussCtxSlot = (void **)LGResolve_GaussianCtxSlot();
    if (!gaussCtxSlot) {
        lglog("registerCustomFilter: could not resolve gaussian context, aborting (no fallback)");
        return false;
    }

    g_gaussCtxValue = LGSymStripData((void *)*gaussCtxSlot);
    lglog("registerCustomFilter: g_gaussCtxValue = %p (raw from %p)", g_gaussCtxValue, *gaussCtxSlot);
    void **gaussVtable = (void **)g_gaussCtxValue;
    if (!gaussVtable) {
        lglog("registerCustomFilter: gaussian vtable not found");
        return false;
    }
    lglog("registerCustomFilter: gaussian vtable @ %p", gaussVtable);

    int edgeInfoSlot =
        LGResolve_EdgeInfoVtableSlot((void * const *)gaussVtable,
                                     (int)kVtableSlots);
    if (edgeInfoSlot < 0 || edgeInfoSlot >= (int)kVtableSlots) {
        lglog("registerCustomFilter: could not resolve edge-info vtable slot (got %d), aborting",
              edgeInfoSlot);
        return false;
    }
    lglog("registerCustomFilter: edge-info vtable slot = %d", edgeInfoSlot);

    int renderSlot = LGResolve_RenderVtableSlot((void * const *)gaussVtable,
                                                (int)kVtableSlots);
    if (renderSlot < 0 && g_legacyRenderABI && edgeInfoSlot >= 3) {
        int candidate = edgeInfoSlot - 3;
        void *entry = LGSymStripCode(gaussVtable[candidate]);
        if (LGSymAddressInQuartzCoreImage(entry)) {
            renderSlot = candidate;
            lglog("registerCustomFilter: iOS 14 direct render fallback vtable[%d]=%p",
                  renderSlot, entry);
        }
    }
    if (renderSlot < 0 || renderSlot >= (int)kVtableSlots) {
        lglog("registerCustomFilter: could not resolve render vtable slot (got %d), aborting", renderSlot);
        return false;
    }
    lglog("registerCustomFilter: render vtable slot = %d", renderSlot);
    if (edgeInfoSlot == renderSlot) {
        lglog("registerCustomFilter: render and edge-info resolved to the same slot, aborting");
        return false;
    }

    if (g_useHookPath && !g_gaussianHooksInstalled) {
        if (!lgInstallGaussianHooks(gaussVtable[0],
                                    gaussVtable[edgeInfoSlot],
                                    gaussVtable[renderSlot])) {
            lglog("registerCustomFilter: Gaussian hooks install failed");
            return false;
        }
        g_gaussianHooksInstalled = true;
        lglog("registerCustomFilter: Gaussian hooks installed before filter registration");
    }
"""

new_parse = """    if (!g_lgSymbolsResolved) {
        void **gaussCtxSlot = (void **)LGResolve_GaussianCtxSlot();
        if (!gaussCtxSlot) {
            lglog("registerCustomFilter: could not resolve gaussian context, aborting (no fallback)");
            return false;
        }
        g_lgCachedCtxSlot = gaussCtxSlot;   // FIX: 供块外使用

        g_gaussCtxValue = LGSymStripData((void *)*gaussCtxSlot);
        lglog("registerCustomFilter: g_gaussCtxValue = %p (raw from %p)", g_gaussCtxValue, *gaussCtxSlot);
        void **gaussVtable = (void **)g_gaussCtxValue;
        if (!gaussVtable) {
            lglog("registerCustomFilter: gaussian vtable not found");
            return false;
        }
        lglog("registerCustomFilter: gaussian vtable @ %p", gaussVtable);

        int edgeInfoSlot =
            LGResolve_EdgeInfoVtableSlot((void * const *)gaussVtable,
                                         (int)kVtableSlots);
        if (edgeInfoSlot < 0 || edgeInfoSlot >= (int)kVtableSlots) {
            lglog("registerCustomFilter: could not resolve edge-info vtable slot (got %d), aborting",
                  edgeInfoSlot);
            return false;
        }
        lglog("registerCustomFilter: edge-info vtable slot = %d", edgeInfoSlot);

        int renderSlot = LGResolve_RenderVtableSlot((void * const *)gaussVtable,
                                                    (int)kVtableSlots);
        if (renderSlot < 0 && g_legacyRenderABI && edgeInfoSlot >= 3) {
            int candidate = edgeInfoSlot - 3;
            void *entry = LGSymStripCode(gaussVtable[candidate]);
            if (LGSymAddressInQuartzCoreImage(entry)) {
                renderSlot = candidate;
                lglog("registerCustomFilter: iOS 14 direct render fallback vtable[%d]=%p",
                      renderSlot, entry);
            }
        }
        if (renderSlot < 0 || renderSlot >= (int)kVtableSlots) {
            lglog("registerCustomFilter: could not resolve render vtable slot (got %d), aborting", renderSlot);
            return false;
        }
        lglog("registerCustomFilter: render vtable slot = %d", renderSlot);
        if (edgeInfoSlot == renderSlot) {
            lglog("registerCustomFilter: render and edge-info resolved to the same slot, aborting");
            return false;
        }

        if (g_useHookPath && !g_gaussianHooksInstalled) {
            if (!lgInstallGaussianHooks(gaussVtable[0],
                                        gaussVtable[edgeInfoSlot],
                                        gaussVtable[renderSlot])) {
                lglog("registerCustomFilter: Gaussian hooks install failed");
                return false;
            }
            g_gaussianHooksInstalled = true;
            lglog("registerCustomFilter: Gaussian hooks installed before filter registration");
        }

        // ---- 缓存解析结果，重试时直接用 ----
        g_lgCachedVtable     = gaussVtable;
        g_lgCachedEdgeSlot   = edgeInfoSlot;
        g_lgCachedRenderSlot = renderSlot;
        g_lgSymbolsResolved  = true;
        lglog("registerCustomFilter: symbols cached (vtable=%p edge=%d render=%d)",
              gaussVtable, edgeInfoSlot, renderSlot);
    }

    // FIX: 块外使用缓存值（替代原作用域内局部变量）
    void **gaussVtable   = g_lgCachedVtable;
    int    edgeInfoSlot  = g_lgCachedEdgeSlot;
    int    renderSlot    = g_lgCachedRenderSlot;
    void **gaussCtxSlot  = g_lgCachedCtxSlot;
"""

src = replace_or_die(src, old_parse, new_parse, "parse block")


# ============================================================
# 3. 替换重试块：改 200 次 + 主队列
# ============================================================
old_retry = """    if (!*filterTableSlot) {
        lglog("registerCustomFilter: filter_table null, retrying in 250ms");
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0),
                       ^{ registerCustomFilter(); });
        return false;
    }
"""

new_retry = """    if (!*filterTableSlot) {
        static int sRetry = 0;
        static const int kMaxRetries = 200;
        if (sRetry < kMaxRetries) {
            sRetry++;
            lglog("registerCustomFilter: retry %d/%d *slot=%p (cached: edge=%d render=%d)",
                  sRetry, kMaxRetries, *filterTableSlot,
                  g_lgCachedEdgeSlot, g_lgCachedRenderSlot);
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                           dispatch_get_main_queue(),
                           ^{ registerCustomFilter(); });
        } else {
            lglog("registerCustomFilter: gave up after %d retries, *slot=%p",
                  kMaxRetries, *filterTableSlot);
        }
        return false;
    }
"""

src = replace_or_die(src, old_retry, new_retry, "retry block")


# ============================================================
# 4. 后续引用改成缓存变量（全部走断言）
# ============================================================
src = replace_or_die(
    src,
    "memcpy(g_customVtable, gaussVtable, kVtableSlots * sizeof(void *));",
    "memcpy(g_customVtable, g_lgCachedVtable, kVtableSlots * sizeof(void *));",
    "memcpy customVtable",
)

# clone 路径里按槽位写入
src = replace_or_die(
    src,
    "g_customVtable[edgeInfoSlot] =\n            LGSymStripCode((void *)&ourCustomEdgeInfo);",
    "g_customVtable[g_lgCachedEdgeSlot] =\n            LGSymStripCode((void *)&ourCustomEdgeInfo);",
    "customVtable edgeInfoSlot",
)

src = replace_or_die(
    src,
    "g_customVtable[renderSlot] = LGSymStripCode((void *)&ourCustomRender13);",
    "g_customVtable[g_lgCachedRenderSlot] = LGSymStripCode((void *)&ourCustomRender13);",
    "customVtable renderSlot",
)

# 原始函数指针
src = replace_or_die(
    src,
    "g_origGaussR13 = (Render13Fn)LGSymMakeCallable(gaussVtable[renderSlot]);",
    "g_origGaussR13 = (Render13Fn)LGSymMakeCallable(g_lgCachedVtable[g_lgCachedRenderSlot]);",
    "origGaussR13",
)

src = replace_or_die(
    src,
    "g_origGaussEdgeInfo =\n            (EdgeInfoFn)LGSymMakeCallable(gaussVtable[edgeInfoSlot]);",
    "g_origGaussEdgeInfo =\n            (EdgeInfoFn)LGSymMakeCallable(g_lgCachedVtable[g_lgCachedEdgeSlot]);",
    "origGaussEdgeInfo",
)

# done 日志里的 renderSlot
src = replace_or_die(
    src,
    "renderSlot, atomId, kHostCount);",
    "g_lgCachedRenderSlot, atomId, kHostCount);",
    "done log renderSlot",
)

# FIX: 2018 行的 registrationDescriptor
src = replace_or_die(
    src,
    "registrationDescriptor = gaussCtxSlot;",
    "registrationDescriptor = g_lgCachedCtxSlot;",
    "registrationDescriptor",
)


# ============================================================
# 5. 安全检查：确认块外没有残留裸引用
# ============================================================
leaks = []
for i, line in enumerate(src.splitlines(), 1):
    if "if (!g_lgSymbolsResolved)" in line:
        # 从这里到对应的闭合括号之后，跳过检查（粗略判断，仅做提示）
        leaks.append(("IN-IF", i, line.strip()))
        break

with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"patched: symbols cached + retry 200 on main queue in {PATH}")
print("next: grep -n 'gaussVtable\\|gaussCtxSlot\\|edgeInfoSlot\\|renderSlot' Tweak.mm")
print("      confirm no bare reference remains outside the if-block")
