import type { OpportunityContext } from "../../api/investment";
import { researchSummary } from "./research-reading";

export function marketJudgment(data: OpportunityContext): string[] {
  const report = data.dailyResearch?.report;
  const text = report?.macroCycle?.trim() || report?.summary?.trim();
  if (!text) return [];
  return (
    researchSummary(text, Infinity).match(/[^。！？!?]+[。！？!?]*/gu) ?? []
  )
    .map((sentence) => sentence.trim())
    .filter(Boolean)
    .slice(0, 3)
    .map((sentence) => researchSummary(sentence, 160));
}
