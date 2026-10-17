const readingTerms: Array<[RegExp, string]> = [
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
];

/** Reading-only projection; original reports and frozen snapshots stay intact. */
export function readingText(text: string, limit = 140): string {
  const translated = readingTerms
    .reduce((value, [pattern, label]) => value.replace(pattern, label), text)
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
