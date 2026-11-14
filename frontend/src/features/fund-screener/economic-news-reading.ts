import type { EconomicCategory, EconomicSource } from "./economic-story-model";

type NewsSource = Pick<EconomicSource, "title" | "quote">;
const overseas =
  /美国|美联储|英国|欧洲|欧元区|日本|韩国|伊朗|俄乌|俄罗斯|乌克兰|OPEC|中东|纳斯达克|标普|道琼斯|英伟达|美光|海外/i;
const domestic =
  /中国|我国|国内|A股|沪深|央行|人民银行|国务院|财政部|国家统计局|上海|深圳|北京|国联民生|康辰|航天宏图|星网宇达|智微智能/;
const editorialTitle = /实施线索|分别研究|仍须并列|仍需商业闭环|传导分别验证/;
const categorySubjects: ReadonlyArray<[RegExp, EconomicCategory]> = [
  [/央行|人民银行|降息|加息|降准|货币政策|互换便利|公开市场操作/, "货币与政策"],
  [/火箭|卫星|星链|航天|Starship/i, "科技"],
  [/投资者大会|上交所|深交所|证监会|稳市|ETF|券商|银行|分红/i, "金融市场"],
];

const questionTopics = [
  /航运|海运|船舶|商船|通航|航道|海峡|停航|运输|战争险/,
  /油气|原油|石油|油价|产油|减产|产量|能源供应/,
  /黄金|金价|贵金属/,
  /加息|降息|美联储|实际利率|政策利率/,
  /卫星|火箭|星链|Starship|航天/i,
];
const otherSubjects = /英国|俄罗斯|乌克兰|俄乌|对俄|法国|加沙|欧盟/g;

/** A combined news digest is not evidence that all its subitems belong to this event. */
export function economicEventQuestions(title: string, rows: readonly string[]) {
  const withoutGeneric = (value: string) =>
    value.replace(
      /报道|风险|尚未|待确认|仍需|有待|是否|影响|最新|目前|变化|调查|历史|线索|研究|市场/g,
      " ",
    );
  const anchors =
    withoutGeneric(title).match(/[\p{Script=Han}]{3,}|[a-z]{3,}\d*/giu) ?? [];
  const relevant = (clause: string) => {
    if (
      (clause.match(otherSubjects) ?? []).some(
        (subject) => !title.includes(subject),
      )
    )
      return false;
    const text = withoutGeneric(clause).toLowerCase();
    return (
      questionTopics.some((axis) => axis.test(title) && axis.test(text)) ||
      anchors.some((word) => {
        if (/^[a-z]/i.test(word)) return text.includes(word.toLowerCase());
        return [...word].some(
          (_, index) =>
            index + 3 <= word.length &&
            text.includes(word.slice(index, index + 3)),
        );
      })
    );
  };
  return [
    ...new Set(
      rows
        .flatMap((row) =>
          typeof row === "string"
            ? row.split(/[；;。\n]+/).map((part) => part.trim())
            : [],
        )
        .filter((part) => part && relevant(part)),
    ),
  ].slice(0, 2);
}
function sharesSubject(title: string, source: string) {
  const subject = title.split(/[，,：:；;。]/)[0].replace(editorialTitle, "");
  const generic = new Set([
    "行业",
    "风险",
    "市场",
    "研究",
    "政策",
    "继续",
    "仍需",
    "分别",
  ]);
  const words = subject.match(/[\p{Script=Han}]{2,}|[A-Za-z]{2,}/gu) ?? [];
  return words.some((word) =>
    /^[A-Za-z]+$/.test(word)
      ? source.toLowerCase().includes(word.toLowerCase())
      : Array.from(word).some(
          (_, index, chars) =>
            index + 1 < chars.length &&
            !generic.has(chars[index] + chars[index + 1]) &&
            source.includes(chars[index] + chars[index + 1]),
        ),
  );
}

