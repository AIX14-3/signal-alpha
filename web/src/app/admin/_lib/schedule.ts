// 수집 스케줄 카드의 순수 로직 — draft 변환·실행이력 요약·헬스 톤·검증(컴포넌트 비의존).

import type { AdminSchedule, AdminScheduleRun } from "@/lib/apiClient";

export const ALL_TARGETS = ["price", "dart", "report", "alternative"] as const;
export const TARGET_LABEL: Record<string, string> = {
  price: '\uac00\uaca9',
  dart: 'DART',
  report: '\ub9ac\ud3ec\ud2b8',
  alternative: '\ub300\uccb4 \ub370\uc774\ud130',
};

export type ScheduleDraft = {
  runAt: string;
  targets: string[];
  frequencyMinutes: string;
  activeFrom: string;
  activeUntil: string;
  reportLimit: string;
  reportDaysBack: string;
  reportMaxPages: string;
  alternativeCollectEnabled: boolean;
  alternativeAnalyzeEnabled: boolean;
  alternativeCollectTimeoutSeconds: string;
  alternativeAnalyzeTimeoutSeconds: string;
  backpressureMaxWaiting: string;
  backpressureMaxFailed: string;
};

export const DEFAULT_SCHEDULE_DRAFT: ScheduleDraft = {
  runAt: '04:30',
  targets: [],
  frequencyMinutes: '1440',
  activeFrom: '04:30',
  activeUntil: '04:30',
  reportLimit: '100',
  reportDaysBack: '7',
  reportMaxPages: '20',
  alternativeCollectEnabled: true,
  alternativeAnalyzeEnabled: true,
  alternativeCollectTimeoutSeconds: '3600',
  alternativeAnalyzeTimeoutSeconds: '3600',
  backpressureMaxWaiting: '',
  backpressureMaxFailed: '',
};

