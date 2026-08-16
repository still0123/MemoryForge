#!/usr/bin/env python3
"""Render the "MemoryForge vs vanilla RAG" pipeline comparison diagrams.

Outputs:
    assets/diagrams/vs-rag-pipeline.zh.svg
    assets/diagrams/vs-rag-pipeline.en.svg

Style follows the existing asset palette (brand blue #1664FF, mint #18A980,
alert red #D94A4A) so the new diagram matches 01..06 diagram series.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "diagrams"

CJK_FONT = "'Noto Sans CJK SC','PingFang SC','Microsoft YaHei',sans-serif"
LATIN_FONT = "'Inter','Helvetica Neue',Arial,sans-serif"

W = 1600
PANEL_W = 700
PANEL_X_RAG = 60
PANEL_X_MF = 840
PANEL_Y = 156
HEADER_H = 78
STAGE_H = 80
STAGE_GAP = 44
OUTCOME_H = 62

STYLES = {
    "zh": {
        "font": CJK_FONT,
        "title": "同样的资料，两条知识管线",
        "subtitle": "普通 RAG 直接检索原文切片；MemoryForge 先把资料编译成可审核的 Wiki，再渐进式查询。",
        "rag_header": "普通 RAG",
        "rag_sub": "切片 → 向量化 → 召回 → 直接生成",
        "rag_stages": [
            ("原始资料", "文档、代码、网页随时更新", "无版本锁定"),
            ("固定窗口切片", "结构和上下文在切片时丢失", "语义破碎"),
            ("向量化写入向量库", "重建索引直接替换旧数据", "无审核记录"),
            ("相似度 Top-K 召回", "按向量距离取回片段", "相关 ≠ 支持"),
            ("LLM 直接生成答案", "召回什么用什么，无法核验", "引用漂移"),
        ],
        "rag_outcome": "结论无法重放：说不清“当时依据的是哪个版本的哪段原文”",
        "mf_header": "MemoryForge",
        "mf_sub": "快照 → 提案 → 审核 → 发布 → 渐进式查询",
        "mf_stages": [
            ("原始资料", "Git 仓库、飞书、网页、Issue、AI 会话", "默认 local_only"),
            ("SourceAdapter 快照", "不可变 SourceVersion，SHA-256 锁定", "来源可回放"),
            ("WikiCompiler 提案", "只生成 PROPOSED ChangeSet", "AI 无发布权"),
            ("review → approve → apply", "人工审阅 Diff 与引用后授权落盘", "Git 可回滚"),
            ("Wiki + SQLite FTS5", "可读 Markdown + 本地全文索引", "人机共用一份知识"),
            ("渐进式查询", "INDEX.md 定位 → Citation → 按需展开 Evidence", "按需加载"),
        ],
        "mf_outcome": "结论可重放：Citation 固定 SourceVersion + locator + Commit + SHA-256",
        "banner": "MemoryForge 不是“更强的检索”，而是把知识更新变成一条可审计的工程链路：编译、审核、发布、溯源。",
    },
    "en": {
        "font": LATIN_FONT,
        "title": "Same sources, two knowledge pipelines",
        "subtitle": "Vanilla RAG retrieves raw chunks; MemoryForge compiles sources into an auditable Wiki first, then queries progressively.",
        "rag_header": "Vanilla RAG",
        "rag_sub": "chunk → embed → recall → generate",
        "rag_stages": [
            ("Raw sources", "Docs, code, pages updated at will", "No version pinning"),
            ("Fixed-window chunking", "Structure and context lost at chunk time", "Fragmented semantics"),
            ("Embed into vector store", "Re-indexing silently replaces old data", "No review trail"),
            ("Top-K similarity recall", "Picks fragments by vector distance", "Relevant ≠ supported"),
            ("LLM answers directly", "Uses whatever was recalled", "Citation drift"),
        ],
        "rag_outcome": "Not replayable: no way to tell which version of which passage a claim came from",
        "mf_header": "MemoryForge",
        "mf_sub": "snapshot → propose → review → publish → progressive query",
        "mf_stages": [
            ("Raw sources", "Git repos, Feishu, web, Issues, AI chats", "local_only by default"),
            ("SourceAdapter snapshot", "Immutable SourceVersion pinned by SHA-256", "Replayable source"),
            ("WikiCompiler proposal", "Emits PROPOSED ChangeSets only", "AI never publishes"),
            ("review → approve → apply", "Human reviews diff & citations, then authorizes", "Git rollback"),
            ("Wiki + SQLite FTS5", "Readable Markdown + local full-text index", "One base for human & AI"),
            ("Progressive query", "INDEX.md → Citation → Evidence on demand", "Context on demand"),
        ],
        "mf_outcome": "Replayable: citations pin SourceVersion + locator + Commit + SHA-256",
        "banner": "MemoryForge is not “better retrieval” — it turns knowledge updates into an auditable pipeline: compile, review, publish, trace.",
    },
}


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def svg_text(x: float, y: float, size: float, fill: str, content: str, *, weight: int = 400,
             anchor: str = "start", font: str = CJK_FONT, spacing: float = 0) -> str:
    return (
        f'<text x="{x:g}" y="{y:g}" font-family="{font}" font-size="{size:g}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" letter-spacing="{spacing:g}">'
        f"{esc(content)}</text>"
    )


def stage_arrow(x_center: float, y_top: float, color: str, marker: str) -> str:
    y_bottom = y_top + STAGE_GAP
    return (
        f'<line x1="{x_center:g}" y1="{y_top + 6:g}" x2="{x_center:g}" y2="{y_bottom - 6:g}" '
        f'stroke="{color}" stroke-width="2.5" stroke-linecap="round" marker-end="url(#{marker})"/>'
    )


def panel(x: float, header_title: str, header_sub: str, stages: list, outcome: str, *,
          accent: str, header_bg: str, header_fg: str, stage_mark_fg: str,
          stage_mark_bg: str, arrow_color: str, arrow_marker: str, outcome_bg: str,
          outcome_fg: str, font: str) -> tuple[str, float]:
    """Draw one pipeline panel; returns (svg_fragment, panel_height)."""
    parts: list[str] = []
    n = len(stages)
    panel_h = HEADER_H + 18 + n * STAGE_H + (n - 1) * STAGE_GAP + 22 + OUTCOME_H + 4

    # Panel card with colored accent border.
    parts.append(
        f'<rect x="{x:g}" y="{PANEL_Y:g}" width="{PANEL_W:g}" height="{panel_h:g}" rx="22" '
        f'fill="#FFFFFF" stroke="{accent}" stroke-width="{2 if accent != "#D9E4F5" else 1.5}" '
        f'filter="url(#panelShadow)"/>'
    )
    # Header strip.
    parts.append(
        f'<path d="M {x:g} {PANEL_Y + 22:g} A 22 22 0 0 1 {x + 22:g} {PANEL_Y:g} '
        f'L {x + PANEL_W - 22:g} {PANEL_Y:g} A 22 22 0 0 1 {x + PANEL_W:g} {PANEL_Y + 22:g} '
        f'L {x + PANEL_W:g} {PANEL_Y + HEADER_H:g} L {x:g} {PANEL_Y + HEADER_H:g} Z" '
        f'fill="{header_bg}"/>'
    )
    parts.append(svg_text(x + 26, PANEL_Y + 34, 23, header_fg, header_title, weight=800, font=font))
    parts.append(svg_text(x + 26, PANEL_Y + 60, 13.5, "#69758C", header_sub, weight=450, font=font))

    # Stage boxes.
    for i, (title, detail, mark) in enumerate(stages):
        y = PANEL_Y + HEADER_H + 18 + i * (STAGE_H + STAGE_GAP)
        parts.append(
            f'<rect x="{x + 22:g}" y="{y:g}" width="{PANEL_W - 44:g}" height="{STAGE_H:g}" rx="12" '
            f'fill="#F7F9FC" stroke="#E3EAF4" stroke-width="1"/>'
        )
        parts.append(
            f'<rect x="{x + 22:g}" y="{y + 14:g}" width="4" height="{STAGE_H - 28:g}" rx="2" '
            f'fill="{accent if accent != "#D9E4F5" else "#8CA0BE"}"/>'
        )
        parts.append(svg_text(x + 44, y + 33, 16.5, "#1F2329", title, weight=750, font=font))
        parts.append(svg_text(x + 44, y + 58, 13, "#69758C", detail, weight=450, font=font))
        # Right-side mark chip (✗ pain / ✓ guarantee).
        chip_r = 10
        chip_cx = x + PANEL_W - 30 - chip_r
        parts.append(f'<circle cx="{chip_cx:g}" cy="{y + STAGE_H / 2:g}" r="{chip_r:g}" fill="{stage_mark_bg}"/>')
        if stage_mark_fg.startswith("#D"):
            parts.append(svg_text(chip_cx, y + STAGE_H / 2 + 5, 13, stage_mark_fg, "✗",
                                 weight=800, anchor="middle", font=font))
        else:
            parts.append(
                f'<path d="M {chip_cx - 4.5:g} {y + STAGE_H / 2 + 0.5:g} L {chip_cx - 1.2:g} '
                f'{y + STAGE_H / 2 + 4:g} L {chip_cx + 4.6:g} {y + STAGE_H / 2 - 3.6:g}" '
                f'fill="none" stroke="{stage_mark_fg}" stroke-width="2.6" '
                f'stroke-linecap="round" stroke-linejoin="round"/>'
            )
        # Truncate mark text to keep the chip clear.
        mark_x = chip_cx - chip_r - 10
        parts.append(svg_text(mark_x, y + STAGE_H / 2 + 4.5, 12.5, stage_mark_fg, mark,
                              weight=650, anchor="end", font=font))
        if i < n - 1:
            parts.append(stage_arrow(x + PANEL_W / 2, y + STAGE_H, arrow_color, arrow_marker))

    # Outcome strip.
    oy = PANEL_Y + HEADER_H + 18 + n * STAGE_H + (n - 1) * STAGE_GAP + 22
    parts.append(
        f'<rect x="{x + 22:g}" y="{oy:g}" width="{PANEL_W - 44:g}" height="{OUTCOME_H:g}" rx="14" '
        f'fill="{outcome_bg}"/>'
    )
    parts.append(svg_text(x + PANEL_W / 2, oy + OUTCOME_H / 2 + 6, 14.5, outcome_fg, outcome,
                          weight=700, anchor="middle", font=font))
    return "".join(parts), panel_h


def render(lang: str) -> str:
    s = STYLES[lang]
    font = s["font"]
    rag_svg, rag_h = panel(
        PANEL_X_RAG, s["rag_header"], s["rag_sub"], s["rag_stages"], s["rag_outcome"],
        accent="#D9E4F5", header_bg="#F1F4F9", header_fg="#14213D",
        stage_mark_fg="#D94A4A", stage_mark_bg="#FFE9E9",
        arrow_color="#8CA0BE", arrow_marker="arrowGray",
        outcome_bg="#FFF1F1", outcome_fg="#B93838", font=font,
    )
    mf_svg, mf_h = panel(
        PANEL_X_MF, s["mf_header"], s["mf_sub"], s["mf_stages"], s["mf_outcome"],
        accent="#18A980", header_bg="#DDF7EE", header_fg="#17885F",
        stage_mark_fg="#17885F", stage_mark_bg="#DDF7EE",
        arrow_color="#18A980", arrow_marker="arrowGreen",
        outcome_bg="#E4F8F0", outcome_fg="#14684A", font=font,
    )

    mf_bottom = PANEL_Y + mf_h
    banner_y = mf_bottom + 34
    banner_w = 1160
    total_h = int(banner_y + 52 + 30)

    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{total_h}" '
        f'viewBox="0 0 {W} {total_h}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{esc(s["title"])}</title>',
        f'<desc id="desc">{esc(s["subtitle"])}</desc>',
        """<defs>
  <filter id="panelShadow" x="-20%" y="-20%" width="140%" height="150%">
    <feDropShadow dx="0" dy="10" stdDeviation="16" flood-color="#17335C" flood-opacity="0.10"/>
  </filter>
  <filter id="vsShadow" x="-40%" y="-40%" width="180%" height="180%">
    <feDropShadow dx="0" dy="5" stdDeviation="8" flood-color="#17335C" flood-opacity="0.14"/>
  </filter>
  <linearGradient id="brandGrad" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#1664FF"/>
    <stop offset="1" stop-color="#5B5CE2"/>
  </linearGradient>
  <marker id="arrowGray" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto">
    <path d="M0,0 L10,5.5 L0,11 z" fill="#8CA0BE"/>
  </marker>
  <marker id="arrowGreen" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto">
    <path d="M0,0 L10,5.5 L0,11 z" fill="#18A980"/>
  </marker>
