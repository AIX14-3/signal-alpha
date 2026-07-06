"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminGetQueueOverview,
  adminReconcileDeadLetters,
  adminReplayDeadLetters,
  adminRetryQueueTask,
  adminSweepStaleQueue,
  type AdminDeadLetter,
  type AdminQueueOverview,
  type AdminQueueTask,
} from "@/lib/apiClient";
import { fmtDateTime } from "../_lib/datetime";

const QUEUE_EVENT_LABEL: Record<string, string> = {
  queue_backlog: '큐 적체',
  queue_failed: '큐 실패',
  dead_letter_pending: 'Dead Letter 대기',
  schedule_health: '스케줄 상태 확인',
  failed_task_list: '실패 작업 목록',
};

function queueCount(overview: AdminQueueOverview | null, status: string): number {
  return overview?.queue.totals_by_status?.[status] ?? 0;
}

export function QueueOpsCard({ onError }: { onError: (msg: string | null) => void }) {
  const [overview, setOverview] = useState<AdminQueueOverview | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await adminGetQueueOverview();
      setOverview(data);
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

  if (!overview) return null;

  const failedTasks = overview.failed_tasks.items ?? [];
  const deadLetters = overview.dead_letters.items ?? [];

  return (
    <section className="card mt-8 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">{'\ud050 / Dead Letter \uc870\uce58'}</h2>
          <p className="mt-0.5 text-[12.5px] text-muted">
            {'\uc2a4\ucf00\uc904\ub7ec \uc9c0\uc5f0\uacfc \ud050 \uc801\uccb4\ub97c \ud55c \uacf3\uc5d0\uc11c \ud655\uc778\ud558\uace0 \uc6b4\uc601 \uc870\uce58\ub97c \uc2e4\ud589\ud569\ub2c8\ub2e4.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void load()}
            disabled={busy}
            className="rounded-full border border-line px-4 py-2 text-[13px] font-semibold text-navy-soft disabled:opacity-50"
          >
            {'\uc0c8\ub85c\uace0\uce68'}
          </button>
          <button
            type="button"
            onClick={() => void runAction(() => adminSweepStaleQueue({ running_timeout_minutes: 30, retrying_timeout_minutes: 120 }))}
            disabled={busy}
            className="rounded-full border border-line px-4 py-2 text-[13px] font-semibold text-navy-soft disabled:opacity-50"
          >
            {'stale \uc815\ub9ac'}
          </button>
          <button
            type="button"
            onClick={() => void runAction(() => adminReconcileDeadLetters(100))}
            disabled={busy}
            className="rounded-full border border-line px-4 py-2 text-[13px] font-semibold text-navy-soft disabled:opacity-50"
          >
            {'Dead Letter \ubcf4\uc815'}
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <OpsMetric label="pending" value={queueCount(overview, 'pending')} />
        <OpsMetric label="retrying" value={queueCount(overview, 'retrying')} />
        <OpsMetric label="failed" value={queueCount(overview, 'failed')} tone="text-red" />
        <OpsMetric label="dead letter" value={overview.queue.dead_letter?.unreplayed ?? overview.dead_letters.count} tone="text-red" />
        <OpsMetric label="schedule" value={overview.schedule_summary.attention_count} />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <div>
          <h3 className="text-[13px] font-bold">{'\uc6b4\uc601 \uc774\ubca4\ud2b8'}</h3>
          <div className="mt-2 space-y-2">
            {overview.events.length === 0 ? (
              <p className="rounded-md bg-surface-2 px-3 py-2 text-[12.5px] text-muted">
                {'\ud604\uc7ac \ucd94\uac00 \ud655\uc778\uc774 \ud544\uc694\ud55c \uc6b4\uc601 \uc774\ubca4\ud2b8\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.'}
              </p>
            ) : (
              overview.events.map((event) => (
                <div key={`${event.type}-${event.count ?? 0}`} className="rounded-md border border-line px-3 py-2">
                  <div className="text-[12.5px] font-semibold">
                    {QUEUE_EVENT_LABEL[event.type] ?? event.type}
                  </div>
                  <div className="mt-0.5 text-[12.5px] text-muted">{event.message}</div>
                </div>
              ))
            )}
          </div>
        </div>

        <div>
          <h3 className="text-[13px] font-bold">{'\ud1b5\ud569 \uc694\uc57d'}</h3>
          <dl className="mt-2 space-y-1.5 rounded-md bg-surface-2 p-3 text-[12.5px]">
            <div className="flex justify-between gap-4">
              <dt className="text-muted">{'\ud050 \uc804\uccb4'}</dt>
              <dd className="font-semibold">{overview.queue.total}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">{'\uc2a4\ucf00\uc904 \ucd94\uac00 \ud655\uc778'}</dt>
              <dd className="font-semibold">{overview.schedule_summary.attention_count}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">{'Dead Letter \ubbf8\uc7ac\ucc98\ub9ac'}</dt>
              <dd className="font-semibold">{overview.queue.dead_letter?.unreplayed ?? overview.dead_letters.count}</dd>
            </div>
          </dl>
        </div>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <QueueTaskList
          items={failedTasks}
          busy={busy}
          onRetry={(task) => runAction(() => adminRetryQueueTask(task.id))}
        />
        <DeadLetterList
          items={deadLetters}
          busy={busy}
          onReplay={(item) => runAction(() => adminReplayDeadLetters([item.id]))}
        />
      </div>
    </section>
  );
}

function OpsMetric({ label, value, tone = 'text-navy' }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-md border border-line px-3 py-2">
      <div className="text-[12px] font-semibold text-muted">{label}</div>
      <div className={`mt-1 text-[20px] font-extrabold ${tone}`}>{value}</div>
    </div>
  );
}

