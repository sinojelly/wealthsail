import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DailyResearch } from "../../api/investment";
import DailyResearchReport from "./components/DailyResearchReport";

const currentRunId = "d7188c05-d288-4a4c-973e-a8b47549cc74";
const previousRunId = "6283bff5-bd75-4dd4-8444-e44756225cf0";
const daily = (overrides: Partial<DailyResearch> = {}): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "FAILED",
  runId: currentRunId,
  scheduledFor: "2026-11-01T23:00:00Z",
  startedAt: "2026-11-01T23:01:00Z",
  completedAt: "2026-11-01T23:05:00Z",
  asOf: "2026-10-31T23:05:00Z",
  nextRunAt: "2026-11-02T23:00:00Z",
  errorClass: "ResearchValidationError",
  reportRunId: previousRunId,
  report: {
    asOf: "2026-10-31T23:05:00Z",
    summary: "上一份有效报告",
    coverage: [],
    sources: [],
    events: [],
    upcoming: [],
    energy: [],
  },
  ...overrides,
});

async function openDiagnostics(data: DailyResearch) {
  render(<DailyResearchReport data={data} />);
  await userEvent.click(screen.getByText("执行与报告状态"));
}

it("explains a verified report-validation failure while retaining the historical report and error class", async () => {
  const data = Object.assign(daily(), {
    failureStage: "REPORT_VALIDATION",
    failureReasonCode: "SCHEMA_VALIDATION_FAILED",
    failureItemIndex: 0,
  });
  const original = JSON.stringify(data);
  await openDiagnostics(data);
  const diagnosis = within(screen.getByRole("group", { name: "研究失败诊断" }));
  expect(diagnosis.getByText("报告未通过业务校验")).toBeVisible();
  expect(diagnosis.getByText("失败阶段：报告业务校验")).toBeVisible();
  expect(diagnosis.getByText(/报告结构不符合要求/)).toBeVisible();
  expect(
    diagnosis.getByText("原因代码：SCHEMA_VALIDATION_FAILED"),
  ).toBeVisible();
  expect(diagnosis.getByText("失败条目：数组索引 0（从0开始）")).toBeVisible();
  expect(diagnosis.getByText(`研究执行ID：${currentRunId}`)).toBeVisible();
  expect(screen.getByText("ResearchValidationError")).toBeVisible();
  expect(screen.getByText(/当前显示历史报告及原始日期/)).toBeVisible();
  expect(
    screen.getByText(new RegExp(`当前展示报告：${previousRunId}`)),
  ).toBeVisible();
  expect(screen.queryByText(/推理成功|模型已成功/)).not.toBeInTheDocument();
  expect(JSON.stringify(data)).toBe(original);
});

it("describes a model timeout without claiming successful inference or business validation", async () => {
  await openDiagnostics(
    Object.assign(daily({ state: "UNCERTAIN", errorClass: "TimeoutError" }), {
      failureStage: "MODEL_EXECUTION",
      failureReasonCode: "TIMEOUT",
    }),
  );
  const diagnosis = within(screen.getByRole("group", { name: "研究失败诊断" }));
  expect(diagnosis.getByText("失败阶段：模型执行")).toBeVisible();
  expect(diagnosis.getByText(/执行超时/)).toBeVisible();
  expect(diagnosis.getByText("原因代码：TIMEOUT")).toBeVisible();
  expect(
    screen.queryByText(/报告未通过业务校验|推理成功/),
  ).not.toBeInTheDocument();
});

it("does not render unknown diagnostic fields or treat them as a confirmed validation failure", async () => {
  await openDiagnostics(
    Object.assign(daily(), {
      failureStage: "REPORT_VALIDATION",
      failureReasonCode: "token=private-diagnostic",
      failureRuleId: "<script>private-rule</script>",
      failureItemIndex: -1,
    }),
  );
  const diagnosis = within(screen.getByRole("group", { name: "研究失败诊断" }));
  expect(diagnosis.getByText("失败原因：原因待核查")).toBeVisible();
  expect(
    screen.queryByText(/private-diagnostic|private-rule/),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/报告未通过业务校验|失败条目：/),
  ).not.toBeInTheDocument();
});

it("keeps legacy error-class diagnostics without deriving structured failure facts from a retained report", async () => {
  const data = daily();
  Object.assign(data.report!, {
    failureStage: "REPORT_VALIDATION",
    failureReasonCode: "SCHEMA_VALIDATION_FAILED",
  });
  await openDiagnostics(data);
  expect(screen.getByText("ResearchValidationError")).toBeVisible();
  expect(screen.getByText(/当前显示历史报告及原始日期/)).toBeVisible();
  expect(
    screen.queryByText(/报告未通过业务校验|原因代码：/),
  ).not.toBeInTheDocument();
});

