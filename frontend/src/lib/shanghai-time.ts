const SHANGHAI_TIME_ZONE = "Asia/Shanghai";
const ABSOLUTE_ISO_TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/;

const SHANGHAI_DATE_TIME_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: SHANGHAI_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function parseAbsoluteTimestamp(value: string): Date | null {
  const match = ABSOLUTE_ISO_TIMESTAMP.exec(value);
  if (!match) {
    return null;
  }
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, zone] =
    match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const daysInMonth =
    month >= 1 && month <= 12
      ? new Date(Date.UTC(year, month, 0)).getUTCDate()
      : 0;
  const offsetValid =
    zone === "Z" ||
    (Number(zone.slice(1, 3)) <= 23 && Number(zone.slice(4, 6)) <= 59);
  if (
    day < 1 ||
    day > daysInMonth ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    !offsetValid
  ) {
    return null;
  }
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? null : instant;
}

function shanghaiParts(value: string) {
  const instant = parseAbsoluteTimestamp(value);
  if (!instant) {
    return null;
  }
  return Object.fromEntries(
    SHANGHAI_DATE_TIME_FORMATTER.formatToParts(instant).map((part) => [
      part.type,
      part.value,
    ]),
  );
}

export function formatShanghaiTime(value: string): string {
  const parts = shanghaiParts(value);
  return parts ? `${parts.hour}:${parts.minute}` : "--:--";
}

export function formatShanghaiDateTime(value: string): string {
  const parts = shanghaiParts(value);
  return parts
    ? `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
    : "时间不可用";
}
