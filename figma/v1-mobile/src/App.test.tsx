import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import App from "./App"

describe("WealthSail MVP shell", () => {
  it("shows the four-session workflow and all MVP asset categories", async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(
      screen.getByRole("heading", { name: "盘前简报" }),
    ).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /盘中跟踪/ })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /基金 14:30/ })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /盘后复盘/ })).toBeInTheDocument()

    expect(screen.getByText("贵州茅台")).toBeInTheDocument()
    expect(screen.getByText("沪深 300 ETF")).toBeInTheDocument()

    await user.click(screen.getByRole("tab", { name: /基金 14:30/ }))
    expect(
      screen.getByRole("heading", { name: "基金 14:30 报告" }),
    ).toBeInTheDocument()
    expect(screen.getAllByText("易方达蓝筹精选混合").length).toBeGreaterThan(0)
    expect(screen.getAllByText("估算").length).toBeGreaterThan(0)
  })

  it("switches users without leaking another tenant's report", async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText("林远航的核心账户")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "切换用户" }))
    await user.click(screen.getByRole("button", { name: /周可盈/ }))

    expect(screen.getByText("周可盈的稳健账户")).toBeInTheDocument()
    expect(screen.queryByText("林远航的核心账户")).not.toBeInTheDocument()
    expect(screen.getByText("企业微信已连接")).toBeInTheDocument()
  })

  it("records a post-market review as a confirmed decision", async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("tab", { name: /盘后复盘/ }))
    expect(screen.getByText("待确认决策")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "确认复盘结论" }))
    expect(screen.getByText("结论已确认并写入决策流水")).toBeInTheDocument()
  })
})