/** Reading projection only. Never rewrites saved facts, verification or event identity. */
export function economicNewsReading(
  title: string,
  summary: string,
  sources: readonly NewsSource[],
) {
  const cleanTitle = title
    .replace(
      /^\s*(?:【(?:待确认|待核实|未核实)】|(?:待确认|待核实|UNVERIFIED)\s*[|｜:：])\s*/i,
      "",
    )
    .replace(/[（(](?:待确认|待核实|未核实)[）)]\s*$/, "")
    .replace(/分别研究\s*$/, "")
    .trim();
  const source = editorialTitle.test(title)
    ? sources.find(
        (item) =>
          item.title.trim().length >= 12 &&
          !/财经日历|日程表|重点日历|原文|目录标题/.test(item.title) &&
          !editorialTitle.test(item.title) &&
          sharesSubject(cleanTitle, item.title),
      )
    : undefined;
  // Remove only recognizable editorial markers/clauses. A negation or an unconfirmed
  // factual allegation is meaningful news and must remain qualified in the summary.
  const cleaned = summary
    .replace(/(^|[。；;])\s*输入另报/g, "$1")
    .replace(/[，,]\s*属实施线索(?=[。；;]|$)/g, "")
    .split(/(?<=[。；;])/)
    .filter(
      (part) =>
        !/^\s*年度策略会属于机构观点[。；;]?\s*$/.test(part) &&
        !/^\s*[^\d。；;]*(?:传导分别验证|分别验证)[。；;]?\s*$/.test(part),
    )
    .join("")
    .replace(/[；;]\s*$/, "")
    .trim();
  return {
    title: source?.title.trim() || cleanTitle || title,
    summary: cleaned || summary,
  };
}

/** Subject-first classification: foreign context alone must not relabel domestic news. */
export function classifyEconomicNews(
  title: string,
  summary: string,
  sources: readonly NewsSource[],
  fallback: EconomicCategory,
): EconomicCategory {
  const domesticAt = title.search(domestic);
  const overseasAt = title.search(overseas);
  const subjectCategory = categorySubjects.find(([matcher]) =>
    matcher.test(title),
  )?.[1];
  if (fallback === "综合要闻" && subjectCategory) return subjectCategory;
  if (domesticAt >= 0 && (overseasAt < 0 || domesticAt < overseasAt))
    return fallback === "国际局势" ? "国内经济" : fallback;
  if (overseasAt >= 0) return "国际局势";
  if (fallback === "综合要闻") {
    const summaryCategory = categorySubjects.find(([matcher]) =>
      matcher.test(summary),
    )?.[1];
    if (summaryCategory) return summaryCategory;
  }
  if (domesticAt >= 0 || domestic.test(summary)) return fallback;
  if (
    overseas.test(summary) ||
    sources.some((source) => overseas.test(source.title))
  )
    return "国际局势";
  return fallback;
}

export function isEmptyCalendarStory(
  phase: string | null | undefined,
  title: string,
  summary: string,
): boolean {
  if (phase !== "PLANNED") return false;
  const normalized = (value: string) =>
    value.replace(/[\s，,。；;：:·｜|]/g, "");
  const body = normalized(summary);
  const empty =
    !body ||
    body === normalized(title) ||
    /^(?:原研究未提供事实摘要|暂无摘要|待公布|待发布)$/.test(body);
  // A concrete planned policy/action is news even when its headline is its only summary.
  const substantivePlan =
    /降准|降息|加息|上调|下调|拨款|发行|实施|执行|启动|暂停|停止|批准|宣布|通过|签署/.test(
      title,
    ) && /央行|政府|财政|国务院|美联储|银行|监管|公司|委员会/.test(title);
  const calendarHeading =
    /GDP|CPI|PPI|PMI|初请|失业金|成屋销售|EIA|原油库存|财经日历|日程表|待公布事项|(?:数据|利率决议)(?:公布|发布)?$/i.test(
      title,
    );
  return empty && calendarHeading && !substantivePlan;
}
