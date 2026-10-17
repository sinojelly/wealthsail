export const energyUnit = (unit: string) =>
  unit === "USD/barrel"
    ? "美元/桶"
    : unit === "USD/gallon"
      ? "美元/加仑"
      : unit;
