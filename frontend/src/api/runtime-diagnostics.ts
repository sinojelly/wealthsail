/** Closed backend runtime reasons shared by reports and operations. */
export const runtimeFailureReasons = {
  CLI_FAILED_UNCLASSIFIED: "CLI 退出失败，具体原因未识别",
  AUTHENTICATION_FAILED: "供应商认证失败",
  ACCESS_DENIED: "供应商拒绝访问",
  RATE_LIMITED: "供应商请求限流",
  QUOTA_EXHAUSTED: "供应商额度已耗尽",
  PROVIDER_UNAVAILABLE: "供应商服务暂不可用",
  NETWORK_UNAVAILABLE: "网络连接不可用",
  TLS_ERROR: "TLS 证书或握手失败",
  CLI_OUTPUT_INVALID: "模型输出格式无法读取",
  CLI_OUTPUT_LIMIT: "模型输出超出读取上限",
  CLI_REPORT_MISSING: "模型输出未包含报告",
  CLI_MODEL_TIMEOUT: "模型执行达到超时上限",
  RUNNER_HTTP_ERROR: "执行器 HTTP 请求失败",
  TRANSPORT_TIMEOUT: "执行器通信超时",
  TRANSPORT_DISCONNECTED: "执行器连接已断开",
  ACCOUNT_RPC_FAILED: "账户 RPC 调用失败，具体原因未识别",
  ACCOUNT_RPC_TIMEOUT: "账户请求超时，未收到响应",
  RPC_RESPONSE_TIMEOUT: "RPC 返回超时错误",
  WORKSPACE_ROUTING_TIMEOUT: "工作区路由发现超时",
  ACCOUNT_UNAVAILABLE: "账户信息暂不可用",
  ACCOUNT_UNAUTHENTICATED: "Codex 尚未登录或认证已失效",
  TRANSPORT_FAILED_UNCLASSIFIED: "执行器通信失败，具体原因未识别",
} as const;

export type RuntimeFailureReasonCode = keyof typeof runtimeFailureReasons;
