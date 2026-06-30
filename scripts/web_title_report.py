#!/usr/bin/env python3
"""
网页标题抓取与词频统计工具

功能：
  1. 从指定 URL 抓取网页内容
  2. 提取所有 h1/h2/h3 标题文本
  3. 统计标题中的词频（中文分词 + 英文分词）
  4. 生成 Markdown 格式报告保存到文件

用法：
  python scripts/web_title_report.py <URL> [-o OUTPUT] [--top N]

示例：
  python scripts/web_title_report.py https://www.python.org
  python scripts/web_title_report.py https://docs.python.org/3/ -o report.md --top 20
"""

import argparse
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# 第一步：抓取网页
# ============================================================

def fetch_page(url: str, timeout: int = 15) -> str:
    """发送 HTTP GET 请求，返回网页 HTML 文本。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        # 自动检测编码
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text
    except requests.exceptions.Timeout:
        print(f"[超时] 请求超时 ({timeout}s): {url}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"[HTTP错误] {e}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"[请求失败] {e}")
        sys.exit(1)


# ============================================================
# 第二步：提取标题
# ============================================================

def extract_headings(html: str) -> dict[str, list[str]]:
    """
    从 HTML 中提取 h1 / h2 / h3 标题文本。

    返回格式：{"h1": [...], "h2": [...], "h3": [...]}
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, list[str]] = {}

    # 移除 script / style 等干扰标签
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    for tag_name in ("h1", "h2", "h3"):
        headings = []
        for tag in soup.find_all(tag_name):
            text = tag.get_text(strip=True)
            if text:  # 跳过空标题
                headings.append(text)
        result[tag_name] = headings

    return result


# ============================================================
# 第三步：词频统计
# ============================================================

def tokenize(text: str) -> list[str]:
    """
    对文本进行分词，支持中文（单字/词）和英文单词。
    返回小写化后的词列表。
    """
    # 检测是否包含中文
    has_chinese = any("\u4e00" <= ch <= "\u9fff" for ch in text)

    if has_chinese:
        # 中文：提取中文段落 + 英文单词
        tokens = []

        # 提取连续的中文字符串，按字符切分为单字
        chinese_parts = re.findall(r"[\u4e00-\u9fff]+", text)
        for part in chinese_parts:
            # 对中文按字符拆分（单字）
            tokens.extend(list(part))

        # 提取英文单词
        english_words = re.findall(r"[a-zA-Z]+(?:[-'][a-zA-Z]+)*", text)
        tokens.extend(w.lower() for w in english_words)

        return tokens
    else:
        # 纯英文：按非字母字符拆分
        words = re.findall(r"[a-zA-Z]+(?:[-'][a-zA-Z]+)*", text.lower())
        return words


def count_word_frequency(
    headings: dict[str, list[str]],
    top_n: int = 30,
) -> tuple[Counter, Counter, Counter]:
    """
    统计标题词频，按标题级别分别统计。

    返回：(h1_freq, h2_freq, h3_freq) 三个 Counter
    """
    freq_h1: Counter = Counter()
    freq_h2: Counter = Counter()
    freq_h3: Counter = Counter()

    for text in headings.get("h1", []):
        freq_h1.update(tokenize(text))

    for text in headings.get("h2", []):
        freq_h2.update(tokenize(text))

    for text in headings.get("h3", []):
        freq_h3.update(tokenize(text))

    return freq_h1, freq_h2, freq_h3


# ============================================================
# 第四步：生成 Markdown 报告
# ============================================================

def generate_report(
    url: str,
    headings: dict[str, list[str]],
    freq_h1: Counter,
    freq_h2: Counter,
    freq_h3: Counter,
    top_n: int = 30,
) -> str:
    """生成完整的 Markdown 报告。"""
    lines = []
    lines.append("# 网页标题分析报告")
    lines.append("")
    lines.append(f"- **目标 URL**：{url}")
    lines.append(f"- **分析时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **标题总数**：{sum(len(v) for v in headings.values())}")
    lines.append("")

    # ---- 标题汇总 ----
    lines.append("## 一、标题汇总")
    lines.append("")
    for tag in ("h1", "h2", "h3"):
        items = headings.get(tag, [])
        lines.append(f"### {tag.upper()}（共 {len(items)} 个）")
        lines.append("")
        if items:
            for i, h in enumerate(items, 1):
                lines.append(f"{i}. {h}")
        else:
            lines.append("（无）")
        lines.append("")

    # ---- 词频统计 ----
    lines.append("## 二、词频统计")
    lines.append("")

    sections = [
        (f"H1 标题词频 Top {top_n}", freq_h1),
        (f"H2 标题词频 Top {top_n}", freq_h2),
        (f"H3 标题词频 Top {top_n}", freq_h3),
    ]

    for section_title, freq in sections:
        lines.append(f"### {section_title}")
        lines.append("")
        lines.append("| 排名 | 词语 | 出现次数 |")
        lines.append("| --- | --- | --- |")
        if freq:
            for rank, (word, cnt) in enumerate(freq.most_common(top_n), 1):
                lines.append(f"| {rank} | {word} | {cnt} |")
        else:
            lines.append("| - | （无数据） | - |")
        lines.append("")

    # ---- 总词频（合并所有标题级别） ----
    total_freq = freq_h1 + freq_h2 + freq_h3
    lines.append(f"### 总词频 Top {top_n}（合并所有标题）")
    lines.append("")
    lines.append("| 排名 | 词语 | 出现次数 |")
    lines.append("| --- | --- | --- |")
    if total_freq:
        for rank, (word, cnt) in enumerate(total_freq.most_common(top_n), 1):
            lines.append(f"| {rank} | {word} | {cnt} |")
    else:
        lines.append("| - | （无数据） | - |")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# 主函数
# ============================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从URL抓取网页，提取h1/h2/h3标题，统计词频，生成Markdown报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="目标网页 URL（如 https://www.python.org）")
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出 Markdown 报告路径（默认自动生成文件名）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="词频统计 Top N（默认 30）",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    url = args.url.rstrip("/")
    top_n = args.top

    print(f"[抓取] 正在抓取: {url}")
    html = fetch_page(url)

    print("[提取] 正在提取标题（h1/h2/h3）...")
    headings = extract_headings(html)

    total = sum(len(v) for v in headings.values())
    print(f"   -> 共提取 {total} 个标题（h1: {len(headings['h1'])}, h2: {len(headings['h2'])}, h3: {len(headings['h3'])}）")

    print(f"[统计] 正在统计词频（Top {top_n}）...")
    freq_h1, freq_h2, freq_h3 = count_word_frequency(headings, top_n)

    print("[报告] 正在生成 Markdown 报告...")
    report = generate_report(url, headings, freq_h1, freq_h2, freq_h3, top_n)

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        # 自动生成：从 URL 提取域名作为文件名
        domain = re.sub(r"https?://", "", url).split("/")[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(f"report_{domain}_{timestamp}.md")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"[完成] 报告已保存: {output_path.resolve()}")


if __name__ == "__main__":
    main()
