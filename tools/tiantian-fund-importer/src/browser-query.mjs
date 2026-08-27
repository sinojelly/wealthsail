import { accessTokenFromRequest, createQueryPayload } from "./query.mjs";

const DELEGATE_LIST_URL =
  "https://query.1234567.com.cn/queryapi/trading/Query/DelegateList";

export function createApiRequester({ apiContext, accessToken }) {
  return async function requestPage(plan) {
    const payload = createQueryPayload(plan, plan.pageNum, plan.pageSize);
    const response = await apiContext.post(DELEGATE_LIST_URL, {
      headers: {
        "ACCESS-TOKEN": accessToken,
        Accept: "application/json",
        "Content-Type": "application/json",
        Origin: "https://query.1234567.com.cn",
        Referer: "https://query.1234567.com.cn/",
      },
      data: payload,
      timeout: 20_000,
    });
    if (!response.ok()) {
      throw new Error(`天天基金查询接口返回 HTTP ${response.status?.() || "错误"}`);
    }
    return response.json();
  };
}

export function waitForQueryAccessToken(page, { timeoutMs }) {
  return new Promise((resolve, reject) => {
    const onRequest = (request) => {
      const token = accessTokenFromRequest(request);
      if (!token) return;
      cleanup();
      resolve(token);
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("未能从交易查询页面获得临时访问令牌，请确认已经登录后重试"));
    }, timeoutMs);
    const cleanup = () => {
      clearTimeout(timer);
      page.off("request", onRequest);
    };
    page.on("request", onRequest);
  });
}
