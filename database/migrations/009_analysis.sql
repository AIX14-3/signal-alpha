-- 009_analysis.sql
-- Zone E (Analysis): 분석 요청 → 결과 → 점수 → 최종 시그널 파이프라인.
-- 분석 테이블의 FK는 NO ACTION(기본) — 분석 이력은 원본 삭제로 연쇄 삭제하지 않는다.

CREATE TABLE analysis_requests (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    analysis_mode VARCHAR(20) NOT NULL DEFAULT 'full'
        CHECK (analysis_mode IN ('full', 'dart_only', 'quick')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    ip_address INET
);

CREATE TABLE analysis_results (
    id BIGSERIAL PRIMARY KEY,
    request_id BIGINT REFERENCES analysis_requests(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    analysis_date DATE NOT NULL,
    run_key VARCHAR(30) NOT NULL DEFAULT 'BATCH',
    source_signal_event_ids BIGINT[] NOT NULL,
    base_score NUMERIC(5,2) NOT NULL CHECK (base_score BETWEEN 0 AND 100),
    pre_xgb_score NUMERIC(5,2),
    xgb_adj NUMERIC(5,2),
    analysis_mode VARCHAR(20) NOT NULL DEFAULT 'full'
        CHECK (analysis_mode IN ('full', 'dart_only', 'quick')),
    warning TEXT,
    disclaimer TEXT NOT NULL DEFAULT
        '본 서비스가 제공하는 시그널은 AI 에이전트의 데이터 분석 결과일 뿐, 투자 권유가 아니며 투자 손실에 대한 책임은 사용자에게 있습니다.',
    version VARCHAR(20) NOT NULL DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_analysis UNIQUE (
        stock_id,
        analysis_date,
        analysis_mode,
        run_key,
        version
    )
);

CREATE INDEX idx_analysis_stock_date
    ON analysis_results (stock_id, analysis_date DESC);

CREATE INDEX idx_analysis_run_key
    ON analysis_results (stock_id, analysis_date DESC, run_key);

CREATE INDEX idx_analysis_signal_events
    ON analysis_results USING GIN (source_signal_event_ids);

CREATE TABLE quant_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    score_breakdown JSONB NOT NULL,
    overall_score NUMERIC(5,2) NOT NULL,
    available_sources TEXT[] NOT NULL,
    missing_sources TEXT[],
    source_agreement VARCHAR(10) NOT NULL
        CHECK (source_agreement IN ('HIGH', 'MEDIUM', 'LOW')),
    failed_agent_count SMALLINT NOT NULL DEFAULT 0,
    alert_level SMALLINT NOT NULL DEFAULT 0
        CHECK (alert_level IN (0, 1, 2, 3)),
    score_cap_applied BOOLEAN NOT NULL DEFAULT FALSE,
    score_cap_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ta_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    ta_score NUMERIC(5,2),
    ta_detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ai_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    dart_agent_score NUMERIC(5,2),
    report_agent_score NUMERIC(5,2),
    alt_agent_score NUMERIC(5,2),
    dart_confidence NUMERIC(5,2),
    report_confidence NUMERIC(5,2),
    alt_confidence NUMERIC(5,2),
    validation_log JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE agent_results (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    debate_method VARCHAR(5) NOT NULL
        CHECK (debate_method IN ('D-1', 'D-2', 'D-3', 'D-4', 'D-5')),
    source_signal_event_ids BIGINT[],
    method_score NUMERIC(5,2) NOT NULL CHECK (method_score BETWEEN 0 AND 100),
    method_signal VARCHAR(10) NOT NULL
        CHECK (method_signal IN ('positive', 'negative', 'neutral', 'mixed')),
    method_detail JSONB NOT NULL,
    reliability_score NUMERIC(5,2),
    evidence_quality NUMERIC(5,2),
    llm_model VARCHAR(50),
    prompt_ver VARCHAR(20) DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_agent_result_method UNIQUE (result_id, debate_method)
);

CREATE INDEX idx_agent_result_id
    ON agent_results (result_id);

CREATE INDEX idx_agent_method
    ON agent_results (result_id, debate_method);

CREATE INDEX idx_agent_source_signal_event_ids
    ON agent_results USING GIN (source_signal_event_ids);

CREATE TABLE xgb_model_versions (
    id BIGSERIAL PRIMARY KEY,
    model_version VARCHAR(20) NOT NULL UNIQUE,
    trained_at TIMESTAMPTZ,
    feature_names JSONB,
    feature_importance JSONB,
    validation_score NUMERIC(5,2),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    training_samples INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_xgb_active
    ON xgb_model_versions (is_active)
    WHERE is_active = TRUE;

CREATE TABLE ml_scores (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL UNIQUE REFERENCES analysis_results(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    model_version_id BIGINT REFERENCES xgb_model_versions(id),
    ml_score NUMERIC(5,2) NOT NULL,
    calibrated_score NUMERIC(5,2) NOT NULL CHECK (calibrated_score BETWEEN 0 AND 100),
    prediction_label VARCHAR(20),
    feature_importance JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE final_signals (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    analysis_result_id BIGINT NOT NULL REFERENCES analysis_results(id),
    signal_date DATE NOT NULL,
    run_key VARCHAR(30) NOT NULL DEFAULT 'BATCH',
    version VARCHAR(20) NOT NULL DEFAULT '1.0',
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    final_score NUMERIC(5,2) NOT NULL CHECK (final_score BETWEEN 0 AND 100),
    confidence NUMERIC(5,2) NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    signal VARCHAR(10) NOT NULL
        CHECK (signal IN ('positive', 'negative', 'neutral', 'mixed')),
    source_agreement VARCHAR(10) NOT NULL
        CHECK (source_agreement IN ('HIGH', 'MEDIUM', 'LOW')),
    warning_level VARCHAR(10) NOT NULL DEFAULT 'NORMAL'
        CHECK (warning_level IN ('NORMAL', 'CAUTION', 'WARNING')),
    score_breakdown JSONB NOT NULL,
    summary TEXT NOT NULL,
    bull_point TEXT,
    bear_point TEXT,
    disclaimer TEXT NOT NULL DEFAULT
        '본 서비스가 제공하는 시그널은 AI 에이전트의 데이터 분석 결과일 뿐, 투자 권유가 아니며 투자 손실에 대한 책임은 사용자에게 있습니다.',
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    min_plan_required VARCHAR(20) NOT NULL DEFAULT 'free'
        CHECK (min_plan_required IN ('free', 'pro', 'premium')),
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_final_signal_version UNIQUE (
        stock_id,
        signal_date,
        run_key,
        version
    ),
    CONSTRAINT chk_final_signal_publish_time
        CHECK (
            is_published = FALSE
            OR published_at IS NOT NULL
        )
);

CREATE UNIQUE INDEX uq_final_signal_current
    ON final_signals (stock_id, signal_date, run_key)
    WHERE is_current = TRUE;

CREATE INDEX idx_final_stock_date
    ON final_signals (stock_id, signal_date DESC);

CREATE INDEX idx_final_run_key
    ON final_signals (stock_id, signal_date DESC, run_key);

CREATE INDEX idx_final_published
    ON final_signals (is_published, published_at DESC)
    WHERE is_published = TRUE;

CREATE TABLE score_history (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    final_signal_id BIGINT REFERENCES final_signals(id),
    analysis_result_id BIGINT REFERENCES analysis_results(id),
    signal_date DATE NOT NULL,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    final_score NUMERIC(5,2) NOT NULL,
    pre_xgb_score NUMERIC(5,2),
    reliability_score NUMERIC(5,2),
    model_version VARCHAR(20),
    scoring_version VARCHAR(20),
    reanalysis_reason VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_score_history_reference
        CHECK (
            final_signal_id IS NOT NULL
            OR analysis_result_id IS NOT NULL
        )
);

CREATE INDEX idx_score_history_stock
    ON score_history (stock_id, signal_date DESC, scored_at DESC);

CREATE INDEX idx_score_history_final_signal
    ON score_history (final_signal_id)
    WHERE final_signal_id IS NOT NULL;

CREATE TABLE backtest_results (
    id BIGSERIAL PRIMARY KEY,
    final_signal_id BIGINT NOT NULL REFERENCES final_signals(id),
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    signal_date DATE NOT NULL,
    signal_score NUMERIC(5,2) NOT NULL,
    signal_value VARCHAR(10) NOT NULL,
    price_at_signal NUMERIC(12,2) NOT NULL,
    price_after_5d NUMERIC(12,2),
    change_pct_5d NUMERIC(6,2),
    checked_at TIMESTAMPTZ,
    is_hit BOOLEAN,
    confidence_band VARCHAR(15),
    source_agreement VARCHAR(10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
