"""
悟道扩展工具集：浏览器自动化 + RAG 知识检索 + 高级 Agent 模式
"""
import os
import json
import time
import asyncio
import urllib.parse
from pathlib import Path
from typing import Optional

from core.config import WUDAO_DATA

import requests
from bs4 import BeautifulSoup

# 浏览器自动化（playwright 可选升级）
_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

# 默认请求头
_WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# RAG 知识检索
_CHROMA_AVAILABLE = False
try:
    import chromadb
    from chromadb.utils import embedding_functions
    _CHROMA_AVAILABLE = True
except ImportError:
    pass


# ================================================================
# 工具1：浏览器自动化
# ================================================================

BROWSER_HELP = """用法：
- "打开百度搜Python教程" → browser_do(url="https://www.baidu.com", action="search", query="Python教程")
- "帮我打开这个网页" → browser_do(url="https://...", action="open")
- "截图给我看" → browser_do(url="...", action="screenshot")
- "在搜索框输入xxx" → browser_do(url="...", action="search", query="xxx")
- "点击登录按钮" → browser_do(url="...", action="click", selector="#login-btn")
"""


def _fetch_page(url: str, timeout: int = 15) -> dict:
    """用 requests + BeautifulSoup 获取网页内容（基础版浏览器）"""
    try:
        resp = requests.get(url, headers=_WEB_HEADERS, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title else ""
        # 移除 script/style
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        # 优先取 main/article/content
        main = soup.find("main") or soup.find("article") or soup.find("body")
        text = main.get_text(separator="\n", strip=True)[:3000] if main else ""
        return {"success": True, "title": title, "content": text, "status_code": resp.status_code}
    except Exception as e:
        return {"error": f"获取失败: {str(e)[:150]}"}


async def run_browser(action: str = "open",
                      url: str = "",
                      query: str = "",
                      selector: str = "",
                      timeout: int = 30000) -> dict:
    """
    浏览器自动化工具
    action: open | search | click | screenshot
    url: 要打开的网页地址
    query: 搜索关键词（action=search时使用）
    selector: CSS 选择器（action=click时使用）

    基础方式用 requests+BeautifulSoup（无需浏览器），
    高级操作（click/screenshot）需要 playwright。
    """
    if not url and action != "close":
        return {"error": "请提供 URL 地址"}

    result = {"action": action, "url": url, "content": "", "screenshot": None, "title": "", "success": False}

    # --- open：网页获取（基础方式，无需浏览器） ---
    if action in ("open",):
        fetch_result = _fetch_page(url, timeout=timeout // 1000)
        if "error" in fetch_result:
            return fetch_result
        result["title"] = fetch_result["title"]
        result["content"] = fetch_result["content"]
        result["success"] = True
        return result

    # --- search：搜索引擎搜索（基础方式） ---
    if action == "search":
        if not query:
            return {"error": "请提供搜索关键词 (query)"}
        # 默认百度搜索
        search_url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}"
        fetch_result = _fetch_page(search_url, timeout=timeout // 1000)
        if "error" in fetch_result:
            return fetch_result
        result["title"] = f"搜索: {query}"
        result["content"] = fetch_result.get("content", "")[:3000]
        result["success"] = True
        return result

    # --- click / screenshot：需要 playwright ---
    if not _PLAYWRIGHT_AVAILABLE:
        return {"error": f"操作 '{action}' 需要安装浏览器引擎，请执行: pip install playwright && python -m playwright install chromium"}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 800})

            if action == "click" and url:
                await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                if selector:
                    el = await page.query_selector(selector)
                    if el:
                        await el.click()
                        await page.wait_for_timeout(1500)
                        result["title"] = await page.title()
                        text = await page.evaluate("() => document.body.innerText.slice(0, 2000)")
                        result["content"] = text
                        result["success"] = True
                    else:
                        result["content"] = f"未找到元素: {selector}"
                else:
                    result["content"] = "未指定 CSS 选择器"

            elif action == "screenshot":
                if url:
                    await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)
                import base64
                screenshot_bytes = await page.screenshot(full_page=True)
                result["screenshot"] = base64.b64encode(screenshot_bytes).decode()
                result["title"] = await page.title()
                result["success"] = True

            await browser.close()
    except Exception as e:
        return {"error": f"浏览器操作失败: {str(e)[:200]}"}

    return result


# ================================================================
# 工具2：RAG 知识检索
# ================================================================

# 智脑知识库路径（降级：旧 MD 文件目录）
ZHI_NAO_DIR = Path("D:/openclaw-team/龙北小学")

