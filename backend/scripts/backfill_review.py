"""
回填已有 DailyReview 记录的 index_snapshot 和 daily_pnl

用法:
    cd backend && conda run -n quant python scripts/backfill_review.py
    cd backend && conda run -n quant python scripts/backfill_review.py --force   # 强制重新拉取所有
"""
import sys
import os
import json
import time

# 把 backend/ 加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, inspect
from data_engine.storage.database import engine, get_session
from data_engine.storage.models import DailyReview
from review.service import ReviewService


def ensure_column():
    """确保 index_snapshot 列存在"""
    insp = inspect(engine)
    if "daily_reviews" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("daily_reviews")}
        if "index_snapshot" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE daily_reviews ADD COLUMN index_snapshot TEXT"))
            print("✓ 已添加 index_snapshot 列")
        else:
            print("✓ index_snapshot 列已存在")


def backfill(force: bool = False):
    svc = ReviewService()
    session = get_session()
    try:
        reviews = session.query(DailyReview).order_by(DailyReview.review_date.asc()).all()
        print(f"共 {len(reviews)} 条 DailyReview 记录需要回填")
        if force:
            print("(--force 模式：强制重新拉取所有指数)\n")
        else:
            print()

        updated = 0
        for i, review in enumerate(reviews, 1):
            rd = review.review_date
            changed = False

            # --- 回填 index_snapshot ---
            # 检查是否需要拉取：force 模式、无快照、或旧格式（无 a_share_closed 字段）
            need_fetch = force or not review.index_snapshot
            if not need_fetch:
                try:
                    old = json.loads(review.index_snapshot)
                    if "a_share_closed" not in old:
                        need_fetch = True  # 旧格式，需要重新生成
                except json.JSONDecodeError:
                    need_fetch = True

            if need_fetch:
                empty_items = [
                    {"symbol": c, "name": svc.INDEX_NAMES[c], "price": None, "change_pct": None}
                    for c in svc.INDEX_ORDER
                ]
                if rd.weekday() >= 5:
                    snapshot = svc._build_indices_result(empty_items, all_closed=True)
                    review.index_snapshot = json.dumps(snapshot, ensure_ascii=False)
                    changed = True
                    print(f"  [{i}/{len(reviews)}] {rd} 全部休市（周末）✓")
                else:
                    items = svc._fetch_indices_from_history(rd)
                    if items:
                        snapshot = svc._build_indices_result(items)
                        review.index_snapshot = json.dumps(snapshot, ensure_ascii=False)
                        changed = True
                        closed_parts = []
                        if snapshot["a_share_closed"]:
                            closed_parts.append("A股休市")
                        if snapshot["hk_closed"]:
                            closed_parts.append("港股休市")
                        if snapshot["us_closed"]:
                            closed_parts.append("美股休市")
                        tag = f' ({", ".join(closed_parts)})' if closed_parts else ''
                        print(f"  [{i}/{len(reviews)}] {rd} 指数快照 ✓{tag}")
                    else:
                        snapshot = svc._build_indices_result(empty_items, all_closed=True)
                        review.index_snapshot = json.dumps(snapshot, ensure_ascii=False)
                        changed = True
                        print(f"  [{i}/{len(reviews)}] {rd} 全部休市（节假日）✓")
                    time.sleep(0.3)
            else:
                print(f"  [{i}/{len(reviews)}] {rd} 指数快照 — 已有，跳过")

            # --- 回填 daily_pnl ---
            positions_daily = svc._get_positions_daily(session, rd)
            new_pnl = round(sum(p.get("daily_pnl", 0) or 0 for p in positions_daily), 2)
            if review.daily_pnl != new_pnl:
                old_pnl = review.daily_pnl
                review.daily_pnl = new_pnl
                review.positions_count = len(positions_daily)
                changed = True
                print(f"           {rd} daily_pnl: {old_pnl} → {new_pnl}")

            if changed:
                updated += 1

        session.commit()
        print(f"\n✓ 回填完成，更新了 {updated}/{len(reviews)} 条记录")

    except Exception as e:
        session.rollback()
        print(f"✗ 回填失败: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    ensure_column()
    force_mode = "--force" in sys.argv
    backfill(force=force_mode)
