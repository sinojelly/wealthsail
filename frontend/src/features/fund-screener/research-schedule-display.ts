import type { DailyResearch } from "../../api/investment";

export function researchScheduleLabel(schedule?: DailyResearch["schedule"]) {
  if (
    !schedule ||
    schedule.timezone !== "Asia/Shanghai" ||
    !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(schedule.time) ||
    (schedule.mode !== "DAILY" && schedule.mode !== "WEEKDAYS")
  ) {
    return "自动研究计划以服务配置为准";
  }
  const days = schedule.mode === "DAILY" ? "每天" : "周一至周五";
  return `${days}${schedule.time}启动AI研究（北京时间）`;
}
