const readingTerms: Array<[RegExp, string]> = [
  [
    /previousValueDirections\s*(?:为|是)?\s*空(?:数组)?(?:[，,]\s*首次建立(?:同|该)方向(?:基准)?计划)?/g,
    "首次建立该方向基准计划",
  ],
  [/historyQuality\s*=\s*CONFLICT\b/g, "历史数据存在冲突"],
  [/historyQuality\s*=\s*STALE\b/g, "历史数据待更新"],
  [/historyQuality\s*=\s*UNKNOWN\b/g, "历史数据尚未核验"],
  [/\bPIT\b/g, "历史时点证据"],
  [/\bSOURCE_CHECKED\b/g, "原文已核对"],
  [/\bIMPLEMENTED\b/g, "已实施"],
  [/\bANNOUNCED\b/g, "已宣布"],
  [/\bPLANNED\b/g, "计划中"],
  [/\bREPORTED\b/g, "报道线索"],
  [/\bOPINION\b/g, "观点"],
  [/\bMODEL_CONDITIONAL\b/g, "条件推演"],
  [/\bUNVERIFIED\b/g, "尚未核验"],
  [/\bVERIFIED\b/g, "已核验"],
  [/\bCONFLICT\b/g, "数据存在冲突"],
  [/\bUNKNOWN\b/g, "证据尚不充分"],
  [/\bUNAVAILABLE\b/g, "证据暂不可用"],
  [/\bINCOMPLETE\b/g, "证据尚未齐备"],
  [/\bPARTIAL\b/g, "部分依据可用"],
  [/\bHAINAN_FTP\b/g, "海南自贸港"],
  [/\bPUDONG_DEV\b/g, "浦东发展"],
  [/\bGEO_CRUDE_OIL\b/g, "地缘与原油"],
  [/\bAI_MEMORY_HBM\b/g, "AI与存储产业"],
  [/\bCOMMERCIAL_SPACE\b/g, "商业航天"],
  [/\bM1_M2_GAP\b/g, "M1与M2剪刀差"],
  [/\bCN_T10\b/g, "中国10年国债收益率"],
  [/\bSHIBOR_7D\b/g, "Shibor一周"],
  [/\bpercentage_points\b/g, "个百分点"],
];

/** Reading-only projection; original reports and frozen snapshots stay intact. */
export function readingText(text: string, limit = 140): string {
  const translated = readingTerms
    .reduce((value, [pattern, label]) => value.replace(pattern, label), text)
    .replace(
      /\$\s*(?:[A-Za-z]|\\[A-Za-z]+(?:\{[A-Za-z]+\})?)\s*=\s*(-?\d+(?:\.\d+)?)\s*\$/g,
      (_, value: string) => String(Number(value)),
    )
    .replace(/(-?\d+\.\d{5,})/g, (value) => Number(value).toFixed(4))
    .replace(
      /(-?\d+(?:\.\d+)?)\s*个百分点/g,
      (_, value: string) => `${Number(value).toFixed(1)} 个百分点`,
    )
    .replace(/\s+/g, " ")
    .trim();
  const characters = Array.from(translated);
  const length = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : 140;
  return characters.length > length
    ? length > 0
      ? characters.slice(0, length - 1).join("") + "…"
      : ""
    : translated;
}

/** Summary-only projection; diagnostic details remain available in their source panels. */
export function researchSummary(text: string, limit = 280): string {
  return (
    readingText(text.replace(/媒体零命中[^。；;]*(?:[。；;]|$)/g, ""), limit) ||
    "综合研判暂无可读结论，请核查当前市场证据。"
  );
}