export function draftFromSchedule(schedule: AdminSchedule): ScheduleDraft {
  const activeFrom = schedule.active_from_local ?? schedule.run_at_local ?? '04:30';
  return {
    runAt: schedule.run_at_local ?? '04:30',
    targets: schedule.targets ?? [],
    frequencyMinutes: String(schedule.frequency_minutes ?? 1440),
    activeFrom,
    activeUntil: schedule.active_until_local ?? activeFrom,
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
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function stringifyRunValue(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (Array.isArray(value)) return `${value.length} items`;
  const record = asRecord(value);
  if (!record) return '-';
  const entries = Object.entries(record);
  if (entries.length === 0) return 'empty';
  return entries
    .slice(0, 3)
    .map(([key, item]) => `${key}=${stringifyRunValue(item)}`)
    .join(', ');
}

const SCHEDULE_DECISION_ACTION_LABEL: Record<string, string> = {
  fire: '실행',
  skip: '보류',
};

const SCHEDULE_DECISION_REASON_LABEL: Record<string, string> = {
  manual: '수동 실행',
  scheduled: '정기 실행',
  'queue-backlog': '큐 적체',
  'recent-failures': '실패 누적',
  'outside-window': '운영 시간 외',
  'not-due': '대기',
  disabled: '비활성',
};

export function formatScheduleRunDecision(run: AdminScheduleRun): string {
  const detail = asRecord(run.detail);
  const decision = asRecord(detail?.decision);
  const action = typeof decision?.action === 'string' ? decision.action : null;
  const reason = typeof decision?.reason === 'string' ? decision.reason : null;
  const reasonValue = reason ?? run.trigger_reason;
  const reasonLabel = reasonValue
    ? (SCHEDULE_DECISION_REASON_LABEL[reasonValue] ?? reasonValue)
    : '-';
  if (action) return `${SCHEDULE_DECISION_ACTION_LABEL[action] ?? action}: ${reasonLabel}`;
  return reasonLabel;
}

export function formatScheduleRunTargetResult(run: AdminScheduleRun): string {
  const detail = asRecord(run.detail);
  const targetSummary = asRecord(detail?.targets);
  if (targetSummary) {
    const entries = Object.entries(targetSummary);
    if (entries.length === 0) return 'empty';
    return entries
      .map(([target, value]) => `${target}: ${stringifyRunValue(value)}`)
      .join(' | ');
  }
  return run.targets.join(', ') || '-';
}

const SCHEDULE_HEALTH_TONE: Record<string, string> = {
  ok: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  disabled: 'border-line bg-surface-2 text-muted',
  delayed: 'border-red/30 bg-red/10 text-red',
  failed_waiting: 'border-amber-200 bg-amber-50 text-amber-700',
  unknown: 'border-sky/30 bg-sky/10 text-sky-deep',
};

export function formatScheduleHealth(schedule: AdminSchedule): {
  label: string;
  detail: string;
  className: string;
} {
  const status = schedule.health_status ?? (schedule.enabled ? 'unknown' : 'disabled');
  return {
    label: schedule.health_label ?? status,
    detail: schedule.health_detail ?? '스케줄러 상태 확인이 필요합니다.',
    className: SCHEDULE_HEALTH_TONE[status] ?? SCHEDULE_HEALTH_TONE.unknown,
  };
}

function scheduleRunDecisionReason(run: AdminScheduleRun): string | null {
  const detail = asRecord(run.detail);
  const decision = asRecord(detail?.decision);
  if (typeof decision?.reason === 'string') return decision.reason;
  return run.trigger_reason;
}

export function getScheduleRunWarning(runs: AdminScheduleRun[]): string | null {
  let consecutive = 0;
  for (const run of runs) {
    const status = (run.status ?? '').toLowerCase();
    const reason = scheduleRunDecisionReason(run);
    const policySkip = status === 'skipped' && (
      reason === 'queue-backlog' || reason === 'recent-failures'
    );
    const failedRun = status === 'failed' || status === 'partial';
    if (!policySkip && !failedRun) break;
    consecutive += 1;
  }
  if (consecutive < 2) return null;
  return `반복 보류/실패 ${consecutive}회가 이어졌습니다. 큐 상태와 최근 실행 결과를 추가 확인하세요.`;
}

export function parseOptionalNumber(value: string): number | undefined {
  const trimmed = value.trim();
  return trimmed === '' ? undefined : Number(trimmed);
}

function validateNumberRange(
  value: string,
  label: string,
  min: number,
  max: number,
  optional = false,
): string | null {
  if (optional && value.trim() === '') return null;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    return `${label} 값은 ${min}~${max} 사이의 정수로 입력하세요.`;
  }
  return null;
}

export function validateScheduleDraft(draft: ScheduleDraft): string | null {
  if (draft.targets.length === 0) {
    return '수집 대상을 최소 1개 선택하세요.';
  }
  const frequencyMinutes = Number(draft.frequencyMinutes);
  if (!Number.isInteger(frequencyMinutes) || frequencyMinutes < 1 || frequencyMinutes > 1440) {
    return '반복 주기는 1~1440분 사이의 정수로 입력하세요.';
  }
  if (frequencyMinutes < 1440 && draft.activeFrom === draft.activeUntil) {
    return '반복 스케줄의 활성 시작/종료 시각은 같을 수 없습니다.';
  }
  return (
    validateNumberRange(draft.reportLimit, 'report_limit', 1, 1000) ??
    validateNumberRange(draft.reportDaysBack, 'report_days_back', 1, 400) ??
    validateNumberRange(draft.reportMaxPages, 'report_max_pages', 1, 200) ??
    validateNumberRange(draft.alternativeCollectTimeoutSeconds, 'alternative_collect_timeout_seconds', 60, 86400) ??
    validateNumberRange(draft.alternativeAnalyzeTimeoutSeconds, 'alternative_analyze_timeout_seconds', 60, 86400) ??
    validateNumberRange(draft.backpressureMaxWaiting, 'backpressure_max_waiting', 0, 1000000, true) ??
    validateNumberRange(draft.backpressureMaxFailed, 'backpressure_max_failed', 0, 1000000, true)
  );
}
