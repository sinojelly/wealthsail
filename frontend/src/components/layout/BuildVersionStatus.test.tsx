import { act, fireEvent, render, screen } from "@testing-library/react";
import BuildVersionStatus from "./BuildVersionStatus";

const nextCommit = "1".repeat(40);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("notifies a restored tab about the deployed commit and reloads only on request", async () => {
  const reload = vi.fn();
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json(__WEALTHSAIL_BUILD__))
    .mockResolvedValueOnce(
      Response.json({ commit: nextCommit, describe: "release-2" }),
    );
  vi.stubGlobal("fetch", fetch);
  render(<BuildVersionStatus monitor reload={reload} />);
  await act(async () => {});
  expect(screen.queryByRole("button", { name: "刷新到最新版本" })).toBeNull();
  await act(async () => {
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: true }),
    );
  });
  expect(screen.getByText("发现新版本 release-2")).toBeVisible();
  expect(screen.getByLabelText(/网页版本/)).toHaveTextContent(
    __WEALTHSAIL_BUILD__.describe,
  );
  expect(reload).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledWith(
    "/build-info.json",
    expect.objectContaining({ cache: "no-store" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "刷新到最新版本" }));
  expect(reload).toHaveBeenCalledOnce();
});

it("keeps the loaded build through transient errors and deduplicates a focus burst", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("<html></html>"));
  vi.stubGlobal("fetch", fetch);
  render(<BuildVersionStatus monitor />);
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
  expect(fetch).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "刷新到最新版本" })).toBeNull();
  expect(screen.getByLabelText(/网页版本/)).toBeVisible();
});
