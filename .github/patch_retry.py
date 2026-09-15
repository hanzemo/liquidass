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

# 已经打过新版补丁就跳过
if "LGInvalidateSymbolCache" in src:
    print(f"SKIP: {PATH} already fully patched (new version)")
    sys.exit(0)


def replace_or_die(text, old, new, label, count=1):
    if old not in text:
        print(f"ERROR: [{label}] pattern NOT found.")
        print("-------- expected --------")
        print(old[:400])
        print("--------------------------")
        sys.exit(1)
    return text.replace(old, new, count)


# ============================================================
# 1. 在 "static const int kDynamicRadiusSteps = 32;" 之后
#    插入文件级全局 + 前置声明 + 失效函数
# ============================================================
anchor_globals = "static const int kDynamicRadiusSteps = 32;\n"
if anchor_globals not in src:
    print("ERROR: kDynamicRadiusSteps anchor not found")
    sys.exit(1)

globals_block = (
    "static const int kDynamicRadiusSteps = 32;\n"
    "\n"
    "// ===== 崩溃修复：缓存提升为文件级全局 =====\n"
    "static bool    g_lgSymbolsResolved  = false;\n"
    "static void  **g_lgCachedVtable     = nullptr;\n"
    "static int     g_lgCachedEdgeSlot   = -1;\n"
    "static int     g_lgCachedRenderSlot = -1;\n"
    "static void  **g_lgCachedCtxSlot    = nullptr;\n"
    "static int     g_lgRetryCount       = 0;\n"
    "\n"
    "static bool registerCustomFilter(void);   // 前置声明\n"
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
    "    g_lgRetryCount       = 0;\n"
    "    dispatch_async(dispatch_get_main_queue(), ^{\n"
    "        registerCustomFilter();\n"
    "    });\n"
    "}\n"
)
src = replace_or_die(src, anchor_globals, globals_block, "insert globals")


# ============================================================
# 2. ourCustomRender13 入口加防御检查
# ============================================================
r13_anchor = """static void ourCustomRender13(void *self, void *filter, void *layer, void *ctx,
                               float opacity, void *surface, float scale,
                               bool flag, void *cm, void *shape, float *out)
{
    static uint64_t tsum_stop = 0, tsum_ours = 0, tsum_gauss = 0, tcount = 0;
"""

r13_new = """static void ourCustomRender13(void *self, void *filter, void *layer, void *ctx,
                               float opacity, void *surface, float scale,
                               bool flag, void *cm, void *shape, float *out)
{
    // ===== 崩溃修复：缓存可能已被 CA 重建，必须校验 =====
    if (!g_lgSymbolsResolved || !g_lgCachedVtable) {
        lglog("ourCustomRender13: cache invalid, skipping custom render");
        return;
    }
    if (!LGSymAddressInQuartzCoreImage((void *)g_lgCachedVtable)) {
        lglog("ourCustomRender13: vtable %p left QuartzCore, invalidating",
              g_lgCachedVtable);
        LGInvalidateSymbolCache("vtable-left-image");
        return;
    }
    uintptr_t _vt = (uintptr_t)g_lgCachedVtable;
    if (_vt < 0x100000000ULL || _vt > 0x900000000ULL) {
        lglog("ourCustomRender13: vtable out of range %p, invalidating",
              g_lgCachedVtable);
        LGInvalidateSymbolCache("out-of-range");
        return;
    }

    static uint64_t tsum_stop = 0, tsum_ours = 0, tsum_gauss = 0, tcount = 0;
"""

src = replace_or_die(src, r13_anchor, r13_new, "insert R13 defensive check")


