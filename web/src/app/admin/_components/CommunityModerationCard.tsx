"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminDeleteCommunityComment,
  adminDeleteCommunityPost,
  adminListCommunityModeration,
  adminRestoreCommunityComment,
  adminRestoreCommunityPost,
  type AdminCommunityModerationItem,
} from "@/lib/apiClient";
import { fmtDateTime } from "../_lib/datetime";

const PAGE = 20;

export function CommunityModerationCard({ onError }: { onError: (msg: string | null) => void }) {
  const [items, setItems] = useState<AdminCommunityModerationItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await adminListCommunityModeration({ target_type: "all", limit: PAGE, cursor: null });
      setItems(data.items);
      setNextCursor(data.next_cursor);
    } catch (err) {
      onError((err as Error).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(action: () => Promise<unknown>) {
    setBusy(true);
    onError(null);
    try {
      await action();
      await load();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function loadMore() {
    if (nextCursor == null || loadingMore) return;
    setLoadingMore(true);
    onError(null);
    try {
      const data = await adminListCommunityModeration({ target_type: "all", limit: PAGE, cursor: nextCursor });
      setItems((prev) => [...prev, ...data.items]);
      setNextCursor(data.next_cursor);
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }

  function restore(item: AdminCommunityModerationItem) {
    return item.target_type === "post"
      ? adminRestoreCommunityPost(item.id)
      : adminRestoreCommunityComment(item.id);
  }

  function remove(item: AdminCommunityModerationItem) {
    return item.target_type === "post"
      ? adminDeleteCommunityPost(item.id)
      : adminDeleteCommunityComment(item.id);
  }

  return (
    <section className="card mt-8 p-6" data-flow="moderation_review">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">커뮤니티 검토</h2>
          <p className="mt-0.5 text-[12.5px] text-muted">신고로 숨김 처리된 게시글과 댓글</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy}
          className="rounded-full border border-line px-4 py-2 text-[13px] font-semibold text-navy-soft disabled:opacity-50"
        >
          새로고침
        </button>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-[12.5px]">
          <thead className="text-muted">
            <tr>
              <th className="py-2 pr-4 font-semibold">대상</th>
              <th className="py-2 pr-4 font-semibold">내용</th>
              <th className="py-2 pr-4 font-semibold">신고</th>
              <th className="py-2 pr-4 font-semibold">최근 신고</th>
              <th className="py-2 pr-4 font-semibold">조치</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td className="py-3 text-muted" colSpan={5}>
                  검토 중인 숨김 항목이 없습니다.
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={`${item.target_type}-${item.id}`} className="border-t border-line align-top">
                  <td className="py-2 pr-4 font-semibold">
                    {item.target_type === "post" ? "게시글" : "댓글"}
                  </td>
                  <td className="max-w-[420px] py-2 pr-4">
                    <div className="font-semibold text-navy">
                      {item.target_type === "post" ? item.post.title : item.post_title ?? `#${item.post_id}`}
                    </div>
                    <div className="mt-1 line-clamp-2 text-muted">
                      {item.target_type === "post" ? item.post.body || "-" : item.body || "-"}
                    </div>
                  </td>
                  <td className="py-2 pr-4">
                    <div className="font-semibold">{item.report_count}</div>
                    <div className="mt-1 max-w-[180px] truncate text-muted">
                      {item.report_reasons.length ? item.report_reasons.join(", ") : "-"}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-muted">
                    {item.latest_reported_at ? fmtDateTime(item.latest_reported_at) : "-"}
                  </td>
                  <td className="py-2 pr-4">
                    <div className="flex flex-wrap gap-3">
                      <button
                        type="button"
                        onClick={() => void runAction(() => restore(item))}
                        disabled={busy}
                        className="text-[12.5px] font-semibold text-sky-deep disabled:opacity-50"
                      >
                        복구
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (window.confirm("숨김 항목을 삭제할까요?")) {
                            void runAction(() => remove(item));
                          }
                        }}
                        disabled={busy}
                        className="text-[12.5px] font-semibold text-muted hover:text-red disabled:opacity-50"
                      >
                        삭제
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {nextCursor != null && (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={busy || loadingMore}
          className="mt-4 rounded-full border border-line px-4 py-2 text-[13px] font-semibold text-navy-soft disabled:opacity-50"
        >
          {loadingMore ? "불러오는 중..." : "더 보기"}
        </button>
      )}
    </section>
  );
}
