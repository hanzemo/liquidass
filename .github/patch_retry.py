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
# 1. 文件级全局缓存 + 前置声明 + 失效函数
#    全部修好：sRetry 全局化、主动重解析、保留 code 地址
# ============================================================
anchor_fn = "static bool registerCustomFilter(void) {\n"
if anchor_fn not in src:
    print(f"ERROR: registerCustomFilter anchor not found in {PATH}")
    sys.exit(1)

globals_and_invalidator = (
    "// ===== 崩溃修复：缓存提升为文件级全局 =====\n"
    "static bool    g_lgSymbolsResolved  = false;\n"
    "static void  **g_lgCachedVtable     = nullptr;\n"
    "static int     g_lgCachedEdgeSlot   = -1;\n"
    "static int     g_lgCachedRenderSlot = -1;\n"
    "static void  **g_lgCachedCtxSlot    = nullptr;\n"
    "static int     g_lgRetryCount       = 0;   // 从 registerCustomFilter 内部提升\n"
    "\n"
    "// 前置声明：让 LGInvalidateSymbolCache 能重新触发解析\n"
    "static bool registerCustomFilter(void);\n"
    "\n"
    "static void LGInvalidateSymbolCache(const char *reason) {\n"
    "    if (!g_lgSymbolsResolved && !g_lgCachedVtable) return;\n"
    "    lglog(\"LGInvalidateSymbolCache: %s (was vtable=%p edge=%d render=%d retry=%d)\",\n"
    "          reason, g_lgCachedVtable, g_lgCachedEdgeSlot, g_lgCachedRenderSlot,\n"
    "          g_lgRetryCount);\n"
    "    g_lgSymbolsResolved  = false;\n"
    "    g_lgCachedVtable     = nullptr;\n"
    "    g_lgCachedEdgeSlot   = -1;\n"
    "    g_lgCachedRenderSlot = -1;\n"
    "    g_lgCachedCtxSlot    = nullptr;\n"
    "    g_lgRetryCount       = 0;   // 重置重试计数，允许重解析\n"
    "    // 注意：不动 g_origGaussR13 / g_origGaussEdgeInfo / g_gaussianHooksInstalled\n"
    "    // 它们指向 QuartzCore 代码段，vtable 重建不影响代码段\n"
    "    // 主动触发重新解析（否则只清不重建，功能等于禁用）\n"
    "    dispatch_async(dispatch_get_main_queue(), ^{\n"
    "        registerCustomFilter();\n"
    "    });\n"
    "}\n"
    "\n"
)

src = src.replace(anchor_fn, globals_and_invalidator + anchor_fn, 1)


# ============================================================
# 2. registerCustomFilter 内部不再声明 static 缓存
# ============================================================
src = replace_or_die(
    src,
    "static bool registerCustomFilter(void) {\n"
    "    static bool    g_lgSymbolsResolved  = false;\n"
    "    static void  **g_lgCachedVtable     = nullptr;\n"
    "    static int     g_lgCachedEdgeSlot   = -1;\n"
    "    static int     g_lgCachedRenderSlot = -1;\n"
    "    static void  **g_lgCachedCtxSlot    = nullptr;\n",
    "static bool registerCustomFilter(void) {\n",
    "remove static cache decls",
)


# ============================================================
# 3. 解析段包进 if，缓存到全局
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

        // ---- 缓存解析结果到文件级全局 ----
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
# 4. 重试块：全局 sRetry + 200 次 + 主队列
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
        static const int kMaxRetries = 200;
        if (g_lgRetryCount < kMaxRetries) {
            g_lgRetryCount++;
            lglog("registerCustomFilter: retry %d/%d *slot=%p (cached: edge=%d render=%d)",
                  g_lgRetryCount, kMaxRetries, *filterTableSlot,
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
# 5. 所有块外引用 → 缓存变量
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
# 6. 崩溃修复：ourCustomRender13 入口加防御检查
#    这是最关键的——校验缓存有效 + vtable 还在 QuartzCore 镜像内
# ============================================================
defensive_anchor = "static void ourCustomRender13("
if defensive_anchor not in src:
    print("ERROR: ourCustomRender13 signature not found.")
    print("       请把函数签名贴出来，我改成精确匹配。")
    sys.exit(1)

# 找函数签名的 `{`
idx = src.index(defensive_anchor)
brace_idx = src.index("{", idx)
insert_pos = brace_idx + 1

defensive_code = (
    "\n"
    "    // ===== 崩溃修复：每次校验缓存，防止 CA 重建 vtable 后悬空 =====\n"
    "    if (!g_lgSymbolsResolved || !g_lgCachedVtable) {\n"
    "        lglog(\"ourCustomRender13: cache invalid, skipping custom render\");\n"
    "        return;\n"
    "    }\n"
    "    if (!LGSymAddressInQuartzCoreImage((void *)g_lgCachedVtable)) {\n"
    "        lglog(\"ourCustomRender13: vtable %p left QuartzCore, invalidating\",\n"
    "              g_lgCachedVtable);\n"
    "        LGInvalidateSymbolCache(\"vtable-left-image\");\n"
    "        return;\n"
    "    }\n"
    "    uintptr_t _vt = (uintptr_t)g_lgCachedVtable;\n"
    "    if (_vt < 0x100000000ULL || _vt > 0x900000000ULL) {\n"
    "        lglog(\"ourCustomRender13: vtable out of range %p, invalidating\", g_lgCachedVtable);\n"
    "        LGInvalidateSymbolCache(\"out-of-range\");\n"
    "        return;\n"
    "    }\n"
    "    // vtable slot 也要校验：如果 slot 指向了非法地址，说明 vtable 内容变了\n"
    "    void *_slot0 = g_lgCachedVtable[0];\n"
    "    if (!_slot0 || !LGSymAddressInQuartzCoreImage(_slot0)) {\n"
    "        lglog(\"ourCustomRender13: vtable[0]=%p invalid, invalidating\", _slot0);\n"
    "        LGInvalidateSymbolCache(\"vtable-slot0-invalid\");\n"
    "        return;\n"
    "    }\n"
)

src = src[:insert_pos] + defensive_code + src[insert_pos:]
print("INFO: inserted defensive check into ourCustomRender13")


# ============================================================
# 7. 写盘
# ============================================================
with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"patched OK: {PATH}")
print("checks:")
print("  grep -n 'LGInvalidateSymbolCache' " + PATH)
print("  grep -n 'g_lgRetryCount' " + PATH)
print("  grep -n 'LGSymAddressInQuartzCoreImage' " + PATH)