# ============================================================
# 3. registerCustomFilter 大改
#    用精确匹配整段替换
# ============================================================
old_register = """// registering before filter table exists breaks system blur, this became a tweak btw check out https://github.com/winaviation-tweaks/Blurless
static bool registerCustomFilter(void) {
    void **filterTableSlot = (void **)LGResolve_FilterTableSlot();
    if (!filterTableSlot) {
        lglog("registerCustomFilter: could not resolve filter_table, aborting (no fallback)");
        return false;
    }
    if (g_filterRegistered) return true; // idempotent

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

    if (!*filterTableSlot) {
        lglog("registerCustomFilter: filter_table null, retrying in 250ms");
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0),
                       ^{ registerCustomFilter(); });
        return false;
    }

    if (!g_internAtom || !g_addFilter) {
        lglog("registerCustomFilter: internAtom=%p addFilter=%p, aborting",
              (void *)g_internAtom, (void *)g_addFilter);
        return false;
    }
    uint32_t atomId    = g_internAtom(kCustomFilterTypeName);
    uint32_t gaussAtom = g_internAtom("gaussianBlur");
    lglog("registerCustomFilter: atom('%s')=0x%x  gaussian=0x%x  collision=%s",
          kCustomFilterTypeName, atomId, gaussAtom,
          atomId == gaussAtom ? "YES-BAD" : "no");

    void *registrationDescriptor = nullptr;
    if (g_useHookPath) {
        if (!g_gaussianHooksInstalled) {
            lglog("registerCustomFilter: Gaussian hooks unexpectedly missing");
            return false;
        }
        registrationDescriptor = gaussCtxSlot;
    } else {
        g_origGaussR13 = (Render13Fn)LGSymMakeCallable(gaussVtable[renderSlot]);
        g_origGaussEdgeInfo =
            (EdgeInfoFn)LGSymMakeCallable(gaussVtable[edgeInfoSlot]);
        lglog("registerCustomFilter: g_origGaussR13 = %p", (void *)g_origGaussR13);
        lglog("registerCustomFilter: g_origGaussEdgeInfo = %p",
              (void *)g_origGaussEdgeInfo);
        if (!g_origGaussR13 || !g_origGaussEdgeInfo) {
            lglog("registerCustomFilter: Gaussian callable resolution failed");
            return false;
        }

        g_customVtable = (void **)mmap(NULL, kVtableSlots * sizeof(void *),
                                       PROT_READ | PROT_WRITE,
                                       MAP_ANON | MAP_PRIVATE, -1, 0);
        if (g_customVtable == MAP_FAILED) {
            lglog("registerCustomFilter: vtable mmap failed errno=%d", errno);
            g_customVtable = nullptr;
            return false;
        }
        memcpy(g_customVtable, gaussVtable, kVtableSlots * sizeof(void *));
        g_customVtable[0]          = LGSymStripCode((void *)&ourIdentityStub);
        g_customVtable[edgeInfoSlot] =
            LGSymStripCode((void *)&ourCustomEdgeInfo);
        g_customVtable[renderSlot] = LGSymStripCode((void *)&ourCustomRender13);

        g_customCtx = mmap(NULL, 256, PROT_READ | PROT_WRITE,
                           MAP_ANON | MAP_PRIVATE, -1, 0);
        if (g_customCtx == MAP_FAILED) {
            lglog("registerCustomFilter: ctx mmap failed errno=%d", errno);
            munmap(g_customVtable, kVtableSlots * sizeof(void *));
            g_customVtable = nullptr;
            g_customCtx    = nullptr;
            return false;
        }
        *(void **)g_customCtx = g_customVtable;
        registrationDescriptor = g_customCtx;
    }

    lgStartPrefsObserver();

    g_hostParams[0].atom = atomId;
    lgRegisterCustomAtom(atomId, registrationDescriptor);
    NSString *rootRefreshName = [[NSString stringWithUTF8String:kHostDefaults[0].typeName]
        stringByAppendingString:@".refresh"];
    uint32_t rootRefreshAtom = g_internAtom(rootRefreshName.UTF8String);
    if (rootRefreshAtom) {
        g_refreshRoutes[rootRefreshAtom] = { 0, false };
        lgRegisterCustomAtom(rootRefreshAtom, registrationDescriptor);
    }
    for (int i = 1; i < kHostCount; i++) {
        uint32_t a = g_internAtom(kHostDefaults[i].typeName);
        g_hostParams[i].atom = a;
        if (a && a != atomId) lgRegisterCustomAtom(a, registrationDescriptor);
        NSString *refreshName = [[NSString stringWithUTF8String:kHostDefaults[i].typeName]
            stringByAppendingString:@".refresh"];
        uint32_t refreshAtom = g_internAtom(refreshName.UTF8String);
        if (refreshAtom) {
            g_refreshRoutes[refreshAtom] = { i, false };
            lgRegisterCustomAtom(refreshAtom, registrationDescriptor);
        }
        NSString *darkName = [[NSString stringWithUTF8String:kHostDefaults[i].typeName] stringByAppendingString:@".dark"];
        uint32_t da = g_internAtom(darkName.UTF8String);
        g_darkAtoms[i] = da;
        if (da && da != a) lgRegisterCustomAtom(da, registrationDescriptor);
        NSString *darkRefreshName = [darkName stringByAppendingString:@".refresh"];
        uint32_t darkRefreshAtom = g_internAtom(darkRefreshName.UTF8String);
        if (darkRefreshAtom) {
            g_refreshRoutes[darkRefreshAtom] = { i, true };
            lgRegisterCustomAtom(darkRefreshAtom, registrationDescriptor);
        }
        if (lgUsesDynamicRadiusRoute(i)) {
            for (int step = 0; step <= kDynamicRadiusSteps / 2; step++) {
                NSString *radiusName = [[NSString stringWithUTF8String:kHostDefaults[i].typeName]
                    stringByAppendingFormat:@".r%d", step];
                uint32_t ra = g_internAtom(radiusName.UTF8String);
                if (ra) {
                    g_radiusRoutes[ra] = { i, (float)step / (float)kDynamicRadiusSteps, false };
                    lgRegisterCustomAtom(ra, registrationDescriptor);
                    NSString *radiusRefreshName = [radiusName stringByAppendingString:@".refresh"];
                    uint32_t radiusRefreshAtom = g_internAtom(radiusRefreshName.UTF8String);
                    if (radiusRefreshAtom) {
                        g_radiusRoutes[radiusRefreshAtom] =
                            { i, (float)step / (float)kDynamicRadiusSteps, false };
                        lgRegisterCustomAtom(radiusRefreshAtom, registrationDescriptor);
                    }
                }
                NSString *darkRadiusName = [radiusName stringByAppendingString:@".dark"];
                uint32_t rda = g_internAtom(darkRadiusName.UTF8String);
                if (rda) {
                    g_radiusRoutes[rda] = { i, (float)step / (float)kDynamicRadiusSteps, true };
                    lgRegisterCustomAtom(rda, registrationDescriptor);
                    NSString *darkRadiusRefreshName =
                        [darkRadiusName stringByAppendingString:@".refresh"];
                    uint32_t darkRadiusRefreshAtom =
                        g_internAtom(darkRadiusRefreshName.UTF8String);
                    if (darkRadiusRefreshAtom) {
                        g_radiusRoutes[darkRadiusRefreshAtom] =
                            { i, (float)step / (float)kDynamicRadiusSteps, true };
                        lgRegisterCustomAtom(darkRadiusRefreshAtom, registrationDescriptor);
                    }
                }
            }
        }
    }

    g_filterRegistered = true;
    lglog("registerCustomFilter: done mode=%s descriptor=%p renderSlot=%d atom=0x%x hosts=%d",
          g_useHookPath ? "hook" : "clone", registrationDescriptor,
          renderSlot, atomId, kHostCount);

    CFNotificationCenterPostNotification(
        CFNotificationCenterGetDarwinNotifyCenter(),
        kLGParametersReloadedNote, NULL, NULL, true);
    lglog("registerCustomFilter: registration-ready notification posted");
    return true;
}
"""

