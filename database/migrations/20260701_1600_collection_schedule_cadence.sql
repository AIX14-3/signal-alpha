-- 20260701_1600_collection_schedule_cadence.sql
-- target: backend
-- ============================================================================
-- Add repeat cadence and active local windows to source-specific scheduler rows.
-- ============================================================================

ALTER TABLE public.collection_schedules
    ADD COLUMN frequency_minutes integer NOT NULL DEFAULT 1440,
    ADD COLUMN active_from_local time,
    ADD COLUMN active_until_local time,
    ADD CONSTRAINT collection_schedules_frequency_minutes_check
        CHECK (frequency_minutes BETWEEN 1 AND 1440);

UPDATE public.collection_schedules
SET frequency_minutes = 60,
    active_from_local = '09:05',
    active_until_local = '15:30',
    updated_by = 'migration:collection-schedule-cadence',
    updated_at = NOW()
WHERE name = 'price-collection';

UPDATE public.collection_schedules
SET frequency_minutes = 60,
    active_from_local = '08:30',
    active_until_local = '20:30',
    updated_by = 'migration:collection-schedule-cadence',
    updated_at = NOW()
WHERE name = 'dart-collection';

UPDATE public.collection_schedules
SET frequency_minutes = 720,
    active_from_local = '06:00',
    active_until_local = '18:00',
    updated_by = 'migration:collection-schedule-cadence',
    updated_at = NOW()
WHERE name = 'report-collection';

UPDATE public.collection_schedules
SET frequency_minutes = 720,
    active_from_local = '05:30',
    active_until_local = '17:30',
    updated_by = 'migration:collection-schedule-cadence',
    updated_at = NOW()
WHERE name = 'alternative-collection';
