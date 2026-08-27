function safeSpreadsheetValue(value) {
  const source = value == null ? "" : String(value);
  return /^[=+\-@]/.test(source) ? `'${source}` : source;
}

function escapeCsv(value) {
  const source = safeSpreadsheetValue(value);
  if (/[",\r\n]/.test(source)) {
    return `"${source.replaceAll('"', '""')}"`;
  }
  return source;
}

export function toCsv(records, columns) {
  const lines = [columns.map(escapeCsv).join(",")];
  for (const record of records) {
    lines.push(columns.map((column) => escapeCsv(record[column])).join(","));
  }
  return `\uFEFF${lines.join("\r\n")}\r\n`;
}
