/* Isolated loopback API acceptance. Fixture data must never seed a real account. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.INVESTMENT_SMOKE_BASE_URL;
if (!base || !['127.0.0.1','localhost'].includes(new URL(base).hostname))
  throw new Error('Set INVESTMENT_SMOKE_BASE_URL to an isolated loopback test app');
const artifacts = path.resolve(__dirname,'../../artifacts/investment-evidence');
fs.mkdirSync(artifacts,{recursive:true});
(async()=>{
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1600,height:1050}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',response=>{
    if (response.url().includes('/api/') && response.status()>=400 &&
      !response.url().includes('/auth/refresh'))
      errors.push(`${response.status()} ${new URL(response.url()).pathname}`);
  });
  await page.route('https://fonts.googleapis.com/**',r=>r.fulfill({status:200,contentType:'text/css',body:''}));
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('邮箱').fill(process.env.INVESTMENT_SMOKE_EMAIL||'ci-smoke@wealthsail.example');
    await page.getByLabel('登录密码').fill(process.env.INVESTMENT_SMOKE_PASSWORD||'SmokeLogin-Only-2026!');
    await page.getByRole('button',{name:'登录工作台'}).click();
    await page.getByRole('heading',{name:'资产看板',exact:true}).waitFor();
    await page.getByRole('link',{name:'投资机会',exact:true}).click();
    await page.getByRole('heading',{name:'投资机会中心',exact:true}).waitFor();
    await page.getByRole('button',{name:/市场环境概览/}).click();
    const macro = page.waitForResponse(r=>r.url().includes('market-context?months=12'));
    await page.getByLabel('宏观观察窗口').selectOption('12');
    assert.equal((await macro).status(),200);
    await page.getByRole('img',{name:'制造业 PMI 历史走势'}).waitFor();
    assert.match(await page.locator('body').innerText(),/公布时间未核实/);
    await page.getByRole('button',{name:/验收基金（测试数据）/}).click();
    const diagnosis=page.getByLabel('研究诊断');
    await diagnosis.getByText(/满足研究门槛/).waitFor();
    const text=await diagnosis.innerText();
    assert.match(text,/12\.00%/);
    assert.match(text,/20\.00%/);
    assert.match(text,/组合适配尚未评估/);
    await page.getByRole('button',{name:'高级筛选'}).click();
    const [response]=await Promise.all([
      page.waitForResponse(r=>r.url().includes('valueCandidateOnly=true')),
      page.getByRole('combobox',{name:'研究资格',exact:true}).selectOption('true'),
    ]);
    assert.equal(response.status(),200);
    assert.equal((await response.json()).data.total,1);
    await diagnosis.getByRole('tab',{name:'技术趋势'}).click();
    await diagnosis.getByLabel('历史走势窗口').selectOption('all');
    await diagnosis.getByText(/展示 1000 个实际观察值/).waitFor();
    assert.match(await diagnosis.innerText(),/供应商分红再投收益指数/);
    await diagnosis.getByRole('tab',{name:'产品质量'}).click();
    assert.match(await diagnosis.innerText(),/0\.50%/);
    await page.screenshot({path:path.join(artifacts,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await diagnosis.getByRole('tab',{name:'综合判断'}).click();
    await page.screenshot({path:path.join(artifacts,'mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.reload();
    await page.getByRole('button',{name:/市场环境概览/}).click();
    await page.getByRole('button',{name:'高级筛选'}).click();
    await page.getByLabel('宏观观察窗口').waitFor();
    assert.equal(await page.getByLabel('宏观观察窗口').inputValue(),'12');
    assert.equal(await page.getByRole('combobox',{name:'研究资格',exact:true}).inputValue(),'true');
    assert.deepEqual(errors,[]);
    console.log('Investment evidence desktop/mobile real API acceptance passed');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