function QueueTaskList({
  items,
  busy,
  onRetry,
}: {
  items: AdminQueueTask[];
  busy: boolean;
  onRetry: (task: AdminQueueTask) => Promise<void>;
}) {
  return (
    <div>
      <h3 className="text-[13px] font-bold">{'\uc2e4\ud328 \ud050 \uc791\uc5c5'}</h3>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-[12.5px]">
          <thead className="text-muted">
            <tr>
              <th className="py-1.5 pr-3 font-semibold">ID</th>
              <th className="py-1.5 pr-3 font-semibold">type</th>
              <th className="py-1.5 pr-3 font-semibold">status</th>
              <th className="py-1.5 pr-3 font-semibold">{'\uc870\uce58'}</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td className="py-2 text-muted" colSpan={4}>{'\uc2e4\ud328 \ud050 \uc791\uc5c5\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.'}</td>
              </tr>
            ) : (
              items.map((task) => (
                <tr key={task.id} className="border-t border-line">
                  <td className="py-1.5 pr-3 font-semibold">{task.id}</td>
                  <td className="py-1.5 pr-3">{task.task_type}</td>
                  <td className="py-1.5 pr-3">{task.status}</td>
                  <td className="py-1.5 pr-3">
                    <button
                      type="button"
                      onClick={() => void onRetry(task)}
                      disabled={busy}
                      className="text-[12.5px] font-semibold text-sky-deep disabled:opacity-50"
                    >
                      {'\uc7ac\uc2dc\ub3c4'}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DeadLetterList({
  items,
  busy,
  onReplay,
}: {
  items: AdminDeadLetter[];
  busy: boolean;
  onReplay: (item: AdminDeadLetter) => Promise<void>;
}) {
  return (
    <div>
      <h3 className="text-[13px] font-bold">{'Dead Letter'}</h3>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-[12.5px]">
          <thead className="text-muted">
            <tr>
              <th className="py-1.5 pr-3 font-semibold">ID</th>
              <th className="py-1.5 pr-3 font-semibold">type</th>
              <th className="py-1.5 pr-3 font-semibold">replayed</th>
              <th className="py-1.5 pr-3 font-semibold">{'\uc870\uce58'}</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td className="py-2 text-muted" colSpan={4}>{'Dead Letter\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.'}</td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.id} className="border-t border-line">
                  <td className="py-1.5 pr-3 font-semibold">{item.id}</td>
                  <td className="py-1.5 pr-3">{item.task_type}</td>
                  <td className="py-1.5 pr-3">{item.replayed_at ? fmtDateTime(item.replayed_at) : '-'}</td>
                  <td className="py-1.5 pr-3">
                    <button
                      type="button"
                      onClick={() => void onReplay(item)}
                      disabled={busy || Boolean(item.replayed_at)}
                      className="text-[12.5px] font-semibold text-sky-deep disabled:opacity-50"
                    >
                      {'\uc7ac\ucc98\ub9ac'}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
