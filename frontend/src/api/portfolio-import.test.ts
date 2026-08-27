import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpError } from "./client";
import { confirmTiantianImport, previewTiantianImport } from "./portfolio";

const meta = {
  requestId: "req-1",
  asOf: "2026-08-28T12:00:00+08:00",
  qualityStatus: "FRESH",
  schemaVersion: 1,
};

afterEach(() => vi.unstubAllGlobals());

describe("Tiantian import API", () => {
  it("sends FormData without overriding its multipart boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: { batchId: "batch-1", status: "DRAFT", version: 1 },
          meta,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    });

    await previewTiantianImport(
      file,
      { accountName: "天天基金账户" },
      "preview-1",
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.get("Idempotency-Key")).toBe("preview-1");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
    expect((init.body as FormData).get("accountName")).toBe("天天基金账户");
  });

  it("sends versioned confirmation and preserves actionable field errors", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: {
            code: "VERSION_CONFLICT",
            message: "预览版本已经变化",
            retryable: false,
            fieldErrors: [
              { field: "decisions.0.startedOn", message: "日期已失效" },
            ],
          },
          requestId: "req-stale",
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      confirmTiantianImport(
        "batch-1",
        3,
        {
          decisions: [{ itemId: "item-1", startedOn: "2024-01-02" }],
          confirmEmpty: false,
        },
        "confirm-1",
      ),
    ).rejects.toMatchObject({
      status: 409,
      code: "VERSION_CONFLICT",
      requestId: "req-stale",
      fieldErrors: [{ field: "decisions.0.startedOn", message: "日期已失效" }],
    } satisfies Partial<HttpError>);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("If-Match")).toBe('"3"');
    expect(headers.get("Idempotency-Key")).toBe("confirm-1");
  });
});