# 四库 JSON 路径（优先）
_FOUR_LIB_DIR = Path("D:/projects/悟道/data")
_FOUR_LIB_FILES = [
    ("knowledge_base.json", "知识库", "knowledge_base"),
    ("strategy_base.json", "策略库", "strategy_base"),
    ("pattern_base.json", "模式库", "pattern_base"),
    ("prompt_template_base.json", "模板库", "prompt_template_base"),
]

_NOISE_TAGS = {"轻微噪声", "噪声", "含噪声"}  # 导入时跳过

_KNOWLEDGE_COLLECTION = None


def _get_knowledge_collection():
    """获取 ChromaDB 知识库集合（懒加载）"""
    global _KNOWLEDGE_COLLECTION
    if _KNOWLEDGE_COLLECTION is not None:
        return _KNOWLEDGE_COLLECTION
    if not _CHROMA_AVAILABLE:
        return None

    data_dir = WUDAO_DATA
    db_path = os.path.join(data_dir, "chroma_knowledge")
    try:
        client = chromadb.PersistentClient(path=db_path)
        try:
            collection = client.get_collection("zhinao_knowledge")
        except Exception:
            collection = client.create_collection("zhinao_knowledge")

        # 如果集合为空，导入数据
        if collection.count() == 0:
            # 优先：四库 JSON 存在则导入
            if _FOUR_LIB_DIR.exists() and (_FOUR_LIB_DIR / _FOUR_LIB_FILES[0][0]).exists():
                _import_4lib_json(collection)
            else:
                # 降级：旧 MD 文件导入
                _import_zhinao_files(collection)

        _KNOWLEDGE_COLLECTION = collection
        return collection
    except Exception as e:
        print(f"[tools_ext] ChromaDB 初始化失败: {e}")
        return None


def _has_noise_tag(tags):
    """检查标签列表是否包含噪声标签"""
    if not tags:
        return False
    if isinstance(tags, str):
        return tags in _NOISE_TAGS
    return bool(_NOISE_TAGS & set(tags))


def _import_4lib_json(collection):
    """将四库 JSON 数据导入 ChromaDB"""
    imported = 0
    skipped_noise = 0
    skipped_short = 0

    for fname, lib_name, prefix in _FOUR_LIB_FILES:
        fp = _FOUR_LIB_DIR / fname
        if not fp.exists():
            print(f"[tools_ext] 跳过（不存在）: {fname}")
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[tools_ext] 解析失败 {fname}: {e}")
            continue

        if not isinstance(data, list):
            print(f"[tools_ext] 跳过（非列表）: {fname}")
            continue

        batch_docs = []
        batch_metas = []
        batch_ids = []
        batch_size = 100  # Chroma 批量添加上限

        def _flush_batch():
            nonlocal batch_docs, batch_metas, batch_ids
            if not batch_docs:
                return
            collection.add(
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids,
            )
            batch_docs = []
            batch_metas = []
            batch_ids = []

        for idx, item in enumerate(data):
            title = item.get("title", "") or ""
            content = item.get("content", "") or ""
            tags = item.get("tags", [])
            weight = item.get("weight", 1) or 1
            source = item.get("source", "") or lib_name

            # 过滤：空/短内容
            content = content.strip()
            if not content or len(content) < 50:
                skipped_short += 1
                continue

            # 过滤：噪声标签
            if _has_noise_tag(tags):
                skipped_noise += 1
                continue

            # 内容截断（Chroma 限制）
            doc_text = content[:2000]

            batch_docs.append(doc_text)
            batch_metas.append({
                "source": f"{source}/{title[:60]}" if title else source,
                "title": title[:100],
                "weight": weight,
                "library": lib_name,
            })
            batch_ids.append(f"{prefix}_{idx}")

            imported += 1
            if len(batch_docs) >= batch_size:
                _flush_batch()

        _flush_batch()
        print(f"[tools_ext] {lib_name}: {fp.name} → 导入 {imported} 条（跳过噪声 {skipped_noise} 条、短内容 {skipped_short} 条）")

    print(f"[tools_ext] 四库导入完成，共 {imported} 条知识")