new_register = """// registering before filter table exists breaks system blur, this became a tweak btw check out https://github.com/winaviation-tweaks/Blurless
static bool registerCustomFilter(void) {
    if (g_filterRegistered) return true; // idempotent

    void **filterTableSlot = (void **)LGResolve_FilterTableSlot();
    if (!filterTableSlot) {
        lglog("registerCustomFilter: could not resolve filter_table, aborting (no fallback)");
        return false;
    }

    if (!g_lgSymbolsResolved) {
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

    if (!*filterTableSlot) {
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

    if (!g_internAtom || !g_addFilter) {
        lglog("registerCustomFilter: internAtom=%p addFilter=%p, aborting",
              (void *)g_internAtom, (void *)g_addFilter);
        return false;
    }
    uint32_t atomId    = g_internAtom(kCustomFilterTypeName);
    uint32_t gaussAtom = g_internAtom("gaussianBlur");
    lglog("registerCustomFilter: atom('%s')=0x%x  gaussian=0x%x  collision=%s",
          kCustomFilterTypeName, atomId, gaussAtom,
          atomId == gaussAtom ? "YES-BAD" : "no");

    void *registrationDescriptor = nullptr;
    if (g_useHookPath) {
        if (!g_gaussianHooksInstalled) {
            lglog("registerCustomFilter: Gaussian hooks unexpectedly missing");
            return false;
        }
        registrationDescriptor = g_lgCachedCtxSlot;
    } else {
        g_origGaussR13 = (Render13Fn)LGSymMakeCallable(
            g_lgCachedVtable[g_lgCachedRenderSlot]);
        g_origGaussEdgeInfo = (EdgeInfoFn)LGSymMakeCallable(
            g_lgCachedVtable[g_lgCachedEdgeSlot]);
        lglog("registerCustomFilter: g_origGaussR13 = %p", (void *)g_origGaussR13);
        lglog("registerCustomFilter: g_origGaussEdgeInfo = %p",
              (void *)g_origGaussEdgeInfo);
        if (!g_origGaussR13 || !g_origGaussEdgeInfo) {
            lglog("registerCustomFilter: Gaussian callable resolution failed");
            return false;
        }

        g_customVtable = (void **)mmap(NULL, kVtableSlots * sizeof(void *),
                                       PROT_READ | PROT_WRITE,
                                       MAP_ANON | MAP_PRIVATE, -1, 0);
        if (g_customVtable == MAP_FAILED) {
            lglog("registerCustomFilter: vtable mmap failed errno=%d", errno);
            g_customVtable = nullptr;
            return false;
        }
        memcpy(g_customVtable, g_lgCachedVtable, kVtableSlots * sizeof(void *));
        g_customVtable[0] = LGSymStripCode((void *)&ourIdentityStub);
        g_customVtable[g_lgCachedEdgeSlot] =
            LGSymStripCode((void *)&ourCustomEdgeInfo);
        g_customVtable[g_lgCachedRenderSlot] =
            LGSymStripCode((void *)&ourCustomRender13);

        g_customCtx = mmap(NULL, 256, PROT_READ | PROT_WRITE,
                           MAP_ANON | MAP_PRIVATE, -1, 0);
        if (g_customCtx == MAP_FAILED) {
            lglog("registerCustomFilter: ctx mmap failed errno=%d", errno);
            munmap(g_customVtable, kVtableSlots * sizeof(void *));
            g_customVtable = nullptr;
            g_customCtx    = nullptr;
            return false;
        }
        *(void **)g_customCtx = g_customVtable;
        registrationDescriptor = g_customCtx;
    }

    lgStartPrefsObserver();

    g_hostParams[0].atom = atomId;
    lgRegisterCustomAtom(atomId, registrationDescriptor);
    NSString *rootRefreshName = [[NSString stringWithUTF8String:kHostDefaults[0].typeName]
        stringByAppendingString:@".refresh"];
    uint32_t rootRefreshAtom = g_internAtom(rootRefreshName.UTF8String);
    if (rootRefreshAtom) {
        g_refreshRoutes[rootRefreshAtom] = { 0, false };
        lgRegisterCustomAtom(rootRefreshAtom, registrationDescriptor);
    }
    for (int i = 1; i < kHostCount; i++) {
        uint32_t a = g_internAtom(kHostDefaults[i].typeName);
        g_hostParams[i].atom = a;
        if (a && a != atomId) lgRegisterCustomAtom(a, registrationDescriptor);
        NSString *refreshName = [[NSString stringWithUTF8String:kHostDefaults[i].typeName]
            stringByAppendingString:@".refresh"];
        uint32_t refreshAtom = g_internAtom(refreshName.UTF8String);
        if (refreshAtom) {
            g_refreshRoutes[refreshAtom] = { i, false };
            lgRegisterCustomAtom(refreshAtom, registrationDescriptor);
        }
        NSString *darkName = [[NSString stringWithUTF8String:kHostDefaults[i].typeName] stringByAppendingString:@".dark"];
        uint32_t da = g_internAtom(darkName.UTF8String);
        g_darkAtoms[i] = da;
        if (da && da != a) lgRegisterCustomAtom(da, registrationDescriptor);
        NSString *darkRefreshName = [darkName stringByAppendingString:@".refresh"];
        uint32_t darkRefreshAtom = g_internAtom(darkRefreshName.UTF8String);
        if (darkRefreshAtom) {
            g_refreshRoutes[darkRefreshAtom] = { i, true };
            lgRegisterCustomAtom(darkRefreshAtom, registrationDescriptor);
        }
        if (lgUsesDynamicRadiusRoute(i)) {
            for (int step = 0; step <= kDynamicRadiusSteps / 2; step++) {
                NSString *radiusName = [[NSString stringWithUTF8String:kHostDefaults[i].typeName]
                    stringByAppendingFormat:@".r%d", step];
                uint32_t ra = g_internAtom(radiusName.UTF8String);
                if (ra) {
                    g_radiusRoutes[ra] = { i, (float)step / (float)kDynamicRadiusSteps, false };
                    lgRegisterCustomAtom(ra, registrationDescriptor);
                    NSString *radiusRefreshName = [radiusName stringByAppendingString:@".refresh"];
                    uint32_t radiusRefreshAtom = g_internAtom(radiusRefreshName.UTF8String);
                    if (radiusRefreshAtom) {
                        g_radiusRoutes[radiusRefreshAtom] =
                            { i, (float)step / (float)kDynamicRadiusSteps, false };
                        lgRegisterCustomAtom(radiusRefreshAtom, registrationDescriptor);
                    }
                }
                NSString *darkRadiusName = [radiusName stringByAppendingString:@".dark"];
                uint32_t rda = g_internAtom(darkRadiusName.UTF8String);
                if (rda) {
                    g_radiusRoutes[rda] = { i, (float)step / (float)kDynamicRadiusSteps, true };
                    lgRegisterCustomAtom(rda, registrationDescriptor);
                    NSString *darkRadiusRefreshName =
                        [darkRadiusName stringByAppendingString:@".refresh"];
                    uint32_t darkRadiusRefreshAtom =
                        g_internAtom(darkRadiusRefreshName.UTF8String);
                    if (darkRadiusRefreshAtom) {
                        g_radiusRoutes[darkRadiusRefreshAtom] =
                            { i, (float)step / (float)kDynamicRadiusSteps, true };
                        lgRegisterCustomAtom(darkRadiusRefreshAtom, registrationDescriptor);
                    }
                }
            }
        }
    }

    g_filterRegistered = true;
    lglog("registerCustomFilter: done mode=%s descriptor=%p renderSlot=%d atom=0x%x hosts=%d",
          g_useHookPath ? "hook" : "clone", registrationDescriptor,
          g_lgCachedRenderSlot, atomId, kHostCount);

    CFNotificationCenterPostNotification(
        CFNotificationCenterGetDarwinNotifyCenter(),
        kLGParametersReloadedNote, NULL, NULL, true);
    lglog("registerCustomFilter: registration-ready notification posted");
    return true;
}
"""

src = replace_or_die(src, old_register, new_register, "registerCustomFilter rewrite")


# ============================================================
# 4. 写盘
# ============================================================
with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print(f"patched OK: {PATH}")
print()
print("verify:")
print("  grep -n 'LGInvalidateSymbolCache' " + PATH)
print("  grep -n 'g_lgRetryCount' " + PATH)
print("  grep -n 'g_lgSymbolsResolved' " + PATH)
