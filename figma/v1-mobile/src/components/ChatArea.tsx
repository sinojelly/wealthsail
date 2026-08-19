import { useRef, useEffect, useState } from "react";
import {
  Message,
  StockData,
  Sentiment,
  initialMessages,
  stocks,
} from "../data/mockData";
import Sparkline from "./Sparkline";

const sentimentMap: Record<
  Sentiment,
  { label: string; color: string; bg: string; dot: string }
> = {
  bullish: {
    label: "偏暖",
    color: "#10b981",
    bg: "rgba(16,185,129,0.1)",
    dot: "▲",
  },
  bearish: {
    label: "防御",
    color: "#f43f5e",
    bg: "rgba(244,63,94,0.1)",
    dot: "▼",
  },
  neutral: {
    label: "中性",
    color: "#f59e0b",
    bg: "rgba(245,158,11,0.1)",
    dot: "●",
  },
};

interface ChatAreaProps {
  channel: string;
  onStockFocus: (stock: StockData) => void;
}

function PreMarketCard({
  stocks: cardStocks,
  timestamp,
  onStockFocus,
}: {
  stocks: StockData[];
  timestamp: string;
  onStockFocus: (s: StockData) => void;
}) {
  const today = new Date().toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });

  return (
    <div className="flex gap-3">
      <div
        className="w-7 h-7 rounded-full flex items-center justify-center text-sm flex-shrink-0 mt-0.5 border"
        style={{
          background: "rgba(34,211,238,0.08)",
          borderColor: "rgba(34,211,238,0.2)",
        }}
      >
        🤖
      </div>
      <div className="flex-1 max-w-2xl">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-semibold" style={{ color: "#22d3ee" }}>
            AlphaGhost
          </span>
          <span className="text-[10px] text-slate-600 font-mono">{timestamp}</span>
        </div>

        <div
          className="rounded-xl overflow-hidden border"
          style={{
            background: "#0e1b32",
            borderColor: "rgba(255,255,255,0.08)",
          }}
        >
          {/* Header */}
          <div
            className="px-4 py-3 border-b flex items-center justify-between"
            style={{ borderColor: "rgba(255,255,255,0.06)" }}
          >
            <div className="flex items-center gap-2.5">
              <span className="text-base">📊</span>
              <div>
                <div className="text-sm font-semibold text-slate-200">
                  今日盘前推演
                </div>
                <div className="text-[10px] text-slate-600 font-mono">
                  {today} · 08:30 定时推送
                </div>
              </div>
            </div>
            <div
              className="text-[10px] px-2 py-1 rounded-full font-mono"
              style={{
                background: "rgba(16,185,129,0.12)",
                color: "#10b981",
              }}
            >
              ● 自动
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                  {["标的", "现价 / 涨跌", "支撑位", "压力位", "走势", "情绪"].map(
                    (h) => (
                      <th
                        key={h}
                        className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600 text-left last:text-center"
                      >
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody>
                {cardStocks.map((s, i) => {
                  const sent = sentimentMap[s.sentiment];
                  const pos = s.changePercent >= 0;
                  return (
                    <tr
                      key={s.symbol}
                      onClick={() => onStockFocus(s)}
                      className="cursor-pointer transition-colors hover:bg-white/4"
                      style={{
                        borderBottom:
                          i < cardStocks.length - 1
                            ? "1px solid rgba(255,255,255,0.04)"
                            : "none",
                      }}
                    >
                      <td className="px-4 py-3">
                        <div className="text-sm font-medium text-slate-200">
                          {s.name}
                        </div>
                        <div className="text-[10px] text-slate-600 font-mono">
                          {s.exchange}:{s.symbol}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div
                          className="text-sm font-mono font-semibold"
                          style={{ color: pos ? "#10b981" : "#f43f5e" }}
                        >
                          ¥{s.price.toFixed(2)}
                        </div>
                        <div
                          className="text-[10px] font-mono"
                          style={{
                            color: pos
                              ? "rgba(16,185,129,0.7)"
                              : "rgba(244,63,94,0.7)",
                          }}
                        >
                          {pos ? "+" : ""}
                          {s.changePercent.toFixed(2)}%
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm font-mono text-slate-500">
                        {s.support.toFixed(2)}
                      </td>
                      <td className="px-4 py-3 text-sm font-mono text-slate-500">
                        {s.resistance.toFixed(2)}
                      </td>
                      <td className="px-4 py-3">
                        <Sparkline
                          data={s.sparkline}
                          width={64}
                          height={24}
                          positive={pos}
                        />
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold"
                          style={{
                            color: sent.color,
                            background: sent.bg,
                          }}
                        >
                          {sent.dot} {sent.label}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Footer actions */}
          <div
            className="px-4 py-3 border-t flex flex-wrap gap-2"
            style={{ borderColor: "rgba(255,255,255,0.05)" }}
          >
            <button
              className="px-3 py-1.5 text-xs rounded-lg border transition-colors hover:bg-emerald/10"
              style={{ color: "#10b981", borderColor: "rgba(16,185,129,0.3)" }}
            >
              ⚡ 一键更新持仓
            </button>
            <button
              className="px-3 py-1.5 text-xs rounded-lg border transition-colors hover:bg-white/5"
              style={{
                color: "#94a3b8",
                borderColor: "rgba(255,255,255,0.1)",
              }}
            >
              🔍 深度分析
            </button>
            <button
              className="px-3 py-1.5 text-xs rounded-lg border transition-colors hover:bg-white/5"
              style={{
                color: "#94a3b8",
                borderColor: "rgba(255,255,255,0.1)",
              }}
            >
              📤 导出报告
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function StockAnalysisCard({
  stocks: cardStocks,
  content,
  timestamp,
  onStockFocus,
}: {
  stocks: StockData[];
  content: string;
  timestamp: string;
  onStockFocus: (s: StockData) => void;
}) {
  const s = cardStocks[0];
  const pos = s.changePercent >= 0;

  const lines = content.split("\n");

  return (
    <div className="flex gap-3">
      <div
        className="w-7 h-7 rounded-full flex items-center justify-center text-sm flex-shrink-0 mt-0.5 border"
        style={{
          background: "rgba(34,211,238,0.08)",
          borderColor: "rgba(34,211,238,0.2)",
        }}
      >
        🤖
      </div>
      <div className="flex-1 max-w-2xl">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-semibold" style={{ color: "#22d3ee" }}>
            AlphaGhost
          </span>
          <span className="text-[10px] text-slate-600 font-mono">{timestamp}</span>
        </div>

        <div
          className="rounded-xl overflow-hidden border"
          style={{
            background: "#0e1b32",
            borderColor: "rgba(255,255,255,0.08)",
          }}
        >
          {/* Stock header */}
          <div
            className="px-4 py-3 border-b flex items-center justify-between cursor-pointer hover:bg-white/3 transition-colors"
            style={{ borderColor: "rgba(255,255,255,0.06)" }}
            onClick={() => onStockFocus(s)}
          >
            <div>
              <div className="text-sm font-semibold text-slate-200">
                {s.name}
              </div>
              <div className="text-[10px] text-slate-600 font-mono">
                {s.exchange}:{s.symbol}
              </div>
            </div>
            <div className="flex items-center gap-4">
              <Sparkline
                data={s.sparkline}
                width={80}
                height={28}
                positive={pos}
              />
              <div className="text-right">
                <div
                  className="text-sm font-mono font-bold"
                  style={{ color: pos ? "#10b981" : "#f43f5e" }}
                >
                  ¥{s.price.toFixed(2)}
                </div>
                <div
                  className="text-[10px] font-mono"
                  style={{
                    color: pos
                      ? "rgba(16,185,129,0.7)"
                      : "rgba(244,63,94,0.7)",
                  }}
                >
                  {pos ? "+" : ""}
                  {s.changePercent.toFixed(2)}%
                </div>
              </div>
            </div>
          </div>

          {/* Analysis */}
          <div className="px-4 py-4 space-y-2 text-sm text-slate-400 leading-relaxed">
            {lines.map((line, i) => {
              if (line.startsWith("**") && line.endsWith("**")) {
                return (
                  <div
                    key={i}
                    className="text-slate-200 font-semibold text-xs uppercase tracking-wider pt-2 first:pt-0"
                    style={{ color: "#94a3b8" }}
                  >
                    {line.replace(/\*\*/g, "")}
                  </div>
                );
              }
              if (line.trim()) {
                return (
                  <div key={i} className="flex gap-2">
                    <span className="text-slate-700 mt-1 flex-shrink-0">·</span>
                    <span
                      dangerouslySetInnerHTML={{
                        __html: line.replace(
                          /¥[\d,]+(\.\d+)?/g,
                          (m) =>
                            `<span style="color:#22d3ee;font-family:var(--font-mono);font-size:12px">${m}</span>`
                        ),
                      }}
                    />
                  </div>
                );
              }
              return null;
            })}
          </div>

          {/* Footer */}
          <div
            className="px-4 py-3 border-t flex gap-2"
            style={{ borderColor: "rgba(255,255,255,0.05)" }}
          >
            <button
              className="px-3 py-1.5 text-xs rounded-lg border transition-colors hover:bg-white/5"
              style={{
                color: "#94a3b8",
                borderColor: "rgba(255,255,255,0.1)",
              }}
            >
              🔍 更深度分析
            </button>
            <button
              className="px-3 py-1.5 text-xs rounded-lg border transition-colors hover:bg-rose/10"
              style={{
                color: "#f43f5e",
                borderColor: "rgba(244,63,94,0.2)",
              }}
            >
              🔔 设置止损提醒
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function TradeConfirmCard({
  data,
  timestamp,
}: {
  data: NonNullable<Message["tradeData"]>;
  timestamp: string;
}) {
  const isBuy = data.action === "buy";
  const total = data.shares * data.price;

  return (
    <div className="flex gap-3">
      <div
        className="w-7 h-7 rounded-full flex items-center justify-center text-sm flex-shrink-0 mt-0.5 border"
        style={{
          background: "rgba(34,211,238,0.08)",
          borderColor: "rgba(34,211,238,0.2)",
        }}
      >
        🤖
      </div>
      <div className="flex-1 max-w-sm">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-semibold" style={{ color: "#22d3ee" }}>
            AlphaGhost
          </span>
          <span className="text-[10px] text-slate-600 font-mono">{timestamp}</span>
        </div>

        <div
          className="rounded-xl border p-4"
          style={{
            background: "#0e1b32",
            borderColor: isBuy
              ? "rgba(16,185,129,0.2)"
              : "rgba(244,63,94,0.2)",
          }}
        >
          <div className="flex items-center gap-2 mb-3">
            <span
              className="text-xs font-bold px-2 py-0.5 rounded"
              style={{
                background: isBuy
                  ? "rgba(16,185,129,0.15)"
                  : "rgba(244,63,94,0.15)",
                color: isBuy ? "#10b981" : "#f43f5e",
              }}
            >
              ✓ 已记录{isBuy ? "买入" : "卖出"}
            </span>
          </div>
          <div className="space-y-1.5 text-xs font-mono">
            <div className="flex justify-between">
              <span className="text-slate-500">标的</span>
              <span className="text-slate-200">
                {data.name} ({data.symbol})
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">方向</span>
              <span style={{ color: isBuy ? "#10b981" : "#f43f5e" }}>
                {isBuy ? "买入" : "卖出"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">股数</span>
              <span className="text-slate-200">{data.shares} 股</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">成本价</span>
              <span className="text-slate-200">¥{data.price.toFixed(2)}</span>
            </div>
            <div
              className="flex justify-between pt-1.5 mt-1.5"
              style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}
            >
              <span className="text-slate-500">总金额</span>
              <span
                className="font-semibold"
                style={{ color: "#22d3ee" }}
              >
                ¥{total.toLocaleString("zh-CN")}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ThinkingBubble({ content }: { content?: string }) {
  return (
    <div className="flex gap-3">
      <div
        className="w-7 h-7 rounded-full flex items-center justify-center text-sm flex-shrink-0 mt-0.5 border"
        style={{
          background: "rgba(34,211,238,0.08)",
          borderColor: "rgba(34,211,238,0.2)",
        }}
      >
        🤖
      </div>
      <div
        className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs"
        style={{
          background: "#0e1b32",
          color: "#64748b",
          border: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <span className="inline-flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="w-1 h-1 rounded-full"
              style={{
                background: "#10b981",
                animation: `pulse 1.4s ease-in-out ${i * 0.2}s infinite`,
              }}
            />
          ))}
        </span>
        <span>{content ?? "思考中..."}</span>
      </div>
    </div>
  );
}

function UserMessage({
  content,
  timestamp,
}: {
  content: string;
  timestamp: string;
}) {
  const isCommand = content.startsWith("/");
  return (
    <div className="flex justify-end">
      <div className="max-w-md">
        <div className="flex items-center justify-end gap-2 mb-1.5">
          <span className="text-[10px] text-slate-600 font-mono">{timestamp}</span>
          <span className="text-xs font-semibold text-slate-500">我</span>
        </div>
        <div
          className="px-4 py-2.5 rounded-xl text-sm"
          style={{
            background: isCommand ? "rgba(34,211,238,0.08)" : "#162444",
            border: isCommand
              ? "1px solid rgba(34,211,238,0.2)"
              : "1px solid rgba(255,255,255,0.08)",
            color: isCommand ? "#22d3ee" : "#cbd5e1",
            fontFamily: isCommand ? "var(--font-mono)" : "inherit",
          }}
        >
          {content}
        </div>
      </div>
    </div>
  );
}

const SLASH_COMMANDS = [
  { cmd: "/buy", desc: "买入股票", example: "/buy 00700 375 500" },
  { cmd: "/sell", desc: "卖出股票", example: "/sell 300750 228 100" },
  { cmd: "/analyse", desc: "深度分析", example: "/analyse 贵州茅台" },
  { cmd: "/report", desc: "生成今日报告", example: "/report" },
];

export default function ChatArea({ channel, onStockFocus }: ChatAreaProps) {
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [input, setInput] = useState("");
  const [showSlash, setShowSlash] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleInput = (val: string) => {
    setInput(val);
    setShowSlash(val.startsWith("/") && val.length <= 8);
  };

  const send = () => {
    if (!input.trim()) return;
    const ts = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
    const userMsg: Message = {
      id: Date.now().toString(),
      role: "user",
      type: "text",
      content: input,
      timestamp: ts,
    };
    const thinkingMsg: Message = {
      id: Date.now() + "-t",
      role: "agent",
      type: "thinking",
      content: "正在处理您的请求...",
      timestamp: ts,
    };
    setMessages((m) => [...m, userMsg, thinkingMsg]);
    setInput("");
    setShowSlash(false);

    setTimeout(() => {
      const replyMsg: Message = {
        id: Date.now() + "-r",
        role: "agent",
        type: "stock-analysis",
        timestamp: new Date().toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        }),
        stocks: [stocks["09988"]],
        content:
          "**行情概览**\n阿里巴巴今日上涨 2.55%，受港股科技板块整体回暖带动，南向资金净流入明显。\n**技术面**\n当前价 ¥86.4 突破短期均线压制，若能站稳 ¥88 将打开上行空间至 ¥92 压力位\n目前支撑位在 ¥82，建议以此为止损参考\n**操作建议**\n可轻仓参与，仓位控制在 10% 以内，目标 ¥92",
      };
      setMessages((m) => [
        ...m.filter((x) => x.id !== thinkingMsg.id),
        replyMsg,
      ]);
    }, 2000);
  };

  const channelTitle = channel === "premarket" ? "盘前推演" : "个股诊断";

  return (
    <div className="flex-1 flex flex-col min-w-0 h-full">
      {/* Header */}
      <div
        className="flex items-center justify-between px-5 py-3.5 border-b flex-shrink-0"
        style={{ borderColor: "rgba(255,255,255,0.06)" }}
      >
        <div className="flex items-center gap-2.5">
          <span className="text-base">{channel === "premarket" ? "📊" : "🔍"}</span>
          <div>
            <div className="text-sm font-semibold text-slate-200">
              {channelTitle}
            </div>
            <div className="text-[10px] text-slate-600 font-mono">
              2026-08-19 · {messages.filter((m) => m.role === "agent").length} 条消息
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <div
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: "#10b981" }}
          />
          <span className="text-[11px] text-slate-500">Agent 就绪</span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-6 space-y-6">
        {/* Date separator */}
        <div className="flex items-center gap-3">
          <div
            className="flex-1 h-px"
            style={{ background: "rgba(255,255,255,0.05)" }}
          />
          <span className="text-[10px] text-slate-600 font-mono px-2">
            2026-08-19 交易日
          </span>
          <div
            className="flex-1 h-px"
            style={{ background: "rgba(255,255,255,0.05)" }}
          />
        </div>

        {messages.map((msg) => {
          if (msg.role === "user") {
            return (
              <UserMessage
                key={msg.id}
                content={msg.content!}
                timestamp={msg.timestamp}
              />
            );
          }
          if (msg.type === "premarket-report" && msg.stocks) {
            return (
              <PreMarketCard
                key={msg.id}
                stocks={msg.stocks}
                timestamp={msg.timestamp}
                onStockFocus={onStockFocus}
              />
            );
          }
          if (msg.type === "stock-analysis" && msg.stocks) {
            return (
              <StockAnalysisCard
                key={msg.id}
                stocks={msg.stocks}
                content={msg.content!}
                timestamp={msg.timestamp}
                onStockFocus={onStockFocus}
              />
            );
          }
          if (msg.type === "trade-confirm" && msg.tradeData) {
            return (
              <TradeConfirmCard
                key={msg.id}
                data={msg.tradeData}
                timestamp={msg.timestamp}
              />
            );
          }
          if (msg.type === "thinking") {
            return (
              <ThinkingBubble key={msg.id} content={msg.content} />
            );
          }
          return null;
        })}
        <div ref={bottomRef} />
      </div>

      {/* Slash command suggestions */}
      {showSlash && (
        <div
          className="mx-5 mb-2 rounded-xl border overflow-hidden"
          style={{
            background: "#0e1b32",
            borderColor: "rgba(255,255,255,0.1)",
          }}
        >
          <div className="px-3 py-1.5 text-[10px] text-slate-600 uppercase tracking-wider font-semibold border-b" style={{ borderColor: "rgba(255,255,255,0.05)" }}>
            快捷指令
          </div>
          {SLASH_COMMANDS.filter((c) =>
            c.cmd.startsWith(input)
          ).map((c) => (
            <button
              key={c.cmd}
              className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-white/5 transition-colors"
              onClick={() => {
                setInput(c.cmd + " ");
                setShowSlash(false);
                inputRef.current?.focus();
              }}
            >
              <span className="font-mono text-xs" style={{ color: "#22d3ee" }}>
                {c.cmd}
              </span>
              <span className="text-xs text-slate-500">{c.desc}</span>
              <span className="text-[10px] font-mono text-slate-700 ml-auto">
                {c.example}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Input bar */}
      <div
        className="px-5 py-4 border-t flex-shrink-0"
        style={{ borderColor: "rgba(255,255,255,0.06)" }}
      >
        <div
          className="flex items-end gap-3 rounded-xl border px-4 py-3 transition-colors focus-within:border-cyan/30"
          style={{
            background: "#0e1b32",
            borderColor: "rgba(255,255,255,0.1)",
          }}
        >
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => handleInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
              if (e.key === "Escape") setShowSlash(false);
            }}
            placeholder="输入问题，或键入 / 使用快捷指令..."
            rows={1}
            className="flex-1 bg-transparent text-sm text-slate-300 resize-none outline-none placeholder:text-slate-700 leading-relaxed"
            style={{ maxHeight: "120px", fontFamily: "inherit" }}
          />
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className="text-[10px] text-slate-700 font-mono hidden sm:block">
              ↵ 发送
            </span>
            <button
              onClick={send}
              disabled={!input.trim()}
              className="w-7 h-7 rounded-lg flex items-center justify-center text-xs transition-all disabled:opacity-30"
              style={{
                background: input.trim()
                  ? "#10b981"
                  : "rgba(255,255,255,0.06)",
                color: input.trim() ? "#fff" : "#64748b",
              }}
            >
              ↑
            </button>
          </div>
        </div>
        <div className="flex items-center gap-3 mt-2 px-1">
          <span className="text-[10px] text-slate-700">
            输入 /buy · /sell · /analyse 快速操作
          </span>
        </div>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 80%, 100% { opacity: 0.2; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </div>
  );
}
