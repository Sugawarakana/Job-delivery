import asyncio
import json
import os
import fitz
import anthropic
from playwright.async_api import async_playwright

COOKIES_FILE = "linkedin_cookies.json"
RESUME_FILE = "resume.pdf"

SEEN_JOBS_FILE = "seen_jobs.json"

def load_seen_jobs() -> set:
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen_jobs(seen: set):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(list(seen), f)




# ─────────────────────────────────────────
# 简历解析
# ─────────────────────────────────────────

def extract_resume_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    return "".join(page.get_text() for page in doc).strip()

# ─────────────────────────────────────────
# Claude 匹配分析
# ─────────────────────────────────────────

def analyze_match(resume_text: str, job: dict) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    prompt = f"""你是一个求职筛选助手，帮助一个需要 H-1B sponsorship 的候选人决定是否投递职位。

## 硬性淘汰（满足任意一条，score 直接返回 0）
1. 职位描述中出现 security clearance 相关字样（无论是"持有"还是"有资格获得"）
2. 要求绿卡、美国公民身份，或"eligible for clearance"（隐含公民要求）
3. 明确不提供 visa / H-1B sponsorship
4. 要求经验年限 ≥ 候选人实际经验的 2.5 倍

⚠️ 注意：H-1B 持有人通常无法获得 security clearance，因此任何涉及 clearance 的要求均视为淘汰。

## 候选人简历
{resume_text}

## 职位信息
职位名称：{job.get('title', 'N/A')}
公司：{job.get('company', 'N/A')}
地点：{job.get('location', 'N/A')}
职位描述：{job.get('description', 'N/A')}

请用JSON格式返回，不要加任何其他内容：
{{
  "score": <0-100，硬性淘汰时为0>,
  "apply": <true 或 false，score为0时为false>,
  "matched_skills": [<匹配的技能>],
  "missing_skills": [<缺少的关键技能>],
  "summary": "<2句话总结，硬性淘汰时说明原因>"
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

# ─────────────────────────────────────────
# 浏览器（连接已打开的 Chrome）
# ─────────────────────────────────────────

async def create_browser(p):
    browser = await p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)
    return browser, context

# ─────────────────────────────────────────
# 登录检查
# ─────────────────────────────────────────

async def ensure_logged_in(page):
    await page.goto("https://www.linkedin.com/feed", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    if "feed" not in page.url:
        print("未登录，请在浏览器中手动登录 LinkedIn...")
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await page.wait_for_url("**/feed**", timeout=120000)
        print("✅ 登录成功")
    else:
        print("✅ 已登录")

# ─────────────────────────────────────────
# 搜索职位
# ─────────────────────────────────────────
async def search_jobs(page, keywords: str, location: str = "", limit: int = 30, start_page: int = 1):
    all_jobs = []
    start = (start_page - 1) * 25  # 换算成 LinkedIn 的 start 参数

    while len(all_jobs) < limit:
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={keywords}"
            f"&location={location}"
            f"&f_TPR=r86400"
            f"&f_E=2"
            f"&f_JT=F"
            f"&start={start}"
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # 滚动左侧列表加载所有职位
        await page.mouse.move(530, 400)
        for _ in range(8):
            await page.mouse.wheel(0, 600)
            await page.wait_for_timeout(1000)

        try:
            await page.wait_for_selector(
                'li[data-occludable-job-id], li.jobs-search-results__list-item, .job-card-container',
                timeout=15000
            )
        except Exception:
            await page.screenshot(path=f"debug_page_{start}.png")
            print(f"⚠️  第 {start//25 + 1} 页未找到职位，停止翻页")
            break

        jobs = await page.evaluate("""() => {
            const links = [...document.querySelectorAll('a[href*="/jobs/view/"]')];
            const seen = new Set();
            const results = [];

            for (const link of links) {
                const href = link.href.split('?')[0];
                if (seen.has(href)) continue;
                seen.add(href);

                const card = link.closest('li') || link.parentElement;
                const companyEl = card ? card.querySelector(
                    '.artdeco-entity-lockup__subtitle span, ' +
                    '.job-card-container__primary-description'
                ) : null;
                const locationEl = card ? card.querySelector(
                    '.artdeco-entity-lockup__caption span, ' +
                    '.job-card-container__metadata-item'
                ) : null;

                results.push({
                    title: link.innerText.trim(),
                    company: companyEl ? companyEl.innerText.trim() : 'N/A',
                    location: locationEl ? locationEl.innerText.trim() : 'N/A',
                    url: link.href,
                });
            }
            return results.filter(j => j.title);
        }""")

        if not jobs:
            print(f"⚠️  第 {start//25 + 1} 页返回0个结果，停止翻页")
            break

        print(f"  第 {start//25 + 1} 页找到 {len(jobs)} 个职位")
        all_jobs.extend(jobs)

        if len(jobs) < 25:
            print("已到最后一页")
            break

        start += 25

    # 去重
    seen = set()
    unique_jobs = []
    for job in all_jobs:
        if job["url"] not in seen:
            seen.add(job["url"])
            unique_jobs.append(job)

    return unique_jobs[:limit]
# ─────────────────────────────────────────
# 获取职位详情
# ─────────────────────────────────────────

async def get_job_description(page, job_url: str) -> str:
    await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    # 找 "About the job" 标题，点击其兄弟节点内的 "… more" 按钮
    await page.evaluate("""() => {
        for (const h of document.querySelectorAll('h1,h2,h3')) {
            if (!(h.innerText || '').toLowerCase().includes('about the job')) continue;
            const sibling = h.parentElement && h.parentElement.nextElementSibling;
            if (!sibling) continue;
            for (const btn of sibling.querySelectorAll('button, a')) {
                const t = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                if (t.includes('more') && t.length < 15) {
                    btn.click();
                    return;
                }
            }
        }
    }""")
    await page.wait_for_timeout(800)

    desc = await page.evaluate("""() => {
        // 找 "About the job" 标题的下一个兄弟节点（存放实际 JD 内容）
        for (const h of document.querySelectorAll('h1,h2,h3')) {
            if (!(h.innerText || '').toLowerCase().includes('about the job')) continue;
            const sibling = h.parentElement && h.parentElement.nextElementSibling;
            if (sibling && sibling.innerText.trim().length > 50) {
                return sibling.innerText.trim();
            }
        }
        return '';
    }""")

    return desc[:3000] if desc else ''


# ─────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────

async def main():
    # 简历检查
    if not os.path.exists(RESUME_FILE):
        print(f"❌ 找不到简历：{RESUME_FILE}")
        return
    resume_text = extract_resume_text(RESUME_FILE)
    print(f"✅ 简历已加载（{len(resume_text)} 字符）")

    # ↓↓↓ 修改搜索参数 ↓↓↓
    KEYWORDS = "Embedded Software Engineer"
    LOCATION = "United States"
    LIMIT = 100
    START_PAGE = 1
    # ↑↑↑ 修改搜索参数 ↑↑↑

    async with async_playwright() as p:
        browser, context = await create_browser(p)
        page = await context.new_page()

        await ensure_logged_in(page)

        print(f"\n🔍 搜索「{KEYWORDS}」...")
        jobs = await search_jobs(page, KEYWORDS, LOCATION, LIMIT, START_PAGE)
        print(f"找到 {len(jobs)} 个职位\n")

        if not jobs:
            print("未找到职位，请检查网络或关键词")
            await browser.close()
            return

        seen_jobs = load_seen_jobs()
        results = []

        for i, job in enumerate(jobs):
            # 跳过已处理过的职位
            if job["url"] in seen_jobs:
                # print(f"[{i+1}/{len(jobs)}] 已跳过（看过）：{job['title']} @ {job['company']}")
                continue

            # print(f"[{i+1}/{len(jobs)}] {job['title']} @ {job['company']}")
            job["description"] = await get_job_description(page, job["url"])

            try:
                match = analyze_match(resume_text, job)
                job["match"] = match
                print(f"    ⭐ {match.get('score')}/100 - {match.get('summary', '')}")
            except Exception as e:
                job["match"] = {"score": 0, "error": str(e)}
                print(f"    ⚠️  分析失败：{e}")

            seen_jobs.add(job["url"])
            results.append(job)

        save_seen_jobs(seen_jobs)

        await browser.close()

    # 按匹配度排序输出
    results.sort(key=lambda x: x.get("match", {}).get("score", 0), reverse=True)

    print("\n" + "="*60)
    print("📊 匹配度排名")
    print("="*60)
    for i, job in enumerate(results):
        m = job.get("match", {})
        print(f"\n#{i+1}  {job['title']} @ {job['company']}")
        print(f"     📍 {job['location']}")
        print(f"     ⭐ {m.get('score', 'N/A')}/100")
        print(f"     ✅ 匹配：{', '.join(m.get('matched_skills', []))}")
        print(f"     ❌ 缺少：{', '.join(m.get('missing_skills', []))}")
        print(f"     💬 {m.get('summary', '')}")
        print(f"     🔗 {job['url']}")

    # 读取已有结果
    existing_results = []
    if os.path.exists("job_matches.json"):
        with open("job_matches.json", encoding="utf-8") as f:
            existing_results = json.load(f)

    # 合并新结果（新的放前面）
    all_results = results + existing_results

    # 按匹配度排序
    all_results.sort(key=lambda x: x.get("match", {}).get("score", 0), reverse=True)

    with open("job_matches.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存，共 {len(all_results)} 个职位（含历史）")

async def debug_job(url: str):
    resume_text = extract_resume_text(RESUME_FILE)
    async with async_playwright() as p:
        browser, context = await create_browser(p)
        page = await context.new_page()
        desc = await get_job_description(page, url)
        print(f"\n描述长度: {len(desc)} 字符")
        print("─" * 60)
        print(desc[:1000])
        print("─" * 60)
        if desc:
            match = analyze_match(resume_text, {"title": "Debug Job", "company": "N/A", "location": "N/A", "description": desc})
            print(f"\n⭐ {match.get('score')}/100 - {match.get('summary')}")
        await browser.close()

# ── 调试单个职位时用下面这行，正常运行时换回 main() ──
# asyncio.run(debug_job("https://www.linkedin.com/jobs/view/4398041754/?alternateChannel=search&eBP=CwEAAAGdsTjqMmXX5h5dQghKa3vnIrRY1CPUcOZ6j-th8QjdHQkiiBKTbpf62RLls6oU4c8koKPZzQQUcNUkUjCUhblpsxHo0YMlCC5Lj4yIC1ibPz93idU-6EnblQFeouN7kxs8YgAJw1xV9rkopHG4alXYW_wmKeOfamvpCfrzXFOtRoWFutoxP_CBN5wt-3nzcwzPbfgQn7ePSMo2RbQeCxWK3uBYWh5GTTNN6phAGKFsIp-pqq6CJqDk4GIVYWHhTkmEjLjHZSEvvEdTSozDvvCi3YjH90zhzoLlIN74H3XzDcbFrTJ679nlL3C7xfDjCzeFF3VevCNXQog2xwDm6Hv_YSRdITRiqVX7gUmSiu4UUbj_TZoRnKvIZ4lsdImrstc7TpppToPWlGBdZwqV8NKQuaNJQ4QwLGvNvENsKVPxWk7j7Hp00eTRYDqdfwz7HponS2x40om3LT6zaZNboXblhFZ4ANDZkrAX91kYwJSV5Q&refId=busB%2F%2FsoUCjA9OeR2Q%2Bv4Q%3D%3D&trackingId=KkBMI8AlKoQp9Ce08cIC1g%3D%3D"))
asyncio.run(main())