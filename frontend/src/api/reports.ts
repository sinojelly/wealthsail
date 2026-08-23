export const reportQueryKeys = {
  all: ["reports"] as const,
  detail: (id: string) => ["reports", id] as const,
};