def _import_zhinao_files(collection):
    """将智脑技能指南导入 ChromaDB（降级方案）"""
    imported = 0
    md_files = list(ZHI_NAO_DIR.glob("*.md"))
    for fp in md_files:
        try:
            text = fp.read_text(encoding="utf-8")
            import re
            # 按 ## 标题分块
            chunks = re.split(r'(?=^## )', text, flags=re.MULTILINE)
            for i, chunk in enumerate(chunks):
                chunk = chunk.strip()
                if len(chunk) < 50:
                    continue
                # 取前两句做标题
                first_line = chunk.split("\n")[0][:100]
                collection.add(
                    documents=[chunk[:2000]],
                    metadatas=[{"source": fp.name, "title": first_line}],
                    ids=[f"{fp.stem}_{i}"],
                )
                imported += 1
        except Exception as e:
            print(f"[tools_ext] 导入失败 {fp.name}: {e}")

    # 也导入摘要文件
    summary_file = ZHI_NAO_DIR / "智脑四库摘要.md"
    if summary_file.exists():
        try:
            text = summary_file.read_text(encoding="utf-8")
            collection.add(
                documents=[text[:2000]],
                metadatas=[{"source": "智脑四库摘要", "title": "四库摘要"}],
                ids=["summary_main"],
            )
            imported += 1
        except Exception:
            pass

    print(f"[tools_ext] 降级导入 {imported} 条知识（来源: {ZHI_NAO_DIR}）")


def _fallback_search(query: str, top_k: int = 5) -> dict:
    """
    降级搜索：当 Chroma 语义搜索效果不佳时，做全库关键词模糊匹配
    """
    try:
        import sqlite3
        db_path = os.path.join(WUDAO_DATA, "chroma_knowledge", "chroma.sqlite3")
        if not os.path.exists(db_path):
            return {"results": [], "total": 0}

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 提取关键词（拆中文/英文词，去重，去停用词）
        import re
        stop_words = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这", "与", "及", "或", "但", "为", "从", "被", "把", "让", "对", "能", "可以", "应该", "需要", "必须", "如果", "因为", "所以", "而且", "然后", "但是", "虽然", "如何", "什么", "怎么", "怎样"}
        raw_kws = [kw.strip().lower() for kw in re.split(r'[,，、\s()（）【】「」:：;；]+', query) if len(kw.strip()) > 1]
        keywords = list(dict.fromkeys([kw for kw in raw_kws if kw not in stop_words]))

        if not keywords:
            conn.close()
            return {"results": [], "total": 0}

        # 用 SQL LIKE 模糊匹配 documents 表
        # Chroma 的 embedding_fulltext 表存了分词后的文本，但直接用 documents 表更可靠
        import urllib.parse
        like_patterns = [f"%{kw}%" for kw in keywords]
        like_clauses = " OR ".join([f"d.document LIKE ?" for _ in like_patterns])

        try:
            cursor.execute(f"""
                SELECT d.id, d.document, e.metadata
                FROM embedding_fulltext_search_content AS d
                JOIN embeddings AS e ON d.rowid = e.rowid
                WHERE ({like_clauses})
                LIMIT ?
            """, like_patterns + [top_k * 3])
        except Exception:
            # Chroma 表结构降级方案：直接从 embeddings 表读
            conn.close()
            return {"results": [], "total": 0, "fallback_note": "sqlite 表结构不兼容"}

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return {"results": [], "total": 0}

        items = []
        for row in rows:
            try:
                meta = json.loads(row[2]) if row[2] else {}
            except Exception:
                meta = {}
            items.append({
                "id": str(row[0]),
                "source": meta.get("source", ""),
                "title": meta.get("title", ""),
                "content": (row[1] or "")[:800],
            })

        # 相关度排序：匹配关键词越多越靠前
        def _score(item):
            text = (item["content"] + item["title"] + item["source"]).lower()
            return sum(1 for kw in keywords if kw in text)

        items.sort(key=_score, reverse=True)
        items = items[:top_k]

        src_count = {}
        for it in items:
            src = it["source"].split("/")[0] if "/" in it["source"] else it["source"]
            src_count[src] = src_count.get(src, 0) + 1
        src_desc = "、".join([f"{k}{v}条" for k, v in sorted(src_count.items(), key=lambda x: -x[1])])

        return {
            "results": items,
            "total": len(items),
            "summary": f"模糊匹配找到{len(items)}条，来自：{src_desc}",
            "mode": "keyword_fallback"
        }
    except Exception as e:
        return {"results": [], "total": 0, "fallback_note": str(e)[:100]}


# 简单 LRU 缓存：知识检索结果缓存 60 秒
_knowledge_cache: Dict[str, dict] = {}
_knowledge_cache_ttl: Dict[str, float] = {}
_KNOWLEDGE_CACHE_MAX = 32


def _get_cached(query: str) -> Optional[dict]:
    cached = _knowledge_cache.get(query)
    if cached and _knowledge_cache_ttl.get(query, 0) > time.time():
        return cached
    return None


