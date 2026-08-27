import { normalizeTransaction } from "./domain.mjs";

const PAGE_SIZE = 20;

function assertSuccessfulResponse(response) {
  if ([1401, 3001, 3002].includes(Number(response?.code))) {
    throw new Error("登录已过期或访问令牌失效，请重新运行采集工具并登录");
  }
  if (Number(response?.code) !== 1200 || !response?.data) {
    throw new Error(`天天基金查询失败：${response?.message || response?.code || "未知错误"}`);
  }
}

export async function collectTransactions({ plans, requestPage, delay, onProgress = () => {} }) {
  const records = [];

  for (const plan of plans) {
    let pageNum = 1;
    let totalPages = 1;
    do {
      const response = await requestPage({ ...plan, pageNum, pageSize: PAGE_SIZE });
      assertSuccessfulResponse(response);

      const pageRecords = Array.isArray(response.data.list) ? response.data.list : [];
      for (const raw of pageRecords) {
        records.push(
          normalizeTransaction(raw, {
            windowStart: plan.windowStart,
            windowEnd: plan.windowEnd,
            page: pageNum,
          }),
        );
      }

      const totalCount = Number(response.data.totalCount || pageRecords.length);
      totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
      onProgress({ plan, pageNum, totalPages, pageRecordCount: pageRecords.length });
      pageNum += 1;
      if (pageNum <= totalPages) {
        await delay();
      }
    } while (pageNum <= totalPages);
  }

  return records;
}
