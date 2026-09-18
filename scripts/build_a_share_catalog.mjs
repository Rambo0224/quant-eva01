import fs from "node:fs/promises";

const inspection = JSON.parse(await fs.readFile(".work/indicator_workbook_inspection.json", "utf8"));
const tableLine = String(inspection.summary)
  .split(/\r?\n/)
  .find((line) => line.includes('"kind":"table"'));
if (!tableLine) throw new Error("Could not find the indicator table in the inspection output");

const table = JSON.parse(tableLine);
const rows = table.values.slice(1);

const themeDefinitions = {
  "market-breadth": {
    title: "甯傚満浜ゆ槗涓庢儏缁?",
    description: "鍏ㄥ競鍦烘垚浜ゃ€佽瀺璧勮瀺鍒搞€佹定璺屽仠鍜屽競鍦哄搴︽寚鏍?",
  },
  "index-valuation": {
    title: "鎸囨暟涓庝及鍊?",
    description: "涓昏瀹藉熀鎸囨暟浠锋牸銆佷及鍊煎拰娉㈠姩鐜囨寚鏍?",
  },
  "futures-options": {
    title: "鑲℃寚鏈熻揣涓庢湡鏉?",
    description: "鑲℃寚鏈熻揣鏀剁洏銆佸熀宸€佽娌借璐瘮鍜岄殣鍚尝鍔ㄧ巼",
  },
  "cross-sectional": {
    title: "鍏ㄥ競鍦烘í鎴潰",
    description: "鍏?A 鑲＄エ浼板€笺€佸熀鏈潰銆佸姩閲忓拰鎶€鏈潰鍒嗗竷",
  },
};

const crossSectionFactors = new Set([
  "momentum_20d_median", "momentum_60d_median", "reversal_5d_median",
  "turnover_individual_median", "volume_ratio_20d_median", "bias_20d_median",
  "atr_14d_median", "rsi_14d_median", "advance_ratio_20d", "high_60d_ratio",
  "high_120d_ratio", "high_250d_ratio", "above_ma20_ratio", "above_ma60_ratio",
  "above_ma120_ratio",
]);

const removedFactors = new Set([
  "wa_close", "wa_pe", "if_close", "ic_close", "ih_close",
  "pe_median", "pe_p90", "pe_p10", "pb_median", "pb_p90", "pb_p10",
  "ps_median", "dividend_yield_median", "roe_median", "roa_median",
  "gross_margin_median", "net_margin_median", "debt_assets_median",
  "profit_growth_median", "revenue_growth_median", "eps_growth_median",
]);

const futuresOptionsFactors = new Set([
  "if_basis", "ic_basis", "if_basis_annual",
  "ic_basis_annual", "pcr_volume", "pcr_oi", "ivix_50", "qvix", "if_vol_20d",
  "ic_vol_20d",
]);

const factorSeen = new Map();

