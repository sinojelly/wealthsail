import { useState } from "react";
import type {
  StudyCondition,
  StudyVersion,
  StudyVersionRequest,
} from "../../../api/study-api";
import { studyCatalog as catalog } from "./study-display";
export default function StudyVersionEditor({
  current,
  busy,
  save,
}: {
  current: StudyVersion;
  busy: boolean;
  save: (body: Omit<StudyVersionRequest, "expectedGeneration">) => void;
}) {
  const snapshot = current.snapshot;
  const [reason, setReason] = useState("");
  const [thesis, setThesis] = useState(snapshot.thesis);
  const [days, setDays] = useState(30);
  const [conditions, setConditions] = useState<StudyCondition[]>(
    snapshot.conditions.length
      ? snapshot.conditions
      : [
          {
            metric: "PMI",
            operator: "GTE",
            threshold: "",
            effect: "TRIGGER",
            unit: "指数点",
          },
        ],
  );
  const [transmission, setTransmission] = useState(
    snapshot.transmission.join("\n"),
  );
  const [counter, setCounter] = useState(snapshot.counterFactors.join("\n"));
  const [watch, setWatch] = useState(snapshot.watchFor.join("\n"));
  const [invalidation, setInvalidation] = useState(
    snapshot.invalidationConditions.join("\n"),
  );
  const lines = (value: string) =>
    value
      .split("\n")
      .map((text) => text.trim())
      .filter(Boolean);
  function change(index: number, patch: Partial<StudyCondition>) {
    setConditions((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }
  const invalid =
    [transmission, counter, watch, invalidation].some(
      (text) =>
        lines(text).length > 8 ||
        lines(text).some((line) => line.length > 2000),
    ) ||
    conditions.some(
      (row) =>
        String(row.threshold).trim() === "" ||
        !Number.isFinite(Number(row.threshold)) ||
        Math.abs(Number(row.threshold)) > 1e12 ||
        (row.metric.startsWith("INDEX_") && !row.subjectKey),
    );
  return (
    <form
      aria-label="修订研究条件"
      onSubmit={(event) => {
        event.preventDefault();
        save({
          reason,
          thesis,
          horizonDays: days,
          conditions,
          transmission: lines(transmission),
          counterFactors: lines(counter),
          watchFor: lines(watch),
          invalidationConditions: lines(invalidation),
        });
      }}
    >
      <h4>保存新版本，保留原始研究</h4>
      <label>
        研究假设
        <textarea
          maxLength={2000}
          value={thesis}
          required
          onChange={(event) => setThesis(event.target.value)}
        />
      </label>
      <label>
        传导假设（每行一项）
        <textarea
          value={transmission}
          onChange={(event) => setTransmission(event.target.value)}
        />
      </label>
      <label>
        反方因素（每行一项）
        <textarea
          value={counter}
          onChange={(event) => setCounter(event.target.value)}
        />
      </label>
      <label>
        观察条件（每行一项）
        <textarea
          value={watch}
          onChange={(event) => setWatch(event.target.value)}
        />
      </label>
      <label>
        失效条件（每行一项）
        <textarea
          value={invalidation}
          onChange={(event) => setInvalidation(event.target.value)}
        />
      </label>
      <label>
        新观察天数
        <input
          type="number"
          min={1}
          max={365}
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
        />
      </label>
      {conditions.map((row, i) => (
        <fieldset key={i}>
          <legend>数值条件{i + 1}</legend>
          <label>
            核对指标
            <select
              aria-label={i === 0 ? "核对指标" : `核对指标${i + 1}`}
              value={row.metric}
              onChange={(event) => {
                const metric = event.target.value;
                change(i, {
                  metric,
                  unit: catalog.find(([code]) => code === metric)![2],
                  subjectKey: metric.startsWith("INDEX_") ? "hs300" : null,
                });
              }}
            >
              {catalog.map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {row.metric.startsWith("INDEX_") && (
            <label>
              条件指数
              <select
                aria-label={i === 0 ? "条件指数" : `条件指数${i + 1}`}
                value={row.subjectKey ?? "hs300"}
                onChange={(event) =>
                  change(i, { subjectKey: event.target.value })
                }
              >
                {[
                  ["hs300", "沪深300"],
                  ["a500", "中证A500"],
                  ["zz500", "中证500"],
                ].map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            比较方式
            <select
              value={row.operator}
              onChange={(event) =>
                change(i, {
                  operator: event.target.value as StudyCondition["operator"],
                })
              }
            >
              {[
                ["GT", "大于"],
                ["GTE", "大于等于"],
                ["LT", "小于"],
                ["LTE", "小于等于"],
              ].map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            阈值
            <input
              aria-label={i === 0 ? "阈值" : `阈值${i + 1}`}
              inputMode="decimal"
              required
              value={row.threshold}
              onChange={(event) => change(i, { threshold: event.target.value })}
            />
          </label>
          <small>
            单位：{row.unit}；百分比指标填写3表示3%，不会按0.03解释。
          </small>
          <label>
            满足后的研究影响
            <select
              value={row.effect}
              onChange={(event) =>
                change(i, {
                  effect: event.target.value as StudyCondition["effect"],
                })
              }
            >
              {[
                ["STRENGTHEN", "加强"],
                ["WEAKEN", "减弱"],
                ["TRIGGER", "触发"],
                ["INVALIDATE", "失效"],
              ].map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() =>
              setConditions(conditions.filter((_, index) => index !== i))
            }
          >
            移除此条件
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        disabled={conditions.length >= 8}
        onClick={() =>
          setConditions([
            ...conditions,
            {
              metric: "PMI",
              operator: "GTE",
              threshold: "",
              effect: "TRIGGER",
              unit: "指数点",
            },
          ])
        }
      >
        增加数值条件
      </button>
      <p>自然语言条件保留为人工复盘，不自动转换为可执行规则。</p>
      <label>
        修订原因
        <textarea
          aria-label="修订原因"
          required
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <button
        disabled={
          busy ||
          invalid ||
          !reason.trim() ||
          !thesis.trim() ||
          !Number.isInteger(days) ||
          days < 1 ||
          days > 365
        }
      >
        保存新版本
      </button>
    </form>
  );
}
