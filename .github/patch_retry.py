#!/usr/bin/env python3
import os
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


def replace_or_die(text, old, new, label, count=1):
    if old not in text:
        print(f"ERROR: [{label}] pattern NOT found. Expected literally:")
        print("-------- BEGIN --------")
        print(old)
        print("--------- END ---------")
        sys.exit(1)
    return text.replace(old, new, count)


# ============================================================
# 1. 函数入口插入缓存声明（不再包含 g_lgCachedDescriptor）
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
    "    static void  **g_lgCachedCtxSlot    = nullptr;\n"
)
src = src.replace(anchor_fn, cache_decl, 1)


# ============================================================
# 2. 解析段包进 if，并缓存；块内保留同名局部变量，
#    块外不再声明桥接变量
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

        // ---- 缓存解析结果 ----
        g_lgCachedVtable     = gaussVtable;
        g_lgCachedEdgeSlot   = edgeInfoSlot;
        g_lgCachedRenderSlot = renderSlot;
        g_lgCachedCtxSlot    = gaussCtxSlot;
        g_lgSymbolsResolved  = true;
        lglog("registerCustomFilter: symbols cached (vtable=%p edge=%d render=%d)",
              gaussVtable, edgeInfoSlot, renderSlot);
    }
"""

src = replace_or_die(src, old_parse, new_parse, "parse block")


# ============================================================
# 3. 重试块：200 次 + 主队列
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
# 4. 所有块外引用 → 缓存变量（全部走 replace_or_die）
#    如果哪条没匹配上，脚本会打印期望原文并退出
# ============================================================

src = replace_or_die(
    src,
    "memcpy(g_customVtable, gaussVtable, kVtableSlots * sizeof(void *));",
    "memcpy(g_customVtable, g_lgCachedVtable, kVtableSlots * sizeof(void *));",
    "memcpy customVtable",
)

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

src = replace_or_die(
    src,
    "renderSlot, atomId, kHostCount);",
    "g_lgCachedRenderSlot, atomId, kHostCount);",
    "done log renderSlot",
)

src = replace_or_die(
    src,
    "registrationDescriptor = gaussCtxSlot;",
    "registrationDescriptor = g_lgCachedCtxSlot;",
    "registrationDescriptor",
)


# ============================================================
# 5. 写盘
# ============================================================
with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"patched OK: symbols cached + retry 200 on main queue in {PATH}")
print("verify:  grep -n 'gaussVtable\\|gaussCtxSlot\\|edgeInfoSlot\\|renderSlot' " + PATH)
print("         (outside the if-block there must be NO bare names)")