function slugify(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function themeFor(factor) {
  if (crossSectionFactors.has(factor)) return "cross-sectional";
  if (futuresOptionsFactors.has(factor)) return "futures-options";
  if (/^(hs300|sz50|cyb50|kc50|zz500|wa)_/.test(factor)) return "index-valuation";
  return "market-breadth";
}

function sourcePlan(method, type) {
  if (type === "鍚堟垚") {
    return {
      preferred_source: "derived",
      fallback_source: null,
      status: "formula_pending",
      reason: "鐢卞師濮嬫暟鎹湪 OmniSignal 鍒嗘瀽灞傝绠楋紝涓嶇洿鎺ユ姄鍙?",
    };
  }

  const text = String(method || "").toLowerCase();
  if (text.includes("ifind")) {
    return {
      preferred_source: "ifind_mcp",
      fallback_source: "manual_excel",
      status: "requires_credentials",
      reason: "iFinD 宸插湪 Terminal 涓厤缃紝浣?OmniSignal 涓嶅鍒跺瘑閽?",
    };
  }
  if (text.includes("stock_margin")) {
    return {
      preferred_source: "exchange_official",
      fallback_source: "akshare_public",
      status: "adapter_planned",
      reason: "浼樺厛浣跨敤涓婁氦鎵€/娣变氦鎵€瀹樻柟铻嶈祫铻嶅埜鏁版嵁",
    };
  }
  if (text.includes("stock_zh_index_value_csindex")) {
    return {
      preferred_source: "csindex_official",
      fallback_source: "ifind_mcp",
      status: "adapter_planned",
      reason: "浼樺厛浣跨敤涓瘉鎸囨暟鏈夐檺鍏徃浼板€兼暟鎹?",
    };
  }
  if (text.includes("stock_zh_index_daily")) {
    return {
      preferred_source: "exchange_or_ifind",
      fallback_source: "akshare_public",
      status: "adapter_planned",
      reason: "鎸囨暟浠锋牸浼樺厛璧颁氦鏄撴墍鎴?iFinD锛孉kShare 浠呬綔鍏叡鍥為€€",
    };
  }
  if (text.includes("stock_zt_pool") || text.includes("stock_dt_pool") || text.includes("spot_em")) {
    return {
      preferred_source: "eastmoney_public",
      fallback_source: "ifind_mcp",
      status: "adapter_planned",
      reason: "鍏紑鎺ュ彛鍙敤浜庝綆棰戠洃娴嬶紝浣嗗繀椤讳繚鐣欏搷搴旀牎楠屽拰缂撳瓨",
    };
  }
  return {
    preferred_source: "akshare_public",
    fallback_source: "manual_excel",
    status: "fallback_only",
    reason: "Excel 涓粰鍑?AkShare 鏂规硶锛屾殏浣滀负鍏叡鍥為€€锛屼笉瑙嗕负鐢熶骇绾у敮涓€鏉ユ簮",
  };
}

function dependenciesFromMethod(method, type) {
  if (type !== "鍚堟垚") return [];
  const text = String(method || "");
  const matches = [...text.matchAll(/`([a-zA-Z][a-zA-Z0-9_]*)`/g)].map((match) => match[1]);
  return [...new Set(matches.filter((value) => !["date", "close", "open", "high", "low", "volume", "free_shares", "pe_ttm", "pb", "ps_ttm", "dividend_yield", "roe_ttm", "roa_ttm", "grossmargin", "netprofitmargin", "debttoassets", "yoyprofit", "yoy_or", "yoyeps", "high_60d"].includes(value)))];
}

function unitFor(factor, method) {
  if (/_ratio$/.test(factor)) return "姣斾緥";
  if (/_rate$|_annual$|_yield$|margin|growth|reversal|bias/.test(factor)) return "%";
  if (/vol_20d|ivix|qvix|rsi|pcr/.test(factor)) return "鎸囨暟/姣旂巼";
  if (/pe|pb|ps/.test(factor)) return "鍊嶆暟";
  if (/count$/.test(factor)) return "瀹舵暟";
  if (/turnover|amount|balance|purchase/.test(factor)) return "鍘熷鍗曚綅";
  return "";
}

function yLabelFor(factor, unit) {
  if (/_close$/.test(factor)) return "鎸囨暟鐐逛綅";
  if (/count$/.test(factor)) return "瀹舵暟";
  if (/pe|pb|ps/.test(factor)) return "浼板€煎€嶆暟";
  return unit || "鏁板€?";
}

function uniqueId(factor) {
  const count = (factorSeen.get(factor) || 0) + 1;
  factorSeen.set(factor, count);
  return count === 1 ? slugify(factor) : `${slugify(factor)}-${count}`;
}

const themes = Object.fromEntries(Object.entries(themeDefinitions).map(([key, value]) => [key, { ...value, indicators: [] }]));
const catalogRows = [];

for (const row of rows) {
  const [sequence, title, factorName, type, method] = row;
  const factor = String(factorName || "").trim();
  if (removedFactors.has(factor)) continue;
  const indicatorId = uniqueId(factor);
  const theme = themeFor(factor);
  const plan = sourcePlan(method, type);
  const dependencies = dependenciesFromMethod(method, type);
  const unit = unitFor(factor, method);
  const entry = {
    id: indicatorId,
    title: String(title || factor),
    description: `鏉ヨ嚜鎸囨爣搴撳簭鍙?${sequence}锛涘師濮嬫柟娉曪細${String(method || "")}`,
    source: "catalog",
    server: "",
    tool: "",
    arguments: { start_date: "2018-01-01" },
    series: [],
    chart: {
      type: "line",
      x_field: "date",
      y_field: factor,
      title: String(title || factor),
      unit,
      y_label: yLabelFor(factor, unit),
      source_label: plan.preferred_source,
    },
    metadata: {
      excel_sequence: sequence,
      factor_name: factor,
      indicator_type: type,
      excel_method: String(method || ""),
      preferred_source: plan.preferred_source,
      fallback_source: plan.fallback_source,
      source_status: plan.status,
      source_reason: plan.reason,
      dependencies,
    },
  };
  themes[theme].indicators.push(entry);
  catalogRows.push({ sequence, title, factor, type, theme, ...plan, dependencies, method });
}

const catalog = { themes };
await fs.writeFile("config/indicators.yaml", JSON.stringify(catalog, null, 2) + "\n", "utf8");
await fs.writeFile("config/source_registry.yaml", JSON.stringify({
  sources: [
    { id: "ifind_mcp", type: "mcp", reliability: "production_candidate", credential: "external_readonly_config", note: "澶嶇敤 Terminal 鐨勫彧璇婚厤缃矾寰勶紝涓嶅鍒朵护鐗?" },
    { id: "exchange_official", type: "official_exchange", reliability: "preferred", note: "涓婁氦鎵€/娣变氦鎵€瀹樻柟鎶湶" },
    { id: "csindex_official", type: "official_index", reliability: "preferred", note: "涓瘉鎸囨暟鏈夐檺鍏徃鎸囨暟鍙婁及鍊兼暟鎹?" },
    { id: "exchange_or_ifind", type: "official_or_vendor", reliability: "preferred", note: "鎸囨暟浠锋牸浼樺厛浜ゆ槗鎵€鎴?iFinD" },
    { id: "eastmoney_public", type: "public_api", reliability: "fallback", note: "浣跨敤鏃跺繀椤昏褰曟姄鍙栨椂闂淬€佸搷搴旀牎楠屽拰缂撳瓨" },
    { id: "akshare_public", type: "python_adapter", reliability: "fallback", note: "鍏叡鎺ュ彛閫傞厤灞傦紝涓嶄綔涓哄敮涓€鐢熶骇鏉ユ簮" },
    { id: "manual_excel", type: "manual_import", reliability: "last_resort", note: "浠呯敤浜庢殏鏃舵棤娉曡嚜鍔ㄦ姄鍙栫殑鎸囨爣" },
    { id: "derived", type: "calculation", reliability: "internal", note: "鐢辨爣鍑嗗寲鍘熷搴忓垪璁＄畻" },
  ]
}, null, 2) + "\n", "utf8");

const markdown = [
  "# A 鑲″缁村害甯傚満鐩戞祴鎸囨爣搴?",
  "",
  "鏈枃浠剁敱 `A鑲″缁村害甯傚満鐩戞祴鎸囨爣搴揰绛涢€夊悗.xlsx` 鐢熸垚銆俆erminal 浠呬綔涓哄彧璇绘ā鏉匡紱OmniSignal 灏嗗湪姝ゅ熀纭€涓婃帴鍏ョǔ瀹氭暟鎹簮銆?",
  "",
  `鍏?${catalogRows.length} 鏉℃寚鏍囪褰曘€俙,
  "",
  "| 搴忓彿 | 鎸囨爣 | 鍥犲瓙鍚?| 绫诲瀷 | 涓婚 | 棣栭€夋潵婧?| 鍥為€€鏉ユ簮 | 鐘舵€?|",
  "|---:|---|---|---|---|---|---|---|",
  ...catalogRows.map((item) => `| ${item.sequence} | ${item.title} | \`${item.factor}\` | ${item.type} | ${item.theme} | ${item.preferred_source} | ${item.fallback_source || "-"} | ${item.status} |`),
  "",
  "## 鏁版嵁婧愬師鍒?",
  "",
  "- iFinD MCP锛氫紭鍏堢敤浜庣敓浜х骇鍘嗗彶搴忓垪銆佹湡璐ф湡鏉冨拰妯埅闈㈡暟鎹紝浣嗕笉澶嶅埗 Terminal 鐨勪护鐗屻€?",
  "- 浜ゆ槗鎵€/涓瘉鎸囨暟瀹樻柟鏁版嵁锛氫紭鍏堢敤浜庤瀺璧勮瀺鍒搞€佹寚鏁颁环鏍煎拰鎸囨暟浼板€笺€?",
  "- Eastmoney/AkShare锛氬彧浣滀负鍙璁＄殑鍏叡鍥為€€锛屽繀椤讳繚鐣欑紦瀛樸€佹姄鍙栨椂闂村拰寮傚父鐘舵€併€?",
  "- 鍚堟垚鎸囨爣锛氬彧鍦ㄥ師濮嬪簭鍒楄川閲忛€氳繃妫€鏌ュ悗璁＄畻锛屾暟鎹己澶辨椂鐣欑┖骞跺憡璀︺€?",
].join("\n");
await fs.writeFile("docs/a_share_indicator_catalog.md", markdown + "\n", "utf8");

console.log(JSON.stringify({ indicators: catalogRows.length, themes: Object.fromEntries(Object.entries(themes).map(([key, value]) => [key, value.indicators.length])) }));

