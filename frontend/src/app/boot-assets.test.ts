import html from "../../index.html?raw";
import css from "../index.css?raw";

it("offers a useful status before the entry JavaScript has downloaded or executed", () => {
  const initialDocument = new DOMParser().parseFromString(html, "text/html");
  expect(
    initialDocument.querySelector('#root [role="status"]')?.textContent,
  ).toContain("正在加载投研工作台");
});

it("does not block the initial stylesheet on an external font service", () => {
  expect(/@import\s+(?:url\()?\s*["']?https?:/.test(css)).toBe(false);
});
