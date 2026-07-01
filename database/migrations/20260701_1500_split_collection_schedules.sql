-- 20260701_1500_split_collection_schedules.sql
-- target: backend
-- ============================================================================
-- Split the MVP scheduler control row into source-specific schedule rows.
-- ============================================================================

UPDATE public.collection_schedules
SET enabled = false,
    updated_by = COALESCE(updated_by, 'migration:split-source-schedules'),
    updated_at = NOW()
WHERE name = 'daily-collection';

INSERT INTO public.collection_schedules (
    name,
    enabled,
    run_at_local,
    timezone,
    targets,
    dart_limit,
    price_modes,
    updated_by
)
VALUES
    (
        'price-collection',
        true,
        '09:05',
        'Asia/Seoul',
        '["price"]'::jsonb,
        10,
        '["flows","snapshot"]'::jsonb,
        'migration:split-source-schedules'
    ),
    (
        'dart-collection',
        true,
        '08:30',
        'Asia/Seoul',
        '["dart"]'::jsonb,
        100,
        '["snapshot"]'::jsonb,
        'migration:split-source-schedules'
    ),
    (
        'report-collection',
        true,
        '06:00',
        'Asia/Seoul',
        '["report"]'::jsonb,
        10,
        '["snapshot"]'::jsonb,
        'migration:split-source-schedules'
    ),
    (
        'alternative-collection',
        true,
        '05:30',
        'Asia/Seoul',
        '["alternative"]'::jsonb,
        10,
        '["snapshot"]'::jsonb,
        'migration:split-source-schedules'
    )
ON CONFLICT (name) DO UPDATE
SET enabled = EXCLUDED.enabled,
    run_at_local = EXCLUDED.run_at_local,
    timezone = EXCLUDED.timezone,
    targets = EXCLUDED.targets,
    dart_limit = EXCLUDED.dart_limit,
    price_modes = EXCLUDED.price_modes,
    updated_by = EXCLUDED.updated_by,
    updated_at = NOW();