it.each(["READY", "RUNNING"] as const)(
  "does not show stale failure metadata when the current pipeline is %s",
  async (state) => {
    await openDiagnostics(
      Object.assign(
        daily({
          pipeline: { id: "current-pipeline", state, stage: "MODEL_RUNNING" },
        }),
        {
          failureStage: "REPORT_VALIDATION",
          failureReasonCode: "SCHEMA_VALIDATION_FAILED",
        },
      ),
    );
    expect(
      screen.queryByRole("group", { name: "研究失败诊断" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("报告未通过业务校验")).not.toBeInTheDocument();
  },
);

it.each([-1, 0.5, 10000, Number.NaN, Number.POSITIVE_INFINITY])(
  "does not display an invalid array index: %s",
  async (index) => {
    await openDiagnostics(Object.assign(daily(), { failureItemIndex: index }));
    expect(screen.queryByText(/失败条目：/)).not.toBeInTheDocument();
  },
);

it.each([
  ["INDUSTRY_REVIEW_DUPLICATE", "行业反方核查记录重复"],
  ["INDUSTRY_REVIEW_COVERAGE_MISSING", "候选行业反方核查覆盖不完整"],
  ["INDUSTRY_RISK_CHECK_DUPLICATE", "行业风险核查项重复"],
  ["INDUSTRY_RISK_CHECK_MISSING", "行业风险核查项缺失"],
  ["INDUSTRY_RISK_SOURCE_MISSING", "行业风险证据缺少来源"],
  ["INDUSTRY_CYCLE_SOURCE_INVALID", "产业周期证据引用的来源无效"],
  ["DIRECTION_INDUSTRY_REVIEW_UNKNOWN", "价值方向引用了未知行业核查记录"],
  ["DIRECTION_DECLARED_INDUSTRY_UNKNOWN", "价值方向声明了未知行业"],
  ["DIRECTION_PLAN_SECTOR_UNREVIEWED", "基金计划引用的行业尚未完成反方核查"],
  ["INDUSTRY_REVIEW_BUDGET_EXCEEDED", "候选行业数量超过核查预算"],
  ["DIRECTION_MACRO_REFERENCE_UNKNOWN", "价值方向引用了未知宏观指标"],
  ["DIRECTION_CALIBRATION_RULE_UNKNOWN", "价值方向引用了未知校准规则"],
  ["DIRECTION_EVIDENCE_INSUFFICIENT", "价值方向的证据不足"],
])(
  "maps the fixed validation rule to a readable cause: %s",
  async (rule, label) => {
    await openDiagnostics(
      Object.assign(daily(), {
        failureStage: "REPORT_VALIDATION",
        failureRuleId: rule,
      }),
    );
    const diagnosis = within(
      screen.getByRole("group", { name: "研究失败诊断" }),
    );
    expect(diagnosis.getByText(`失败原因：${label}`)).toBeVisible();
    expect(diagnosis.getByText(`校验规则：${rule}`)).toBeVisible();
    expect(diagnosis.getByText("报告未通过业务校验")).toBeVisible();
  },
);

it.each([
  ["DIRECTION_VALIDATION_FAILED", "价值方向未通过校验", true],
  ["FUND_PLAN_INVALID", "基金计划不符合要求", true],
  ["SCHEDULE_DATE_INVALID", "计划事件日期无效", true],
  ["SCHEMA_VALIDATION_FAILED", "报告结构不符合要求", true],
  ["SOURCE_DATE_INVALID", "来源发布日期无效", true],
  ["SOURCE_REFERENCE_INVALID", "来源引用无效", true],
  ["PAYLOAD_VALIDATION_FAILED", "报告内容未通过校验", true],
  ["TIMEOUT", "执行超时", false],
  ["NETWORK_UNAVAILABLE", "网络暂不可用", false],
  ["UNEXPECTED_ERROR", "执行发生未分类异常", false],
  ["CANCELLED", "执行已取消", false],
  ["PIPELINE_RUNTIME_EXPIRED", "研究流程超过运行时限", false],
] as const)(
  "maps only a known reason and does not mistake an execution issue for validation rejection: %s",
  async (reason, label, validation) => {
    await openDiagnostics(
      Object.assign(daily(), {
        failureStage: "REPORT_VALIDATION",
        failureReasonCode: reason,
      }),
    );
    const diagnosis = within(
      screen.getByRole("group", { name: "研究失败诊断" }),
    );
    expect(diagnosis.getByText(`失败原因：${label}`)).toBeVisible();
    expect(diagnosis.getByText(`原因代码：${reason}`)).toBeVisible();
    if (validation)
      expect(diagnosis.getByText("报告未通过业务校验")).toBeVisible();
    else
      expect(
        diagnosis.queryByText("报告未通过业务校验"),
      ).not.toBeInTheDocument();
  },
);

it.each([
  ["SOURCE_VERIFICATION", "来源核验"],
  ["PERSISTING", "保存报告"],
])(
  "shows the fixed %s stage without inferring model success",
  async (stage, label) => {
    await openDiagnostics(
      Object.assign(daily(), {
        failureStage: stage,
        failureReasonCode: "TIMEOUT",
      }),
    );
    expect(screen.getByText(`失败阶段：${label}`)).toBeVisible();
    expect(
      screen.queryByText(/报告未通过业务校验|推理成功/),
    ).not.toBeInTheDocument();
  },
);

it.each(["constructor", "__proto__", "FUTURE_DIAGNOSTIC_CODE"])(
  "does not accept an unknown value as a diagnostic mapping: %s",
  async (code) => {
    await openDiagnostics(
      Object.assign(daily(), {
        failureStage: code,
        failureReasonCode: code,
        failureRuleId: code,
      }),
    );
    const diagnosis = within(
      screen.getByRole("group", { name: "研究失败诊断" }),
    );
    expect(diagnosis.getByText("失败原因：原因待核查")).toBeVisible();
    expect(diagnosis.getByText("失败阶段：阶段待核查")).toBeVisible();
    expect(diagnosis.queryByText(new RegExp(code))).not.toBeInTheDocument();
  },
);

it("does not use a retained run ID for a new pipeline without a research run", async () => {
  await openDiagnostics(
    Object.assign(
      daily({
        runId: previousRunId,
        pipeline: {
          id: "new-pipeline",
          state: "FAILED",
          stage: "VERIFYING_OUTPUT",
          researchRunId: null,
        },
      }),
      { failureStage: "SOURCE_VERIFICATION", failureReasonCode: "TIMEOUT" },
    ),
  );
  const diagnosis = within(screen.getByRole("group", { name: "研究失败诊断" }));
  expect(diagnosis.getByText("研究执行ID：身份未记录")).toBeVisible();
  expect(
    diagnosis.queryByText(new RegExp(previousRunId)),
  ).not.toBeInTheDocument();
});

it.each([
  ["absent", undefined],
  ["null", null],
  ["empty", []],
  [
    "unknown and hostile",
    [
      "UNKNOWN_SECTOR",
      "<script>secret-sector</script>",
      "__proto__",
      "constructor",
    ],
  ],
  ["non-array", "MODEL_GENERATED_SECTOR"],
  ["object", { value: "PRIVATE_SECTOR" }],
])("does not expose %s industry diagnostic values", async (_label, codes) => {
  await openDiagnostics(
    Object.assign(daily(), { failureIndustryCodes: codes }),
  );
  const diagnosis = within(screen.getByRole("group", { name: "研究失败诊断" }));
  expect(diagnosis.queryByText(/相关行业代码：/)).not.toBeInTheDocument();
  expect(
    screen.queryByText(
      /UNKNOWN_SECTOR|secret-sector|__proto__|constructor|MODEL_GENERATED_SECTOR|PRIVATE_SECTOR/,
    ),
  ).not.toBeInTheDocument();
});

it("does not borrow industry diagnostic codes from a retained report", async () => {
  const data = daily();
  Object.assign(data.report!, {
    failureIndustryCodes: ["US_TECH"],
  });
  await openDiagnostics(data);
  expect(screen.queryByText(/相关行业代码：|US_TECH/)).not.toBeInTheDocument();
});

it("displays only standard industry codes in sorted unique order without mutating the response", async () => {
  const data = Object.assign(daily(), {
    failureIndustryCodes: [
      "ROBOT",
      "AI",
      "BANK",
      "ROBOT",
      "UNKNOWN_SECTOR",
      "<script>secret-sector</script>",
      "robot",
      null,
      42,
      "__proto__",
    ],
  });
  const original = JSON.stringify(data);
  await openDiagnostics(data);
  const diagnosis = within(screen.getByRole("group", { name: "研究失败诊断" }));
  expect(diagnosis.getByText("相关行业代码：AI、BANK、ROBOT")).toBeVisible();
  expect(
    screen.queryByText(/UNKNOWN_SECTOR|secret-sector|__proto__/),
  ).not.toBeInTheDocument();
  expect(JSON.stringify(data)).toBe(original);
});

it("limits the sorted standard industry diagnosis to ten unique codes", async () => {
  await openDiagnostics(
    Object.assign(daily(), {
      failureIndustryCodes: [
        "US_TECH",
        "SOLID_BATTERY",
        "SATELLITE",
        "ROBOT",
        "POWER",
        "PCB",
        "MEMORY",
        "HK_TECH",
        "GOLD",
        "FINTECH",
        "CHEMICAL",
        "ROBOT",
      ],
    }),
  );
  const diagnosis = within(screen.getByRole("group", { name: "研究失败诊断" }));
  expect(
    diagnosis.getByText(
      "相关行业代码：CHEMICAL、FINTECH、GOLD、HK_TECH、MEMORY、PCB、POWER、ROBOT、SATELLITE、SOLID_BATTERY",
    ),
  ).toBeVisible();
  expect(diagnosis.queryByText(/US_TECH/)).not.toBeInTheDocument();
});
