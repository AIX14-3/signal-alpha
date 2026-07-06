"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminDryRunSchedule,
  adminListScheduleRuns,
  adminListSchedules,
  adminTriggerSchedule,
  adminUpdateSchedule,
  type AdminSchedule,
  type AdminScheduleDryRun,
  type AdminScheduleRun,
} from "@/lib/apiClient";
import { fmtDateTime } from "../_lib/datetime";
import {
  ALL_TARGETS,
  DEFAULT_SCHEDULE_DRAFT,
  TARGET_LABEL,
  draftFromSchedule,
  formatScheduleHealth,
  formatScheduleRunDecision,
  formatScheduleRunTargetResult,
  getScheduleRunWarning,
  parseOptionalNumber,
  validateScheduleDraft,
  type ScheduleDraft,
} from "../_lib/schedule";

export function ScheduleCard({ onError }: { onError: (msg: string | null) => void }) {
  const [schedules, setSchedules] = useState<AdminSchedule[]>([]);
  const [drafts, setDrafts] = useState<Record<number, ScheduleDraft>>({});
  const [runsBySchedule, setRunsBySchedule] = useState<Record<number, AdminScheduleRun[]>>({});
  const [dryRuns, setDryRuns] = useState<Record<number, AdminScheduleDryRun>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { items } = await adminListSchedules();
      setSchedules(items);
      setDrafts(
        Object.fromEntries(
          items.map((schedule) => [
            schedule.id,
            draftFromSchedule(schedule),
          ]),
        ),
      );
      const runEntries = await Promise.all(
        items.map(async (schedule) => {
          const { items: runs } = await adminListScheduleRuns(schedule.id, 5);
          return [schedule.id, runs] as const;
        }),
      );
      setRunsBySchedule(Object.fromEntries(runEntries));
    } catch (err) {
      onError((err as Error).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  if (schedules.length === 0) return null;

  async function refreshRuns(scheduleId: number) {
    const { items } = await adminListScheduleRuns(scheduleId, 5);
    setRunsBySchedule((prev) => ({ ...prev, [scheduleId]: items }));
  }

  async function save(schedule: AdminSchedule, body: Parameters<typeof adminUpdateSchedule>[1]) {
    setBusy(true);
    onError(null);
    try {
      const updated = await adminUpdateSchedule(schedule.id, body);
      setSchedules((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setDrafts((prev) => ({
        ...prev,
        [updated.id]: draftFromSchedule(updated),
      }));
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function saveTargets(schedule: AdminSchedule, draft: ScheduleDraft) {
    const validation = validateScheduleDraft(draft);
    if (validation) {
      onError(validation);
      return;
    }
    await save(schedule, { targets: draft.targets });
  }

  async function saveCadence(schedule: AdminSchedule, draft: ScheduleDraft) {
    const validation = validateScheduleDraft(draft);
    if (validation) {
      onError(validation);
      return;
    }
    await save(schedule, {
      frequency_minutes: Number(draft.frequencyMinutes),
      active_from_local: draft.activeFrom,
      active_until_local: draft.activeUntil,
    });
  }

  async function savePolicy(schedule: AdminSchedule, draft: ScheduleDraft) {
    const validation = validateScheduleDraft(draft);
    if (validation) {
      onError(validation);
      return;
    }
    await save(schedule, {
      report_limit: Number(draft.reportLimit),
      report_days_back: Number(draft.reportDaysBack),
      report_max_pages: Number(draft.reportMaxPages),
      alternative_collect_enabled: draft.alternativeCollectEnabled,
      alternative_analyze_enabled: draft.alternativeAnalyzeEnabled,
      alternative_collect_timeout_seconds: Number(draft.alternativeCollectTimeoutSeconds),
      alternative_analyze_timeout_seconds: Number(draft.alternativeAnalyzeTimeoutSeconds),
      backpressure_max_waiting: parseOptionalNumber(draft.backpressureMaxWaiting),
      backpressure_max_failed: parseOptionalNumber(draft.backpressureMaxFailed),
    });
  }

  async function dryRunSchedule(schedule: AdminSchedule) {
    setBusy(true);
    onError(null);
    try {
      const result = await adminDryRunSchedule(schedule.id);
      setDryRuns((prev) => ({ ...prev, [schedule.id]: result }));
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function trigger(schedule: AdminSchedule) {
    if (!window.confirm(`${schedule.name ?? schedule.id} \uc2a4\ucf00\uc904\uc744 \ub2e4\uc74c \ud3f4\ub9c1\uc5d0\uc11c \uc2e4\ud589\ud558\ub3c4\ub85d \uc694\uccad\ud560\uae4c\uc694?`)) return;
    setBusy(true);
    onError(null);
    try {
      const updated = await adminTriggerSchedule(schedule.id);
      setSchedules((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      await refreshRuns(schedule.id);
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function setRunAt(scheduleId: number, runAt: string) {
    setDrafts((prev) => {
      const schedule = schedules.find((item) => item.id === scheduleId);
      const current = prev[scheduleId] ?? (schedule ? draftFromSchedule(schedule) : DEFAULT_SCHEDULE_DRAFT);
      return { ...prev, [scheduleId]: { ...current, runAt } };
    });
  }

  function setDraftField(schedule: AdminSchedule, key: keyof ScheduleDraft, value: string | boolean) {
    setDrafts((prev) => ({
      ...prev,
      [schedule.id]: {
        ...(prev[schedule.id] ?? draftFromSchedule(schedule)),
        [key]: value,
      },
    }));
  }

  function toggleTarget(scheduleId: number, key: string) {
    setDrafts((prev) => {
      const schedule = schedules.find((item) => item.id === scheduleId);
      const current = prev[scheduleId] ?? (schedule ? draftFromSchedule(schedule) : DEFAULT_SCHEDULE_DRAFT);
      const nextTargets = current.targets.includes(key)
        ? current.targets.filter((target) => target !== key)
        : [...current.targets, key];
      return { ...prev, [scheduleId]: { ...current, targets: nextTargets } };
    });
  }

  return (
    <section className="card mt-8 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">{'\uc218\uc9d1 \uc2a4\ucf00\uc904'}</h2>
          <p className="mt-0.5 text-[12.5px] text-muted">{'\uc18c\uc2a4\ubcc4 \uc218\uc9d1 \uc8fc\uae30\uc640 \ucd5c\uadfc \uc2e4\ud589 \uc774\ub825\uc744 \uad00\ub9ac\ud569\ub2c8\ub2e4.'}</p>
        </div>
      </div>

      <div className="mt-5 space-y-5">
        {schedules.map((schedule) => {
          const draft = drafts[schedule.id] ?? {
            runAt: schedule.run_at_local ?? '04:30',
            targets: schedule.targets ?? [],
            frequencyMinutes: String(schedule.frequency_minutes ?? 1440),
            activeFrom: schedule.active_from_local ?? schedule.run_at_local ?? '04:30',
            activeUntil: schedule.active_until_local ?? schedule.active_from_local ?? schedule.run_at_local ?? '04:30',
            reportLimit: String(schedule.report_limit ?? 100),
            reportDaysBack: String(schedule.report_days_back ?? 7),
            reportMaxPages: String(schedule.report_max_pages ?? 20),
            alternativeCollectEnabled: schedule.alternative_collect_enabled ?? true,
            alternativeAnalyzeEnabled: schedule.alternative_analyze_enabled ?? true,
            alternativeCollectTimeoutSeconds: String(schedule.alternative_collect_timeout_seconds ?? 3600),
            alternativeAnalyzeTimeoutSeconds: String(schedule.alternative_analyze_timeout_seconds ?? 3600),
            backpressureMaxWaiting: schedule.backpressure_max_waiting == null ? '' : String(schedule.backpressure_max_waiting),
            backpressureMaxFailed: schedule.backpressure_max_failed == null ? '' : String(schedule.backpressure_max_failed),
          };
          const frequencyMinutes = Number(draft.frequencyMinutes);
          const runs = runsBySchedule[schedule.id] ?? [];
          const health = formatScheduleHealth(schedule);
          const runWarning = getScheduleRunWarning(runs);
          const draftValidation = validateScheduleDraft(draft);
          const dryRun = dryRuns[schedule.id];
          return (
            <div key={schedule.id} className="rounded-xl border border-line p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-[15px] font-bold">{schedule.name ?? `schedule-${schedule.id}`}</h3>
                    <span className={`rounded-full border px-2.5 py-1 text-[12px] font-semibold ${health.className}`}>
                      {health.label}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[12.5px] text-muted">
                    {schedule.timezone ?? 'Asia/Seoul'} {schedule.run_at_local ?? '-'} {' - '}
                    {schedule.frequency_minutes ?? 1440}{'\ubd84 \uc8fc\uae30'} {' - '}
                    {schedule.enabled ? '\ud65c\uc131' : '\ube44\ud65c\uc131'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void save(schedule, { enabled: !schedule.enabled })}
                  disabled={busy}
                  className={`rounded-full px-4 py-2 text-[13.5px] font-semibold disabled:opacity-60 ${
                    schedule.enabled
                      ? 'border border-line text-navy-soft hover:border-navy'
                      : 'brand-grad text-white'
                  }`}
                >
                  {schedule.enabled ? '\ube44\ud65c\uc131\ud654' : '\ud65c\uc131\ud654'}
                </button>
              </div>

              <div className="mt-4 grid gap-5 lg:grid-cols-2">
                <div>
                  <label className="text-[12.5px] font-semibold text-muted">{'\uc2e4\ud589 \uc2dc\uac01 (KST)'}</label>
                  <div className="mt-2 flex items-center gap-2">
                    <input
                      type="time"
                      value={draft.runAt}
                      onChange={(e) => setRunAt(schedule.id, e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <button
                      type="button"
                      onClick={() => void save(schedule, { run_at_local: draft.runAt })}
                      disabled={busy || draft.runAt === schedule.run_at_local}
                      className="text-[13px] font-semibold text-sky-deep disabled:opacity-50"
                    >
                      {'\uc2dc\uac01 \uc800\uc7a5'}
                    </button>
                  </div>

                  <label className="mt-4 block text-[12.5px] font-semibold text-muted">{'\uc218\uc9d1 \ub300\uc0c1'}</label>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    {ALL_TARGETS.map((key) => (
                      <label key={key} className="flex items-center gap-1.5 text-[13.5px]">
                        <input
                          type="checkbox"
                          checked={draft.targets.includes(key)}
                          onChange={() => toggleTarget(schedule.id, key)}
                        />
                        {TARGET_LABEL[key] ?? key}
                      </label>
                    ))}
                    <button
                      type="button"
                      onClick={() => void saveTargets(schedule, draft)}
                      disabled={busy}
                      className="text-[13px] font-semibold text-sky-deep disabled:opacity-50"
                    >
                      {'\ub300\uc0c1 \uc800\uc7a5'}
                    </button>
                  </div>

                  <label className="mt-4 block text-[12.5px] font-semibold text-muted">{'\ubc18\ubcf5 \uc8fc\uae30 / \ud65c\uc131 \uc2dc\uac04'}</label>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={draft.frequencyMinutes}
                      onChange={(e) => setDraftField(schedule, 'frequencyMinutes', e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="time"
                      value={draft.activeFrom}
                      onChange={(e) => setDraftField(schedule, 'activeFrom', e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="time"
                      value={draft.activeUntil}
                      onChange={(e) => setDraftField(schedule, 'activeUntil', e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                  </div>
                  {draftValidation && (
                    <p className="mt-2 text-[12.5px] font-semibold text-red">{draftValidation}</p>
                  )}
                  <button
                    type="button"
                    onClick={() => void saveCadence(schedule, draft)}
                    disabled={busy || !Number.isInteger(frequencyMinutes) || frequencyMinutes < 1 || frequencyMinutes > 1440}
                    className="mt-2 text-[13px] font-semibold text-sky-deep disabled:opacity-50"
                  >
                    {'\uc8fc\uae30 \uc800\uc7a5'}
                  </button>

                  <label className="mt-5 block text-[12.5px] font-semibold text-muted">{'\uc2e4\ud589 \uc815\ucc45'}</label>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <input
                      type="number"
                      min={1}
                      max={1000}
                      value={draft.reportLimit}
                      onChange={(e) => setDraftField(schedule, 'reportLimit', e.target.value)}
                      placeholder="report_limit"
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="number"
                      min={1}
                      max={400}
                      value={draft.reportDaysBack}
                      onChange={(e) => setDraftField(schedule, 'reportDaysBack', e.target.value)}
                      placeholder="report_days_back"
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="number"
                      min={1}
                      max={200}
                      value={draft.reportMaxPages}
                      onChange={(e) => setDraftField(schedule, 'reportMaxPages', e.target.value)}
                      placeholder="report_max_pages"
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="number"
                      min={60}
                      max={86400}
                      value={draft.alternativeCollectTimeoutSeconds}
                      onChange={(e) => setDraftField(schedule, 'alternativeCollectTimeoutSeconds', e.target.value)}
                      placeholder="alternative_collect_timeout_seconds"
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="number"
                      min={60}
                      max={86400}
                      value={draft.alternativeAnalyzeTimeoutSeconds}
                      onChange={(e) => setDraftField(schedule, 'alternativeAnalyzeTimeoutSeconds', e.target.value)}
                      placeholder="alternative_analyze_timeout_seconds"
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="number"
                      min={0}
                      value={draft.backpressureMaxWaiting}
                      onChange={(e) => setDraftField(schedule, 'backpressureMaxWaiting', e.target.value)}
                      placeholder="backpressure_max_waiting"
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="number"
                      min={0}
                      value={draft.backpressureMaxFailed}
                      onChange={(e) => setDraftField(schedule, 'backpressureMaxFailed', e.target.value)}
                      placeholder="backpressure_max_failed"
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <label className="flex items-center gap-1.5 text-[13.5px]">
                      <input
                        type="checkbox"
                        checked={draft.alternativeCollectEnabled}
                        onChange={(e) => setDraftField(schedule, 'alternativeCollectEnabled', e.target.checked)}
                      />
                      alternative_collect_enabled
                    </label>
                    <label className="flex items-center gap-1.5 text-[13.5px]">
                      <input
                        type="checkbox"
                        checked={draft.alternativeAnalyzeEnabled}
                        onChange={(e) => setDraftField(schedule, 'alternativeAnalyzeEnabled', e.target.checked)}
                      />
                      alternative_analyze_enabled
                    </label>
                    <button
                      type="button"
                      onClick={() => void savePolicy(schedule, draft)}
                      disabled={busy}
                      className="text-[13px] font-semibold text-sky-deep disabled:opacity-50"
                    >
                      {'\uc815\ucc45 \uc800\uc7a5'}
                    </button>
                  </div>
                </div>

                <div className="rounded-xl bg-surface-2 p-4">
                  <div className="text-[12.5px] font-semibold text-muted">{'\uc2e4\ud589 \uc0c1\ud0dc'}</div>
                  <dl className="mt-2 space-y-1.5 text-[13px]">
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\uc0c1\ud0dc \uc9c4\ub2e8'}</dt>
                      <dd className="text-right font-medium">{health.detail}</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\ub9c8\uc9c0\ub9c9 \uc2e4\ud589'}</dt>
                      <dd className="font-medium">{fmtDateTime(schedule.last_run_at)}</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\uacb0\uacfc'}</dt>
                      <dd className="font-medium">{schedule.last_status ?? '-'}</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\ub2e4\uc74c \uc608\uc815'}</dt>
                      <dd className="font-medium">{fmtDateTime(schedule.next_run_at)}</dd>
                    </div>
                  </dl>
                  <button
                    type="button"
                    onClick={() => void trigger(schedule)}
                    disabled={busy}
                    className="brand-grad mt-4 w-full rounded-full py-2.5 text-[14px] font-bold text-white disabled:opacity-60"
                  >
                    {'\uc9c0\uae08 \uc2e4\ud589'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void dryRunSchedule(schedule)}
                    disabled={busy}
                    className="mt-2 w-full rounded-full border border-line py-2.5 text-[14px] font-bold text-navy-soft disabled:opacity-60"
                  >
                    {'\ubbf8\ub9ac \ud310\ub2e8'}
                  </button>
                  {dryRun && (
                    <dl className="mt-3 space-y-1.5 rounded-md bg-white/70 p-3 text-[12.5px]">
                      <div className="flex justify-between gap-4">
                        <dt className="text-muted">{'\ud310\ub2e8'}</dt>
                        <dd className="font-semibold">{dryRun.decision.action ?? '-'} / {dryRun.decision.reason ?? '-'}</dd>
                      </div>
                      <div className="flex justify-between gap-4">
                        <dt className="text-muted">{'\uc2e4\ud589 \uc5ec\ubd80'}</dt>
                        <dd className="font-semibold">{dryRun.would_fire ? 'fire' : 'skip'}</dd>
                      </div>
                      <div className="flex justify-between gap-4">
                        <dt className="text-muted">{'\ub2e4\uc74c \uc608\uc815'}</dt>
                        <dd className="font-semibold">{fmtDateTime(dryRun.next_run_at)}</dd>
                      </div>
                      <div className="flex justify-between gap-4">
                        <dt className="text-muted">backpressure</dt>
                        <dd className="font-semibold">{dryRun.backpressure.reason ?? 'ok'}</dd>
                      </div>
                    </dl>
                  )}
                </div>
              </div>

              <div className="mt-4">
                <div className="text-[12.5px] font-semibold text-muted">{'\ucd5c\uadfc \uc2e4\ud589 \uc774\ub825'}</div>
                {runWarning && (
                  <p className="mt-1.5 rounded-md border border-red/30 bg-red/10 px-3 py-2 text-[12.5px] font-semibold text-red">
                    {runWarning}
                  </p>
                )}
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-left text-[12.5px]">
                    <thead className="text-muted">
                      <tr>
                        <th className="py-1.5 pr-3 font-semibold">{'\uc2dc\uc791'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\ud2b8\ub9ac\uac70'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\ub300\uc0c1'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\uc0c1\ud0dc'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\ud310\ub2e8'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\uacb0\uacfc \uc694\uc57d'}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.length === 0 ? (
                        <tr>
                          <td className="py-2 text-muted" colSpan={6}>
                            {'\uc2e4\ud589 \uc774\ub825\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.'}
                          </td>
                        </tr>
                      ) : (
                        runs.map((run) => (
                          <tr key={run.id} className="border-t border-line">
                            <td className="py-1.5 pr-3">{fmtDateTime(run.started_at)}</td>
                            <td className="py-1.5 pr-3">{run.trigger_reason ?? '-'}</td>
                            <td className="py-1.5 pr-3">{run.targets.join(', ') || '-'}</td>
                            <td className="py-1.5 pr-3 font-medium">{run.status ?? '-'}</td>
                            <td className="py-1.5 pr-3">{formatScheduleRunDecision(run)}</td>
                            <td className="max-w-[260px] py-1.5 pr-3 text-muted">{formatScheduleRunTargetResult(run)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