def _set_cache(query: str, result: dict, ttl: float = 60.0):
    if len(_knowledge_cache) >= _KNOWLEDGE_CACHE_MAX:
        # 移除最旧的
        oldest = min(_knowledge_cache_ttl.keys(), key=lambda k: _knowledge_cache_ttl[k])
        _knowledge_cache.pop(oldest, None)
        _knowledge_cache_ttl.pop(oldest, None)
    _knowledge_cache[query] = result
    _knowledge_cache_ttl[query] = time.time() + ttl


def query_knowledge(query: str, top_k: int = 5, multi_query: bool = True) -> dict:
    """
    从智脑知识库检索相关内容（优化版）
    返回最相关的 top_k 条知识，自动提取关键词多角度搜索并去重
    如果 Chroma 语义检索结果不理想，自动降级到关键词模糊匹配
    """
    # 缓存命中直接返回
    cached = _get_cached(query)
    if cached:
        return cached
    if not _CHROMA_AVAILABLE:
        return {"error": "ChromaDB 未安装", "results": [], "summary": "ChromaDB 库未安装，无法搜索"}

    collection = _get_knowledge_collection()
    if collection is None:
        return {"error": "知识库未初始化", "results": [], "summary": "知识库未初始化"}

    if collection.count() == 0:
        return {"error": "知识库为空，请先导入数据", "results": [], "summary": "知识库为空"}

    try:
        # 多角度搜索：自动拆分关键词，提高命中率
        queries = [query]
        if multi_query:
            import re
            # 按中文顿号/逗号/空格拆分关键词，最多 2 个额外查询（降低延迟）
            keywords = [kw.strip() for kw in re.split(r'[,，、\\s]+', query) if len(kw.strip()) > 2]
            if len(keywords) > 1:
                queries = keywords[:3]  # 原查询 + 最多 2 个关键词查询

        seen_ids = set()
        all_items = []
        for q in queries:
            results = collection.query(
                query_texts=[q],
                n_results=min(top_k + 5, collection.count()),
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            ids = results.get("ids", [[]])[0]

            for i in range(len(docs)):
                item_id = ids[i] if i < len(ids) else ""
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                all_items.append({
                    "id": item_id,
                    "source": metas[i].get("source", "") if i < len(metas) else "",
                    "title": metas[i].get("title", "") if i < len(metas) else "",
                    "content": docs[i][:800] if i < len(docs) else "",
                })
                if len(all_items) >= top_k:
                    break
            if len(all_items) >= top_k:
                break

        # 评分：语义搜索的匹配度（用 Choma 的 distance 近似）
        def _relevance_score(item):
            """检查结果与查询关键词的文本匹配度"""
            q_lower = query.lower()
            text = (item["content"] + item["title"] + item["source"]).lower()
            # 计算用户查询中有多少关键词出现在结果中
            kw = [w.strip() for w in re.split(r'[,，、\\s]+', query) if len(w.strip()) > 1]
            hits = sum(1 for w in kw if w.lower() in text)
            return hits

        # 如果语义搜索结果看起来不太相关（平均命中关键词数 < 0.5），启用降级搜索
        if all_items:
            avg_hits = _relevance_score(all_items[0])
            if avg_hits < 0.5:
                fallback = _fallback_search(query, top_k)
                fb_results = fallback.get("results", [])
                if fb_results:
                    # 合并：降级结果在前面，语义结果补在后面
                    fb_ids = {it["id"] for it in fb_results}
                    merged = fb_results[:]
                    for it in all_items:
                        if it["id"] not in fb_ids and len(merged) < top_k:
                            merged.append(it)
                            fb_ids.add(it["id"])
                    all_items = merged

        # 生成摘要
        sources_count = {}
        for item in all_items:
            src = item["source"].split("/")[0] if "/" in item["source"] else item["source"]
            sources_count[src] = sources_count.get(src, 0) + 1

        source_desc = "、".join([f"{k}{v}条" for k, v in sorted(sources_count.items(), key=lambda x: -x[1])])
        summary = f"找到{len(all_items)}条相关知识，来自：{source_desc}"

        result = {"results": all_items, "total": len(all_items), "summary": summary}
        _set_cache(query, result)
        return result
    except Exception as e:
        err = str(e)[:200]
        return {"error": f"查询失败: {err}", "results": [], "summary": f"知识检索失败: {err}"}


# ================================================================
# 工具3：高级 Agent 工作流
# ================================================================

def build_workflow(steps: list, goal: str = "") -> dict:
    """
    构建多步骤 Agent 工作流
    steps: [{"name": "步骤名", "tool": "工具名", "params": {...}}, ...]
    goal: 工作流目标描述
    """
    workflow = {
        "goal": goal,
        "steps": steps,
        "total_steps": len(steps),
        "status": "ready",
    }
    return workflow


def suggest_agent_role(task: str) -> dict:
    """
    根据任务描述推荐合适的 Agent 角色组合
    """
    import re

    suggestions = {
        "前端": ["agent_designer", "agent_engineer"],
        "后端": ["agent_engineer", "agent_marketing"],
        "设计": ["agent_designer", "agent_marketing"],
        "算法": ["agent_engineer", "agent_risk"],
        "安全": ["agent_risk", "agent_engineer"],
        "数据": ["agent_marketing", "agent_engineer"],
        "产品": ["agent_marketing", "agent_designer"],
    }

    matched = []
    for keyword, agents in suggestions.items():
        if keyword in task:
            matched.extend(agents)

    if not matched:
        matched = ["agent_engineer", "agent_designer", "agent_marketing"]

    # 去重
    seen = set()
    unique = []
    for a in matched:
        if a not in seen:
            seen.add(a)
            unique.append(a)

    return {"task": task, "suggested_agents": unique}


# ================================================================
# 工具4：提示词模板导入
# ================================================================

TEMPLATE_LIBRARY = {
    "algorithm_implementation": {
        "category": "编程",
        "template": "请用 {language} 实现 {algorithm}。\n要求：\n1. 输入: {input_desc}\n2. 输出: {output_desc}\n3. 处理边界情况: {edge_cases}\n4. 时间/空间复杂度: {complexity}\n5. 添加测试用例覆盖正常、边界、异常场景",
        "params": ["language", "algorithm", "input_desc", "output_desc", "edge_cases", "complexity"],
    },
    "code_review": {
        "category": "编程",
        "template": "请审查以下 {language} 代码，关注：\n1. 逻辑正确性\n2. 边界情况处理\n3. 性能问题\n4. 代码风格\n5. 安全隐患\n\n代码：\n```{language}\n{code}\n```",
        "params": ["language", "code"],
    },
    "debug_analyze": {
        "category": "编程",
        "template": "以下 {language} 代码出现了 {error_desc} 错误。\n1. 分析可能的原因\n2. 给出修复方案\n3. 解释为什么这个修复有效\n\n代码：\n```{language}\n{code}\n```\n错误信息：{error_msg}",
        "params": ["language", "error_desc", "code", "error_msg"],
    },
    "api_design": {
        "category": "开发",
        "template": "设计一个 {method} {path} API 接口。\n功能描述：{description}\n请求参数：{params}\n返回格式：{response_format}\n请给出完整的请求/响应示例和错误处理。",
        "params": ["method", "path", "description", "params", "response_format"],
    },
    "rag_query": {
        "category": "AI",
        "template": "基于以下知识库内容回答问题。\n\n知识库：\n{context}\n\n问题：{query}\n\n要求：\n1. 只基于提供的知识库内容回答\n2. 如果知识库中没有相关信息，直接说不知道\n3. 引用具体来源",
        "params": ["context", "query"],
    },
    "browser_task": {
        "category": "自动化",
        "template": "请帮我用浏览器完成以下任务：\n任务描述：{task}\n目标网址：{url}\n需要获取的信息：{info_needed}\n\n请分步骤执行并在每一步告诉我结果。",
        "params": ["task", "url", "info_needed"],
    },
    "multi_agent_plan": {
        "category": "AI",
        "template": "我需要召集一个多 Agent 讨论会来解决以下问题：\n\n议题：{topic}\n\n建议参与的角色：{agents}\n讨论轮数：{rounds}\n\n请给出讨论框架和每个角色需要关注的重点。",
        "params": ["topic", "agents", "rounds"],
    },
}


def list_templates(category: str = "") -> dict:
    """列出可用模板"""
    if category:
        items = {k: v for k, v in TEMPLATE_LIBRARY.items() if v["category"] == category}
    else:
        items = TEMPLATE_LIBRARY
    result = {}
    for k, v in items.items():
        result[k] = {"category": v["category"], "params": v["params"]}
    return {"templates": result, "total": len(result)}


def apply_template(name: str, params: dict) -> dict:
    """应用模板填充参数"""
    if name not in TEMPLATE_LIBRARY:
        return {"error": f"模板不存在: {name}"}
    tpl = TEMPLATE_LIBRARY[name]
    try:
        result = tpl["template"]
        for k, v in params.items():
            result = result.replace("{" + k + "}", str(v))
        return {"result": result, "template_name": name}
    except Exception as e:
        return {"error": f"模板填充失败: {str(e)}"}
