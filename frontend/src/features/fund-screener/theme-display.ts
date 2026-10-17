import type { ThemeCode } from "../../api/investment";

export const themeOptions: ReadonlyArray<{ code: ThemeCode; label: string }> = [
  { code: "HAINAN_FTP", label: "海南自贸港" },
  { code: "PUDONG_DEV", label: "浦东开发" },
  { code: "GEO_CRUDE_OIL", label: "地缘与原油" },
  { code: "AI_MEMORY_HBM", label: "AI存储与HBM" },
  { code: "COMMERCIAL_SPACE", label: "商业航天" },
];
