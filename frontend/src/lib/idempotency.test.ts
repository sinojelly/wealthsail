import { afterEach, describe, expect, it, vi } from "vitest";
import { createIdempotencyKey } from "./idempotency";

const originalCrypto = globalThis.crypto;

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: originalCrypto,
  });
});

describe("createIdempotencyKey", () => {
  it("uses randomUUID when available", () => {
    const randomUUID = vi.fn(() => "uuid-from-browser");
    vi.stubGlobal("crypto", { randomUUID });

    expect(createIdempotencyKey()).toBe("uuid-from-browser");
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("uses getRandomValues when randomUUID is unavailable over HTTP", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (target: Uint8Array) => {
        target.set(Array.from({ length: 16 }, (_, index) => index));
        return target;
      },
    });

    expect(createIdempotencyKey()).toBe(
      "00010203-0405-4607-8809-0a0b0c0d0e0f",
    );
  });

  it("returns a legacy key when Web Crypto is absent", () => {
    vi.stubGlobal("crypto", undefined);
    vi.spyOn(Date, "now").mockReturnValue(123456789);
    vi.spyOn(Math, "random").mockReturnValue(0.123456789);

    expect(createIdempotencyKey()).toMatch(/^legacy-/);
  });
});
