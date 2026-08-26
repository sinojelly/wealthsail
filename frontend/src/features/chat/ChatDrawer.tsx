import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Bot,
  Send,
  X,
  Loader2,
  MessageSquare,
} from "lucide-react";
import { getAccessToken } from "../../api/client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

// ---------------------------------------------------------------------------
// SSE stream helper
// ---------------------------------------------------------------------------
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function* streamChat(
  message: string,
  abortSignal: AbortSignal,
): AsyncGenerator<string> {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(`${API_BASE}/v1/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ message }),
    signal: abortSignal,
    credentials: "include",
  });

  if (!resp.ok) {
    throw new Error(`对话请求失败（HTTP ${resp.status}），请稍后重试`);
  }

  const reader = resp.body?.getReader();
  if (!reader) throw new Error("响应体不可读");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Parse SSE lines
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? ""; // keep incomplete last line in buffer

    for (const raw of lines) {
      const trimmed = raw.trim();
      if (!trimmed.startsWith("data: ")) continue;
      try {
        const parsed = JSON.parse(trimmed.slice(6));
        if (parsed.done) return;
        if (parsed.delta) yield parsed.delta;
      } catch {
        // skip malformed SSE lines
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function ChatDrawer() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [abortController, setAbortController] =
    useState<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  // Focus input when drawer opens
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        abortController?.abort();
        setOpen(false);
        toggleRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [abortController, open]);

  const sendText = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;

      const userMsg: ChatMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: trimmed,
        timestamp: new Date(),
      };
      const assistantId = `a-${Date.now()}`;
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);

      const controller = new AbortController();
      setAbortController(controller);

      let fullContent = "";

      try {
        for await (const delta of streamChat(trimmed, controller.signal)) {
          fullContent += delta;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: fullContent } : m,
            ),
          );
        }
      } catch (err: unknown) {
        const errorMsg =
          err instanceof Error && err.name !== "AbortError"
            ? err.message
            : null;
        if (errorMsg) {
          fullContent += errorMsg;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: fullContent } : m,
            ),
          );
        }
      } finally {
        setStreaming(false);
        setAbortController(null);
      }
    },
    [streaming],
  );

  const sendMessage = useCallback(() => {
    setInput("");
    void sendText(input);
  }, [input, sendText]);

  // 外部触发：市场热点点击行等 → 打开抽屉并直接提问
  useEffect(() => {
    function onAsk(event: Event) {
      const detail = (event as CustomEvent<{ message?: string }>).detail;
      if (!detail?.message) return;
      setOpen(true);
      void sendText(detail.message);
    }
    window.addEventListener("wealthsail:chat-ask", onAsk);
    return () => window.removeEventListener("wealthsail:chat-ask", onAsk);
  }, [sendText]);

  const stopStreaming = useCallback(() => {
    abortController?.abort();
  }, [abortController]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <>
      {/* Floating toggle button */}
      <button
        ref={toggleRef}
        className="chat-fab"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "关闭对话" : "打开 AI 对话"}
        title={open ? "关闭对话" : "AI 投研助手"}
      >
        {open ? <X size={22} /> : <MessageSquare size={22} />}
      </button>

      {/* Drawer panel */}
      {open && (
        <div
          className="chat-drawer"
          role="dialog"
          aria-modal="true"
          aria-label="WealthSail 投研助手"
        >
          {/* Header */}
          <div className="chat-header">
            <Bot size={20} />
            <span>WealthSail 投研助手</span>
            <button
              className="chat-close"
              onClick={() => setOpen(false)}
              aria-label="关闭"
            >
              <X size={18} />
            </button>
          </div>

          {/* Messages area */}
          <div className="chat-messages" aria-live="polite" aria-busy={streaming}>
            {messages.length === 0 && (
              <div className="chat-welcome">
                <Bot size={32} />
                <p>你好，我是 WealthSail 投研助手</p>
                <span>我可以帮你解读持仓、分析市场、回答投研问题</span>
              </div>
            )}

            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`chat-bubble ${msg.role === "user" ? "user" : "assistant"}`}
              >
                {msg.role === "user" ? (
                  <p>{msg.content}</p>
                ) : msg.content ? (
                  <div className="chat-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <div className="chat-typing">
                    <Loader2 size={16} className="spin" />
                    <span>思考中...</span>
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Input area */}
          <div className="chat-input-bar">
            <input
              ref={inputRef}
              className="chat-input"
              aria-label="向投研助手提问"
              placeholder="输入你的问题…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={streaming}
              maxLength={4000}
            />
            {streaming ? (
              <button
                className="chat-send chat-send--stop"
                onClick={stopStreaming}
                aria-label="停止生成"
                title="停止生成"
              >
                <X size={18} />
              </button>
            ) : (
              <button
                className="chat-send"
                onClick={sendMessage}
                disabled={!input.trim() || streaming}
                aria-label="发送"
              >
                <Send size={18} />
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