</defs>""",
        f'<rect width="{W}" height="{total_h}" fill="#F5F8FF"/>',
        svg_text(70, 66, 38, "#1F2329", s["title"], weight=800, font=font),
        svg_text(70, 102, 16.5, "#69758C", s["subtitle"], weight=450, font=font),
        '<path d="M70 124 L1530 124" fill="none" stroke="#D9E4F5" stroke-width="1" stroke-linecap="round"/>',
    ]

    vs_badge = (
        f'<circle cx="800" cy="{PANEL_Y + 44:g}" r="32" fill="url(#brandGrad)" filter="url(#vsShadow)"/>'
        + svg_text(800, PANEL_Y + 52, 21, "#FFFFFF", "VS", weight=850, anchor="middle", font=font)
    )

    banner = (
        f'<rect x="{(W - banner_w) / 2:g}" y="{banner_y:g}" width="{banner_w}" height="52" rx="16" '
        f'fill="#102A56" filter="url(#panelShadow)"/>'
        + svg_text(W / 2, banner_y + 33, 16.5, "#FFFFFF", s["banner"], weight=700,
                   anchor="middle", font=font)
    )

    return "\n".join(head) + rag_svg + mf_svg + vs_badge + banner + "\n</svg>\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for lang in ("zh", "en"):
        path = OUT_DIR / f"vs-rag-pipeline.{lang}.svg"
        path.write_text(render(lang), encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
