"""拉取真实物流轨迹存为测试集（规格 scripts/fetch_tracking_seed.py）。

读一个 (tracking_no, carrier) 列表，对每个调 tools/tracking.get_track_info（live 模式），
把规范化轨迹写入 tracking_cache 表，供离线演示与 detect_exception 测试。
需 TRACKING_MODE=live 且配置 17TRACK_API_KEY；注册扣 1 额度，之后反复查询不扣额度。

用法：
  python -m scripts.fetch_tracking_seed --in datasets/tracking_seed.jsonl
datasets/tracking_seed.jsonl 每行：{"tracking_no": "...", "carrier": "..."}
"""
import argparse
import json
from pathlib import Path

from app.tools.tracking import get_track_info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--in",
        dest="path",
        default="datasets/tracking_seed.jsonl",
        help="(tracking_no, carrier) 列表",
    )
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        print(f"缺少输入文件：{p}（每行 {{tracking_no, carrier}}）")
        return

    n = 0
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            info = get_track_info(row["tracking_no"])
            if info:
                n += 1
                print(f"cached {row['tracking_no']}: {len(info.get('events', []))} events")
            else:
                print(f"无数据：{row['tracking_no']}（检查 live 模式与密钥）")
    print(f"完成，缓存 {n} 条轨迹于 tracking_cache")


if __name__ == "__main__":
    main()
