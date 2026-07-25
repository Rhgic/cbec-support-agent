"""知识库构建与切分：读取原始政策素材（本地 markdown 优先），写入 knowledge_docs 与
knowledge_chunks（切片正文，embedding 留空待 embed_knowledge 填充）。

为什么本地优先：CI / 离线环境无外网，且政策页是稳定的中文素材；远程 URL 仅作补充。
切分保持中文原样——跨语种靠 BGE-m3 embedding，不翻译（规格 1.3 / 4.1）。
"""
import argparse
from pathlib import Path

from app.database import SessionLocal
from app.models import KnowledgeChunk, KnowledgeDoc

# 默认读取项目内联的原始素材目录；放这里便于离线构建
RAW_DIR = Path("datasets/knowledge_raw")

# --all 模式：按类目分子目录构建，每个子目录套用各自正确的 category。
# 修正原缺陷——扁平目录 + 单个 --category 会把所有 md 打成同一类目，
# 导致 logistics/product 的内容被错标为 return，检索的 category 过滤随之失真。
CATEGORY_DIRS = {
    "logistics": RAW_DIR / "logistics",
    "return": RAW_DIR / "return",
    "product": RAW_DIR / "product",
}


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """按字符滑动窗口切分。

    为什么不用句子模型：纯中文政策文本按长度切即可，避免额外依赖；overlap
    让边界处语义不丢失。
    """
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def load_raw() -> list[tuple[str, str, str]]:
    """返回 [(source_url, title, content)]。"""
    items: list[tuple[str, str, str]] = []
    if RAW_DIR.exists():
        for p in sorted(RAW_DIR.glob("*.md")):
            content = p.read_text(encoding="utf-8")
            first = content.splitlines()[0].lstrip("# ").strip() if content else p.stem
            items.append((f"file://{p.name}", first or p.stem, content))
    return items


def build(category: str, raw_dir: str | None = None) -> int:
    """构建知识库：插入 docs 与对应 chunks（embedding 为空）。返回 doc 数。"""
    base = Path(raw_dir) if raw_dir else RAW_DIR
    db = SessionLocal()
    try:
        n = 0
        for url, title, content in load_raw() if raw_dir is None else _load_from(base):
            doc = KnowledgeDoc(
                source_url=url, title=title, category=category, content=content
            )
            db.add(doc)
            db.flush()  # 拿到 doc.id 供 chunk 外键
            for chunk in chunk_text(content):
                db.add(
                    KnowledgeChunk(
                        doc_id=doc.id,
                        content=chunk,
                        category=category,
                        token_count=len(chunk),
                    )
                )
            n += 1
        db.commit()
        return n
    finally:
        db.close()


def _load_from(base: Path) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    if not base.exists():
        return items
    for p in sorted(base.glob("*.md")):
        content = p.read_text(encoding="utf-8")
        first = content.splitlines()[0].lstrip("# ").strip() if content else p.stem
        items.append((f"file://{p.name}", first or p.stem, content))
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", choices=["return", "logistics", "product", "general"])
    ap.add_argument("--raw-dir", default=None, help="本地 markdown 目录，默认 datasets/knowledge_raw")
    ap.add_argument(
        "--all", action="store_true",
        help="按 CATEGORY_DIRS 逐类构建（每类一个子目录，各自套用正确 category）——推荐",
    )
    args = ap.parse_args()

    if args.all:
        total = 0
        for cat, d in CATEGORY_DIRS.items():
            if not d.exists():
                print(f"跳过 {cat}: 目录不存在 {d}")
                continue
            n = build(cat, str(d))
            total += n
            print(f"  {cat}: inserted {n} docs from {d}")
        print(f"inserted {total} docs total (chunks pending embedding)")
        return

    if not args.category:
        ap.error("需指定 --category，或用 --all 逐类构建")
    print(f"inserted {build(args.category, args.raw_dir)} docs (chunks pending embedding)")


if __name__ == "__main__":
    main()
