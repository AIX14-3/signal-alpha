--
-- PostgreSQL database dump
--

\restrict ryuiOmqpigFZpPKVMTsp1VVefGCZF8o2ajyuhMq0ZDJV1BMsjfnbuCgYaUKeiYe

-- Dumped from database version 16.14 (Debian 16.14-1.pgdg12+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: api; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA api;


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: hiring_crawler_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.hiring_crawler_type AS ENUM (
    'portal_saramin',
    'portal_jobkorea',
    'official_api',
    'official_selenium',
    'recruiter_kr',
    'simple_site'
);


--
-- Name: set_final_signal_current(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_final_signal_current() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.is_current = TRUE THEN
        PERFORM pg_advisory_xact_lock(
            hashtext(NEW.stock_id::text || '|' || NEW.signal_date::text || '|' || NEW.run_key)
        );

        UPDATE final_signals
        SET is_current = FALSE
        WHERE stock_id = NEW.stock_id
          AND signal_date = NEW.signal_date
          AND run_key = NEW.run_key
          AND is_current = TRUE
          AND id IS DISTINCT FROM NEW.id
          AND version IS DISTINCT FROM NEW.version;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: update_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: processing_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processing_queue (
    id bigint NOT NULL,
    stock_id bigint,
    task_type character varying(50) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    priority character varying(10) DEFAULT 'batch'::character varying NOT NULL,
    source_raw_ids bigint[],
    source_signal_event_ids bigint[],
    source_analysis_result_ids bigint[],
    task_context jsonb,
    retry_count smallint DEFAULT 0 NOT NULL,
    max_retry_count smallint DEFAULT 3 NOT NULL,
    error_message text,
    scheduled_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT processing_queue_priority_check CHECK (((priority)::text = ANY (ARRAY[('immediate'::character varying)::text, ('batch'::character varying)::text]))),
    CONSTRAINT processing_queue_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('running'::character varying)::text, ('success'::character varying)::text, ('failed'::character varying)::text, ('retrying'::character varying)::text, ('skipped'::character varying)::text])))
);


--
-- Name: stocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stocks (
    id bigint NOT NULL,
    ticker character varying(10) NOT NULL,
    name character varying(100) NOT NULL,
    market character varying(10) NOT NULL,
    sector character varying(100),
    is_active boolean DEFAULT true NOT NULL,
    is_target boolean DEFAULT false NOT NULL,
    short_name character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT stocks_market_check CHECK (((market)::text = ANY (ARRAY[('KOSPI'::character varying)::text, ('KOSDAQ'::character varying)::text])))
);


--
-- Name: analysis_pipeline_status; Type: VIEW; Schema: api; Owner: -
--

CREATE VIEW api.analysis_pipeline_status AS
 SELECT processing_queue.id,
    processing_queue.stock_id,
    processing_queue.task_type,
    processing_queue.status,
    processing_queue.created_at,
    processing_queue.updated_at,
    stocks.ticker AS stock_code,
    stocks.name AS stock_name
   FROM (public.processing_queue
     JOIN public.stocks ON ((stocks.id = processing_queue.stock_id)));


--
-- Name: agent_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_results (
    id bigint NOT NULL,
    result_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    debate_method character varying(5) NOT NULL,
    source_signal_event_ids bigint[],
    method_score numeric(5,2) NOT NULL,
    method_signal character varying(10) NOT NULL,
    method_detail jsonb NOT NULL,
    reliability_score numeric(5,2),
    evidence_quality numeric(5,2),
    llm_model character varying(50),
    prompt_ver character varying(20) DEFAULT '1.0'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_results_debate_method_check CHECK (((debate_method)::text = ANY (ARRAY[('D-1'::character varying)::text, ('D-2'::character varying)::text, ('D-3'::character varying)::text, ('D-4'::character varying)::text, ('D-5'::character varying)::text]))),
    CONSTRAINT agent_results_method_score_check CHECK (((method_score >= (0)::numeric) AND (method_score <= (100)::numeric))),
    CONSTRAINT agent_results_method_signal_check CHECK (((method_signal)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying, 'neutral'::character varying, 'mixed'::character varying, 'unknown'::character varying])::text[])))
);


--
-- Name: analysis_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_results (
    id bigint NOT NULL,
    request_id bigint,
    stock_id bigint NOT NULL,
    analysis_date date NOT NULL,
    run_key character varying(30) DEFAULT 'BATCH'::character varying NOT NULL,
    source_signal_event_ids bigint[] NOT NULL,
    base_score numeric(5,2) NOT NULL,
    pre_xgb_score numeric(5,2),
    xgb_adj numeric(5,2),
    analysis_mode character varying(20) DEFAULT 'full'::character varying NOT NULL,
    warning text,
    disclaimer text DEFAULT '본 서비스가 제공하는 시그널은 AI 에이전트의 데이터 분석 결과일 뿐, 투자 권유가 아니며 투자 손실에 대한 책임은 사용자에게 있습니다.'::text NOT NULL,
    version character varying(20) DEFAULT '1.0'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT analysis_results_analysis_mode_check CHECK (((analysis_mode)::text = ANY (ARRAY[('full'::character varying)::text, ('dart_only'::character varying)::text, ('quick'::character varying)::text]))),
    CONSTRAINT analysis_results_base_score_check CHECK (((base_score >= (0)::numeric) AND (base_score <= (100)::numeric)))
);


--
-- Name: final_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.final_signals (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    analysis_result_id bigint NOT NULL,
    signal_date date NOT NULL,
    run_key character varying(30) DEFAULT 'BATCH'::character varying NOT NULL,
    version character varying(20) DEFAULT '1.0'::character varying NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    final_score numeric(5,2) NOT NULL,
    confidence numeric(5,2) NOT NULL,
    signal character varying(10) NOT NULL,
    source_agreement character varying(10) NOT NULL,
    warning_level character varying(10) DEFAULT 'NORMAL'::character varying NOT NULL,
    score_breakdown jsonb NOT NULL,
    summary text NOT NULL,
    bull_point text,
    bear_point text,
    disclaimer text DEFAULT '본 서비스가 제공하는 시그널은 AI 에이전트의 데이터 분석 결과일 뿐, 투자 권유가 아니며 투자 손실에 대한 책임은 사용자에게 있습니다.'::text NOT NULL,
    needs_review boolean DEFAULT false NOT NULL,
    min_plan_required character varying(20) DEFAULT 'free'::character varying NOT NULL,
    is_published boolean DEFAULT false NOT NULL,
    published_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    consensus_score numeric(5,2),
    positive_evidence jsonb,
    caution_evidence jsonb,
    ml_final_score double precision,
    ml_direction character varying(16),
    ml_confidence double precision,
    source_predictions jsonb,
    CONSTRAINT chk_final_signal_ml_direction CHECK (((ml_direction IS NULL) OR ((ml_direction)::text = ANY (ARRAY[('positive'::character varying)::text, ('negative'::character varying)::text, ('neutral'::character varying)::text, ('unknown'::character varying)::text])))),
    CONSTRAINT chk_final_signal_publish_time CHECK (((is_published = false) OR (published_at IS NOT NULL))),
    CONSTRAINT final_signals_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (100)::numeric))),
    CONSTRAINT final_signals_consensus_score_check CHECK (((consensus_score >= (0)::numeric) AND (consensus_score <= (100)::numeric))),
    CONSTRAINT final_signals_final_score_check CHECK (((final_score >= (0)::numeric) AND (final_score <= (100)::numeric))),
    CONSTRAINT final_signals_min_plan_required_check CHECK (((min_plan_required)::text = ANY (ARRAY[('free'::character varying)::text, ('pro'::character varying)::text, ('premium'::character varying)::text]))),
    CONSTRAINT final_signals_signal_check CHECK (((signal)::text = ANY (ARRAY[('positive'::character varying)::text, ('negative'::character varying)::text, ('neutral'::character varying)::text, ('mixed'::character varying)::text]))),
    CONSTRAINT final_signals_source_agreement_check CHECK (((source_agreement)::text = ANY (ARRAY[('HIGH'::character varying)::text, ('MEDIUM'::character varying)::text, ('LOW'::character varying)::text]))),
    CONSTRAINT final_signals_warning_level_check CHECK (((warning_level)::text = ANY (ARRAY[('NORMAL'::character varying)::text, ('CAUTION'::character varying)::text, ('WARNING'::character varying)::text])))
);


--
-- Name: signal_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_events (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    source_document_id bigint NOT NULL,
    event_hash character varying(64) NOT NULL,
    source_type character varying(20) NOT NULL,
    event_type character varying(50) NOT NULL,
    event_date date NOT NULL,
    signal_direction character varying(10) NOT NULL,
    impact_level character varying(10) NOT NULL,
    title text NOT NULL,
    summary text,
    evidence_text text,
    evidence_url text,
    needs_review boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT signal_events_impact_level_check CHECK (((impact_level)::text = ANY (ARRAY[('high'::character varying)::text, ('medium'::character varying)::text, ('low'::character varying)::text]))),
    CONSTRAINT signal_events_signal_direction_check CHECK (((signal_direction)::text = ANY (ARRAY[('positive'::character varying)::text, ('negative'::character varying)::text, ('neutral'::character varying)::text, ('mixed'::character varying)::text, ('unknown'::character varying)::text]))),
    CONSTRAINT signal_events_source_type_check CHECK (((source_type)::text = ANY (ARRAY[('DART'::character varying)::text, ('REPORT'::character varying)::text, ('HIRING'::character varying)::text, ('PATENT'::character varying)::text, ('DATALAB'::character varying)::text])))
);


--
-- Name: source_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_documents (
    id bigint NOT NULL,
    raw_document_id bigint,
    stock_id bigint NOT NULL,
    source_type character varying(20) NOT NULL,
    source_name character varying(100) NOT NULL,
    title text NOT NULL,
    source_url text,
    published_at timestamp with time zone NOT NULL,
    collected_at timestamp with time zone NOT NULL,
    reliability_level character varying(10) DEFAULT 'medium'::character varying NOT NULL,
    is_official boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    external_ref_type character varying(40),
    external_ref_id bigint,
    CONSTRAINT chk_source_doc_anchor CHECK ((((raw_document_id IS NOT NULL) AND (external_ref_type IS NULL) AND (external_ref_id IS NULL)) OR ((raw_document_id IS NULL) AND (external_ref_type IS NOT NULL) AND (external_ref_id IS NOT NULL)))),
    CONSTRAINT source_documents_reliability_level_check CHECK (((reliability_level)::text = ANY (ARRAY[('high'::character varying)::text, ('medium'::character varying)::text, ('low'::character varying)::text]))),
    CONSTRAINT source_documents_source_type_check CHECK (((source_type)::text = ANY (ARRAY[('DART'::character varying)::text, ('REPORT'::character varying)::text, ('HIRING'::character varying)::text, ('PATENT'::character varying)::text, ('DATALAB'::character varying)::text])))
);


--
-- Name: signal_detail; Type: VIEW; Schema: api; Owner: -
--

CREATE VIEW api.signal_detail AS
 SELECT final_signals.id,
    final_signals.stock_id,
    final_signals.analysis_result_id,
    final_signals.signal_date,
    final_signals.run_key,
    final_signals.version,
    final_signals.is_current,
    final_signals.final_score,
    final_signals.confidence,
    final_signals.signal,
    final_signals.source_agreement,
    final_signals.warning_level,
    final_signals.score_breakdown,
    final_signals.summary,
    final_signals.bull_point,
    final_signals.bear_point,
    final_signals.disclaimer,
    final_signals.needs_review,
    final_signals.min_plan_required,
    final_signals.is_published,
    final_signals.published_at,
    final_signals.created_at,
    final_signals.consensus_score,
    final_signals.positive_evidence,
    final_signals.caution_evidence,
    final_signals.ml_final_score,
    final_signals.ml_direction,
    final_signals.ml_confidence,
    final_signals.source_predictions,
    stocks.ticker,
    stocks.name,
    stocks.market,
    stocks.sector,
    analysis_results.analysis_date,
    analysis_results.analysis_mode,
    analysis_results.run_key AS analysis_run_key,
    analysis_results.version AS analysis_version,
    analysis_results.base_score,
    analysis_results.warning AS analysis_warning,
    analysis_results.source_signal_event_ids,
    COALESCE(agent_results.items, '[]'::jsonb) AS agent_results,
    COALESCE(signal_events.items, '[]'::jsonb) AS signal_events
   FROM ((((public.final_signals
     JOIN public.stocks ON ((stocks.id = final_signals.stock_id)))
     JOIN public.analysis_results ON ((analysis_results.id = final_signals.analysis_result_id)))
     LEFT JOIN LATERAL ( SELECT jsonb_agg(jsonb_build_object('id', agent_results_1.id, 'debate_method', agent_results_1.debate_method, 'method_score', agent_results_1.method_score, 'method_signal', agent_results_1.method_signal, 'method_detail', agent_results_1.method_detail, 'source_signal_event_ids', agent_results_1.source_signal_event_ids, 'reliability_score', agent_results_1.reliability_score, 'evidence_quality', agent_results_1.evidence_quality, 'llm_model', agent_results_1.llm_model, 'prompt_ver', agent_results_1.prompt_ver, 'created_at', agent_results_1.created_at) ORDER BY agent_results_1.debate_method, agent_results_1.id) AS items
           FROM public.agent_results agent_results_1
          WHERE (agent_results_1.result_id = analysis_results.id)) agent_results ON (true))
     LEFT JOIN LATERAL ( SELECT jsonb_agg(jsonb_build_object('id', signal_events_1.id, 'source_document_id', signal_events_1.source_document_id, 'source_type', signal_events_1.source_type, 'event_type', signal_events_1.event_type, 'event_date', signal_events_1.event_date, 'signal_direction', signal_events_1.signal_direction, 'impact_level', signal_events_1.impact_level, 'title', signal_events_1.title, 'summary', signal_events_1.summary, 'evidence_url', signal_events_1.evidence_url, 'needs_review', signal_events_1.needs_review, 'source_name', source_documents.source_name, 'source_url', source_documents.source_url, 'is_official', source_documents.is_official) ORDER BY
                CASE signal_events_1.impact_level
                    WHEN 'high'::text THEN 0
                    WHEN 'medium'::text THEN 1
                    ELSE 2
                END, signal_events_1.event_date DESC, signal_events_1.id) AS items
           FROM (public.signal_events signal_events_1
             LEFT JOIN public.source_documents ON ((source_documents.id = signal_events_1.source_document_id)))
          WHERE (signal_events_1.id = ANY (analysis_results.source_signal_event_ids))) signal_events ON (true))
  WHERE ((final_signals.is_current = true) AND (final_signals.is_published = true));


--
-- Name: signals_current; Type: VIEW; Schema: api; Owner: -
--

CREATE VIEW api.signals_current AS
 SELECT final_signals.id,
    final_signals.stock_id,
    final_signals.analysis_result_id,
    final_signals.signal_date,
    final_signals.run_key,
    final_signals.version,
    final_signals.is_current,
    final_signals.final_score,
    final_signals.confidence,
    final_signals.signal,
    final_signals.source_agreement,
    final_signals.warning_level,
    final_signals.score_breakdown,
    final_signals.summary,
    final_signals.bull_point,
    final_signals.bear_point,
    final_signals.disclaimer,
    final_signals.needs_review,
    final_signals.min_plan_required,
    final_signals.is_published,
    final_signals.published_at,
    final_signals.created_at,
    final_signals.consensus_score,
    final_signals.positive_evidence,
    final_signals.caution_evidence,
    final_signals.ml_final_score,
    final_signals.ml_direction,
    final_signals.ml_confidence,
    final_signals.source_predictions,
    stocks.ticker,
    stocks.name,
    stocks.market,
    stocks.sector,
    analysis_results.analysis_mode,
    analysis_results.base_score,
    analysis_results.warning AS analysis_warning
   FROM ((public.final_signals
     JOIN public.stocks ON ((stocks.id = final_signals.stock_id)))
     JOIN public.analysis_results ON ((analysis_results.id = final_signals.analysis_result_id)))
  WHERE ((final_signals.is_current = true) AND (final_signals.is_published = true));


--
-- Name: stock_news; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_news (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    ticker character varying(10) NOT NULL,
    article_hash character varying(64) NOT NULL,
    title text NOT NULL,
    summary text,
    url text,
    press character varying(120),
    source character varying(20) DEFAULT 'NAVER_NEWS'::character varying NOT NULL,
    published_at timestamp with time zone,
    collected_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: stock_news; Type: VIEW; Schema: api; Owner: -
--

CREATE VIEW api.stock_news AS
 SELECT stock_id,
    ticker AS stock_code,
    title,
    summary,
    url,
    press,
    source,
    published_at,
    collected_at
   FROM public.stock_news;


--
-- Name: stock_news_digest; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_news_digest (
    stock_id bigint NOT NULL,
    ticker character varying(10) NOT NULL,
    digest_text text NOT NULL,
    model character varying(60) NOT NULL,
    prompt_version character varying(40) NOT NULL,
    article_count integer NOT NULL,
    source_hash character varying(64) NOT NULL,
    source_window_start timestamp with time zone,
    source_window_end timestamp with time zone,
    generated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: stock_news_digest; Type: VIEW; Schema: api; Owner: -
--

CREATE VIEW api.stock_news_digest AS
 SELECT ticker AS stock_code,
    digest_text,
    model,
    article_count,
    generated_at
   FROM public.stock_news_digest;


--
-- Name: stocks; Type: VIEW; Schema: api; Owner: -
--

CREATE VIEW api.stocks AS
 SELECT id,
    ticker,
    name,
    market,
    sector,
    is_active,
    created_at,
    updated_at
   FROM public.stocks;


--
-- Name: admin_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_accounts (
    id bigint NOT NULL,
    email character varying(255) NOT NULL,
    password_hash text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: admin_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_accounts_id_seq OWNED BY public.admin_accounts.id;


--
-- Name: admin_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_audit_log (
    id bigint NOT NULL,
    actor_admin_id bigint,
    action character varying(50) NOT NULL,
    target_type character varying(30) NOT NULL,
    target_id bigint,
    before jsonb,
    after jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: admin_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_audit_log_id_seq OWNED BY public.admin_audit_log.id;


--
-- Name: admin_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_sessions (
    id bigint NOT NULL,
    admin_id bigint NOT NULL,
    session_token character varying(255) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_activity_at timestamp with time zone,
    ip_address inet,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: admin_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_sessions_id_seq OWNED BY public.admin_sessions.id;


--
-- Name: agent_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_results_id_seq OWNED BY public.agent_results.id;


--
-- Name: ai_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_scores (
    id bigint NOT NULL,
    result_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    dart_agent_score numeric(5,2),
    report_agent_score numeric(5,2),
    alt_agent_score numeric(5,2),
    dart_confidence numeric(5,2),
    report_confidence numeric(5,2),
    alt_confidence numeric(5,2),
    validation_log jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ai_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ai_scores_id_seq OWNED BY public.ai_scores.id;


--
-- Name: analysis_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_requests (
    id bigint NOT NULL,
    user_id bigint,
    stock_id bigint NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    analysis_mode character varying(20) DEFAULT 'full'::character varying NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    error_message text,
    ip_address inet,
    CONSTRAINT analysis_requests_analysis_mode_check CHECK (((analysis_mode)::text = ANY (ARRAY[('full'::character varying)::text, ('dart_only'::character varying)::text, ('quick'::character varying)::text]))),
    CONSTRAINT analysis_requests_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('running'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: analysis_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analysis_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analysis_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analysis_requests_id_seq OWNED BY public.analysis_requests.id;


--
-- Name: analysis_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analysis_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analysis_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analysis_results_id_seq OWNED BY public.analysis_results.id;


--
-- Name: backtest_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_results (
    id bigint NOT NULL,
    final_signal_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    signal_date date NOT NULL,
    signal_score numeric(5,2) NOT NULL,
    signal_value character varying(10) NOT NULL,
    price_at_signal numeric(12,2) NOT NULL,
    price_after_5d numeric(12,2),
    change_pct_5d numeric(6,2),
    checked_at timestamp with time zone,
    is_hit boolean,
    confidence_band character varying(15),
    source_agreement character varying(10),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: backtest_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.backtest_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: backtest_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.backtest_results_id_seq OWNED BY public.backtest_results.id;


--
-- Name: collection_schedule_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_schedule_runs (
    id bigint NOT NULL,
    schedule_id bigint,
    schedule_name character varying(64) NOT NULL,
    trigger_reason text NOT NULL,
    targets jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    detail jsonb,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: collection_schedule_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_schedule_runs ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.collection_schedule_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collection_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_schedules (
    id bigint NOT NULL,
    name character varying(64) NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    run_at_local time without time zone DEFAULT '04:30:00'::time without time zone NOT NULL,
    timezone text DEFAULT 'Asia/Seoul'::text NOT NULL,
    targets jsonb DEFAULT '["price", "dart"]'::jsonb NOT NULL,
    dart_limit integer DEFAULT 10 NOT NULL,
    price_modes jsonb DEFAULT '["flows", "snapshot"]'::jsonb NOT NULL,
    last_run_at timestamp with time zone,
    last_status text,
    last_detail jsonb,
    next_run_at timestamp with time zone,
    manual_trigger_requested_at timestamp with time zone,
    updated_by text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    frequency_minutes integer DEFAULT 1440 NOT NULL,
    active_from_local time without time zone,
    active_until_local time without time zone,
    report_limit integer DEFAULT 100 NOT NULL,
    report_days_back integer DEFAULT 7 NOT NULL,
    report_max_pages integer DEFAULT 20 NOT NULL,
    alternative_collect_enabled boolean DEFAULT true NOT NULL,
    alternative_analyze_enabled boolean DEFAULT true NOT NULL,
    alternative_collect_timeout_seconds integer DEFAULT 3600 NOT NULL,
    alternative_analyze_timeout_seconds integer DEFAULT 3600 NOT NULL,
    backpressure_max_waiting integer,
    backpressure_max_failed integer,
    CONSTRAINT collection_schedules_alternative_analyze_timeout_check CHECK (((alternative_analyze_timeout_seconds >= 60) AND (alternative_analyze_timeout_seconds <= 86400))),
    CONSTRAINT collection_schedules_alternative_collect_timeout_check CHECK (((alternative_collect_timeout_seconds >= 60) AND (alternative_collect_timeout_seconds <= 86400))),
    CONSTRAINT collection_schedules_backpressure_max_failed_check CHECK (((backpressure_max_failed IS NULL) OR (backpressure_max_failed >= 0))),
    CONSTRAINT collection_schedules_backpressure_max_waiting_check CHECK (((backpressure_max_waiting IS NULL) OR (backpressure_max_waiting >= 0))),
    CONSTRAINT collection_schedules_frequency_minutes_check CHECK (((frequency_minutes >= 1) AND (frequency_minutes <= 1440))),
    CONSTRAINT collection_schedules_report_days_back_check CHECK (((report_days_back >= 1) AND (report_days_back <= 400))),
    CONSTRAINT collection_schedules_report_limit_check CHECK (((report_limit >= 1) AND (report_limit <= 1000))),
    CONSTRAINT collection_schedules_report_max_pages_check CHECK (((report_max_pages >= 1) AND (report_max_pages <= 200)))
);


--
-- Name: collection_schedules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_schedules ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.collection_schedules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collector_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collector_runs (
    id bigint NOT NULL,
    collector_type character varying(20) NOT NULL,
    run_mode character varying(20) NOT NULL,
    status character varying(20) DEFAULT 'running'::character varying NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    collected_count integer DEFAULT 0 NOT NULL,
    inserted_count integer DEFAULT 0 NOT NULL,
    skipped_count integer DEFAULT 0 NOT NULL,
    failed_count integer DEFAULT 0 NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collector_runs_collector_type_check CHECK (((collector_type)::text = ANY (ARRAY[('DART'::character varying)::text, ('REPORT'::character varying)::text, ('HIRING'::character varying)::text, ('PATENT'::character varying)::text, ('DATALAB'::character varying)::text, ('PRICE'::character varying)::text]))),
    CONSTRAINT collector_runs_run_mode_check CHECK (((run_mode)::text = ANY (ARRAY[('batch'::character varying)::text, ('immediate'::character varying)::text, ('manual'::character varying)::text]))),
    CONSTRAINT collector_runs_status_check CHECK (((status)::text = ANY (ARRAY[('running'::character varying)::text, ('success'::character varying)::text, ('partial'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: collector_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.collector_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: collector_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.collector_runs_id_seq OWNED BY public.collector_runs.id;


--
-- Name: community_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_comments (
    id bigint NOT NULL,
    post_id bigint NOT NULL,
    parent_comment_id bigint,
    author_user_id bigint NOT NULL,
    body text NOT NULL,
    status character varying(10) DEFAULT 'visible'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT chk_community_comment_status CHECK (((status)::text = ANY ((ARRAY['visible'::character varying, 'hidden'::character varying])::text[])))
);


--
-- Name: community_comments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.community_comments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.community_comments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: community_post_rankings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_post_rankings (
    post_id bigint NOT NULL,
    window_kind character varying(10) NOT NULL,
    score numeric(12,3) DEFAULT 0 NOT NULL,
    likes integer DEFAULT 0 NOT NULL,
    comments integer DEFAULT 0 NOT NULL,
    views integer DEFAULT 0 NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_community_ranking_window CHECK (((window_kind)::text = ANY ((ARRAY['weekly'::character varying, 'all'::character varying])::text[])))
);


--
-- Name: community_post_views; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_post_views (
    id bigint NOT NULL,
    post_id bigint NOT NULL,
    viewer_key character varying(64) NOT NULL,
    viewed_on date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: community_post_views_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.community_post_views ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.community_post_views_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: community_posts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_posts (
    id bigint NOT NULL,
    author_user_id bigint NOT NULL,
    journal_id bigint,
    title character varying(200) NOT NULL,
    body text DEFAULT ''::text NOT NULL,
    show_pnl boolean DEFAULT false NOT NULL,
    view_count integer DEFAULT 0 NOT NULL,
    status character varying(10) DEFAULT 'visible'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT chk_community_post_status CHECK (((status)::text = ANY ((ARRAY['visible'::character varying, 'hidden'::character varying])::text[])))
);


--
-- Name: community_posts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.community_posts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.community_posts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: community_reactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_reactions (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    target_type character varying(10) NOT NULL,
    target_id bigint NOT NULL,
    type character varying(10) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_community_reaction_target CHECK (((target_type)::text = ANY ((ARRAY['post'::character varying, 'comment'::character varying])::text[]))),
    CONSTRAINT chk_community_reaction_type CHECK (((type)::text = ANY ((ARRAY['like'::character varying, 'bookmark'::character varying])::text[])))
);


--
-- Name: community_reactions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.community_reactions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.community_reactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: community_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_reports (
    id bigint NOT NULL,
    reporter_user_id bigint NOT NULL,
    target_type character varying(10) NOT NULL,
    target_id bigint NOT NULL,
    reason character varying(30),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_community_report_target CHECK (((target_type)::text = ANY ((ARRAY['post'::character varying, 'comment'::character varying])::text[])))
);


--
-- Name: community_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.community_reports ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.community_reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: credit_trade_trend; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_trade_trend (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    trade_date date NOT NULL,
    cur_price bigint,
    volume bigint,
    credit_new bigint,
    credit_repay bigint,
    credit_balance bigint,
    credit_amount bigint,
    credit_net bigint,
    balance_ratio numeric(8,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: credit_trade_trend_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.credit_trade_trend_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: credit_trade_trend_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.credit_trade_trend_id_seq OWNED BY public.credit_trade_trend.id;


--
-- Name: dart_collection_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dart_collection_states (
    stock_id bigint NOT NULL,
    ticker character varying(10) NOT NULL,
    last_bgn_de date NOT NULL,
    last_end_de date NOT NULL,
    last_receipt_no character varying(30),
    last_collected_count integer DEFAULT 0 NOT NULL,
    last_collector_run_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dart_corp_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dart_corp_codes (
    id bigint NOT NULL,
    stock_id bigint,
    corp_code character varying(20) NOT NULL,
    ticker character varying(10) NOT NULL,
    corp_name character varying(200) NOT NULL,
    corp_name_eng character varying(200),
    stock_name character varying(200),
    is_active boolean DEFAULT true NOT NULL,
    synced_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dart_corp_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dart_corp_codes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dart_corp_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dart_corp_codes_id_seq OWNED BY public.dart_corp_codes.id;


--
-- Name: dart_employee_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dart_employee_stats (
    id bigint NOT NULL,
    stock_id bigint,
    corp_code character varying(20) NOT NULL,
    rcept_no character varying(30) NOT NULL,
    line_seq smallint DEFAULT 0 NOT NULL,
    bsns_year smallint NOT NULL,
    reprt_code character varying(5) NOT NULL,
    segment character varying(100),
    sex character varying(10),
    headcount integer,
    regular_count integer,
    contract_count integer,
    avg_tenure_years numeric(6,2),
    avg_salary_krw numeric(20,0),
    salary_total_krw numeric(20,0),
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dart_employee_stats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dart_employee_stats_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dart_employee_stats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dart_employee_stats_id_seq OWNED BY public.dart_employee_stats.id;


--
-- Name: dart_financial_facts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dart_financial_facts (
    id bigint NOT NULL,
    stock_id bigint,
    corp_code character varying(20) NOT NULL,
    rcept_no character varying(30) NOT NULL,
    bsns_year smallint NOT NULL,
    reprt_code character varying(5) NOT NULL,
    fs_div character varying(3) NOT NULL,
    sj_div character varying(5) NOT NULL,
    account_id character varying(100),
    account_nm character varying(200) NOT NULL,
    amount_krw numeric(24,0),
    amount_raw text,
    currency character varying(10) DEFAULT 'KRW'::character varying NOT NULL,
    period_label character varying(10) NOT NULL,
    fiscal_period character varying(10),
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dart_financial_facts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dart_financial_facts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dart_financial_facts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dart_financial_facts_id_seq OWNED BY public.dart_financial_facts.id;


--
-- Name: dart_ownership_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dart_ownership_events (
    id bigint NOT NULL,
    stock_id bigint,
    corp_code character varying(20) NOT NULL,
    rcept_no character varying(30) NOT NULL,
    line_seq smallint DEFAULT 0 NOT NULL,
    report_date date NOT NULL,
    holder_name character varying(200) NOT NULL,
    holder_type character varying(20) NOT NULL,
    shares numeric(20,0),
    ratio numeric(8,4),
    shares_delta numeric(20,0),
    ratio_delta numeric(8,4),
    report_reason character varying(100),
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    trade_type character varying(20),
    unit_price numeric(20,4),
    CONSTRAINT chk_dart_ownership_trade_type CHECK (((trade_type IS NULL) OR ((trade_type)::text = ANY ((ARRAY['onmarket_buy'::character varying, 'onmarket_sell'::character varying, 'gift'::character varying, 'gift_received'::character varying, 'inheritance'::character varying, 'stock_option'::character varying, 'appointment'::character varying, 'offmarket'::character varying, 'mixed'::character varying, 'other'::character varying])::text[]))))
);


--
-- Name: dart_ownership_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dart_ownership_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dart_ownership_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dart_ownership_events_id_seq OWNED BY public.dart_ownership_events.id;


--
-- Name: dart_raw_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dart_raw_details (
    raw_document_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    receipt_no character varying(30) NOT NULL,
    corp_code character varying(20),
    report_name text NOT NULL,
    disclosure_type character varying(50),
    priority character varying(10) DEFAULT 'batch'::character varying NOT NULL,
    priority_reason character varying(200),
    is_correction boolean DEFAULT false NOT NULL,
    original_receipt_no character varying(30),
    extra_payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dart_raw_details_priority_check CHECK (((priority)::text = ANY (ARRAY[('immediate'::character varying)::text, ('batch'::character varying)::text])))
);


--
-- Name: datalab_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datalab_categories (
    id bigint NOT NULL,
    name character varying(100) NOT NULL,
    sector character varying(100),
    description text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: datalab_categories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.datalab_categories_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: datalab_categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.datalab_categories_id_seq OWNED BY public.datalab_categories.id;


--
-- Name: datalab_category_keywords; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datalab_category_keywords (
    category_id bigint NOT NULL,
    keyword character varying(200) NOT NULL,
    keyword_group character varying(100) NOT NULL,
    source character varying(50) DEFAULT 'reviewed'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    polarity character varying(10) DEFAULT 'demand'::character varying NOT NULL,
    polarity_source character varying(10) DEFAULT 'default'::character varying NOT NULL,
    polarity_confidence numeric(4,3),
    polarity_model character varying(50),
    polarity_rationale text,
    polarity_classified_at timestamp with time zone,
    review_status character varying(10) DEFAULT 'approved'::character varying NOT NULL,
    validation_active_days integer,
    validation_window_days integer,
    validation_coverage numeric(4,3),
    validated_at timestamp with time zone,
    CONSTRAINT chk_datalab_keyword_polarity_confidence CHECK (((polarity_confidence IS NULL) OR ((polarity_confidence >= (0)::numeric) AND (polarity_confidence <= (1)::numeric)))),
    CONSTRAINT chk_datalab_keyword_polarity_source CHECK (((polarity_source)::text = ANY (ARRAY[('manual'::character varying)::text, ('llm'::character varying)::text, ('default'::character varying)::text]))),
    CONSTRAINT chk_datalab_keyword_review_status CHECK (((review_status)::text = ANY (ARRAY[('approved'::character varying)::text, ('pending'::character varying)::text, ('rejected'::character varying)::text]))),
    CONSTRAINT chk_datalab_keyword_validation_active_days CHECK (((validation_active_days IS NULL) OR (validation_active_days >= 0))),
    CONSTRAINT chk_datalab_keyword_validation_coverage CHECK (((validation_coverage IS NULL) OR ((validation_coverage >= (0)::numeric) AND (validation_coverage <= (1)::numeric)))),
    CONSTRAINT chk_datalab_keyword_validation_window_days CHECK (((validation_window_days IS NULL) OR (validation_window_days > 0))),
    CONSTRAINT datalab_category_keywords_polarity_check CHECK (((polarity)::text = ANY (ARRAY[('demand'::character varying)::text, ('risk'::character varying)::text, ('neutral'::character varying)::text])))
);


--
-- Name: datalab_category_stocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datalab_category_stocks (
    category_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    weight numeric(4,2) DEFAULT 1.0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: datalab_raw_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datalab_raw_details (
    raw_document_id bigint NOT NULL,
    category_id bigint NOT NULL,
    keyword character varying(100) NOT NULL,
    keyword_group character varying(100),
    observed_date date NOT NULL,
    search_index numeric(6,2) NOT NULL,
    previous_search_index numeric(6,2),
    change_pct numeric(8,2),
    period_type character varying(10) DEFAULT 'daily'::character varying NOT NULL,
    device character varying(10) DEFAULT 'all'::character varying NOT NULL,
    gender character varying(5) DEFAULT 'all'::character varying NOT NULL,
    age_group character varying(20) DEFAULT 'all'::character varying NOT NULL,
    is_spike boolean DEFAULT false NOT NULL,
    extra_payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT datalab_raw_details_device_check CHECK (((device)::text = ANY (ARRAY[('pc'::character varying)::text, ('mobile'::character varying)::text, ('all'::character varying)::text]))),
    CONSTRAINT datalab_raw_details_gender_check CHECK (((gender)::text = ANY (ARRAY[('m'::character varying)::text, ('f'::character varying)::text, ('all'::character varying)::text]))),
    CONSTRAINT datalab_raw_details_period_type_check CHECK (((period_type)::text = ANY (ARRAY[('daily'::character varying)::text, ('weekly'::character varying)::text, ('monthly'::character varying)::text])))
);


--
-- Name: datalab_raw_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datalab_raw_documents (
    id bigint NOT NULL,
    category_id bigint NOT NULL,
    collector_run_id bigint,
    source_name character varying(100) NOT NULL,
    external_id character varying(500) NOT NULL,
    source_hash character varying(64) NOT NULL,
    title text NOT NULL,
    source_url text,
    published_at timestamp with time zone NOT NULL,
    collect_status character varying(20) DEFAULT 'success'::character varying NOT NULL,
    collect_error text,
    collected_at timestamp with time zone DEFAULT now() NOT NULL,
    collector_ver character varying(20) DEFAULT '1.0'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT datalab_raw_documents_collect_status_check CHECK (((collect_status)::text = ANY (ARRAY[('success'::character varying)::text, ('partial'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: datalab_raw_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.datalab_raw_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: datalab_raw_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.datalab_raw_documents_id_seq OWNED BY public.datalab_raw_documents.id;


--
-- Name: dead_letter; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dead_letter (
    id bigint NOT NULL,
    processing_queue_id bigint NOT NULL,
    stock_id bigint,
    task_type character varying(50) NOT NULL,
    priority character varying(10) DEFAULT 'batch'::character varying NOT NULL,
    source_raw_ids bigint[],
    source_signal_event_ids bigint[],
    source_analysis_result_ids bigint[],
    task_context jsonb,
    final_error_message text,
    final_retry_count smallint DEFAULT 0 NOT NULL,
    archived_at timestamp with time zone DEFAULT now() NOT NULL,
    replayed_at timestamp with time zone,
    replayed_task_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dead_letter_priority_check CHECK (((priority)::text = ANY (ARRAY[('immediate'::character varying)::text, ('batch'::character varying)::text])))
);


--
-- Name: dead_letter_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dead_letter_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dead_letter_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dead_letter_id_seq OWNED BY public.dead_letter.id;


--
-- Name: event_study_panel; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_study_panel (
    id bigint NOT NULL,
    signal_event_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    asof_date date NOT NULL,
    fwd_return_1d double precision,
    fwd_return_5d double precision,
    fwd_return_20d double precision,
    abnormal_return_20d double precision,
    universe_snapshot character varying(40) DEFAULT 'kospi20_seed'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: event_study_panel_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_study_panel_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_study_panel_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_study_panel_id_seq OWNED BY public.event_study_panel.id;


--
-- Name: final_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.final_signals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: final_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.final_signals_id_seq OWNED BY public.final_signals.id;


--
-- Name: fundamentals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fundamentals (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    fiscal_date date NOT NULL,
    period_type character varying(10) NOT NULL,
    revenue bigint,
    net_income bigint,
    operating_margin numeric(8,2),
    eps numeric(10,2),
    bps numeric(10,2),
    per numeric(8,2),
    pbr numeric(8,2),
    roe numeric(8,2),
    roa numeric(8,2),
    debt_ratio numeric(8,2),
    source character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fundamentals_period_type_check CHECK (((period_type)::text = ANY (ARRAY[('annual'::character varying)::text, ('quarter'::character varying)::text])))
);


--
-- Name: fundamentals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fundamentals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fundamentals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fundamentals_id_seq OWNED BY public.fundamentals.id;


--
-- Name: fx_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fx_rates (
    id bigint NOT NULL,
    pair character varying(16) DEFAULT 'USD/KRW'::character varying NOT NULL,
    trade_date date NOT NULL,
    rate double precision,
    mid double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: fx_rates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fx_rates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fx_rates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fx_rates_id_seq OWNED BY public.fx_rates.id;


--
-- Name: guard_news_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guard_news_events (
    id bigint NOT NULL,
    source character varying(20) NOT NULL,
    article_hash character varying(64) NOT NULL,
    title text,
    url text,
    published_at timestamp with time zone,
    severity smallint,
    is_geopolitical_risk boolean,
    direction character varying(20),
    summary text,
    regions text[],
    affected_themes text[],
    confidence smallint,
    prompt_version character varying(40),
    judged_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_guard_news_direction CHECK (((direction IS NULL) OR ((direction)::text = ANY ((ARRAY['escalation'::character varying, 'deescalation'::character varying, 'unclear'::character varying])::text[]))))
);


--
-- Name: guard_news_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.guard_news_events ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.guard_news_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: guard_recommendations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guard_recommendations (
    id bigint NOT NULL,
    news_event_id bigint,
    suggested_scope character varying(30) NOT NULL,
    severity smallint,
    reason text,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    decided_by character varying(100),
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_guard_reco_scope CHECK (((suggested_scope)::text = ANY ((ARRAY['report_generation'::character varying, 'report_view'::character varying, 'whole_site'::character varying])::text[]))),
    CONSTRAINT chk_guard_reco_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying])::text[])))
);


--
-- Name: guard_recommendations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.guard_recommendations ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.guard_recommendations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: guard_site_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guard_site_status (
    id smallint DEFAULT 1 NOT NULL,
    status character varying(20) DEFAULT 'ok'::character varying NOT NULL,
    scope character varying(30) DEFAULT 'report_generation'::character varying NOT NULL,
    mode character varying(20) DEFAULT 'advisory'::character varying NOT NULL,
    reason text,
    resume_at timestamp with time zone,
    triggered_by character varying(100),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_guard_mode CHECK (((mode)::text = ANY ((ARRAY['manual'::character varying, 'advisory'::character varying, 'auto'::character varying])::text[]))),
    CONSTRAINT chk_guard_scope CHECK (((scope)::text = ANY ((ARRAY['report_generation'::character varying, 'report_view'::character varying, 'whole_site'::character varying])::text[]))),
    CONSTRAINT chk_guard_status CHECK (((status)::text = ANY ((ARRAY['ok'::character varying, 'blocked'::character varying])::text[]))),
    CONSTRAINT guard_site_status_singleton CHECK ((id = 1))
);


--
-- Name: guard_status_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guard_status_audit (
    id bigint NOT NULL,
    action character varying(20) NOT NULL,
    scope character varying(30),
    reason text,
    actor character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: guard_status_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.guard_status_audit ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.guard_status_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: hiring_baseline; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_baseline (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    avg_search_volume numeric(10,2) DEFAULT 0 NOT NULL,
    q1_factor numeric(5,3) DEFAULT 1.0 NOT NULL,
    q2_factor numeric(5,3) DEFAULT 1.0 NOT NULL,
    q3_factor numeric(5,3) DEFAULT 1.0 NOT NULL,
    q4_factor numeric(5,3) DEFAULT 1.0 NOT NULL,
    keyword_group_name character varying(200),
    data_start_date date,
    data_end_date date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hiring_baseline_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hiring_baseline_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hiring_baseline_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hiring_baseline_id_seq OWNED BY public.hiring_baseline.id;


--
-- Name: hiring_job_function_stocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_job_function_stocks (
    job_function_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    weight numeric(4,2) DEFAULT 1.0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hiring_job_functions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_job_functions (
    id bigint NOT NULL,
    function_key character varying(40) NOT NULL,
    label character varying(100) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hiring_job_functions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hiring_job_functions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hiring_job_functions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hiring_job_functions_id_seq OWNED BY public.hiring_job_functions.id;


--
-- Name: hiring_portal_company_ids; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_portal_company_ids (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    portal character varying(20) NOT NULL,
    company_id character varying(40) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hiring_portal_company_ids_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hiring_portal_company_ids_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hiring_portal_company_ids_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hiring_portal_company_ids_id_seq OWNED BY public.hiring_portal_company_ids.id;


--
-- Name: hiring_quarantine; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_quarantine (
    id bigint NOT NULL,
    collector_run_id bigint,
    source_type character varying(20) NOT NULL,
    source_label character varying(100),
    company_name text,
    violation_reason character varying(200) NOT NULL,
    record_payload jsonb NOT NULL,
    raw_payload text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    replayed_at timestamp with time zone,
    replayed_run_id bigint
);


--
-- Name: hiring_quarantine_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hiring_quarantine_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hiring_quarantine_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hiring_quarantine_id_seq OWNED BY public.hiring_quarantine.id;


--
-- Name: hiring_raw_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_raw_details (
    raw_document_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    keyword character varying(100),
    job_category character varying(100),
    job_count integer,
    previous_job_count integer,
    change_pct numeric(8,2),
    extra_payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    observed_date date NOT NULL,
    ocr_skills jsonb,
    ocr_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    CONSTRAINT chk_hiring_ocr_status CHECK (((ocr_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('success'::character varying)::text, ('failed'::character varying)::text, ('skipped'::character varying)::text])))
);


--
-- Name: hiring_search_trend; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_search_trend (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    keyword_group character varying(100) NOT NULL,
    period_date date NOT NULL,
    search_index numeric(10,4) NOT NULL,
    period_type character varying(10) DEFAULT 'weekly'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hiring_search_trend_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hiring_search_trend_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hiring_search_trend_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hiring_search_trend_id_seq OWNED BY public.hiring_search_trend.id;


--
-- Name: hiring_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_signals (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    observed_date date NOT NULL,
    job_count integer DEFAULT 0 NOT NULL,
    baseline numeric(10,2),
    relative_strength numeric(8,4),
    is_spike boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    calculation_phase character varying(1)
);


--
-- Name: hiring_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hiring_signals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hiring_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hiring_signals_id_seq OWNED BY public.hiring_signals.id;


--
-- Name: hiring_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hiring_sources (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    crawler_type public.hiring_crawler_type NOT NULL,
    crawler_class character varying(100),
    base_url character varying(500),
    extra_config jsonb,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hiring_sources_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hiring_sources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hiring_sources_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hiring_sources_id_seq OWNED BY public.hiring_sources.id;


--
-- Name: meta_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meta_signals (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    run_key character varying(50) DEFAULT 'ML'::character varying NOT NULL,
    asof_date date NOT NULL,
    horizon smallint NOT NULL,
    combined_vol double precision,
    confidence double precision DEFAULT 0 NOT NULL,
    method character varying(20) DEFAULT 'stacking'::character varying NOT NULL,
    model_count smallint DEFAULT 0 NOT NULL,
    weight_breakdown jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    final_score double precision,
    direction character varying(16),
    CONSTRAINT meta_signals_direction_check CHECK (((direction IS NULL) OR ((direction)::text = ANY (ARRAY[('positive'::character varying)::text, ('negative'::character varying)::text, ('neutral'::character varying)::text, ('unknown'::character varying)::text])))),
    CONSTRAINT meta_signals_method_check CHECK (((method)::text = ANY (ARRAY[('stacking'::character varying)::text, ('equal_fallback'::character varying)::text, ('empty'::character varying)::text, ('linear_stacking'::character varying)::text])))
);


--
-- Name: meta_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.meta_signals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: meta_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.meta_signals_id_seq OWNED BY public.meta_signals.id;


--
-- Name: ml_inferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ml_inferences (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    run_key character varying(50) DEFAULT 'ML'::character varying NOT NULL,
    asof_date date NOT NULL,
    model_name character varying(50) NOT NULL,
    horizon smallint NOT NULL,
    pred_value double precision,
    device character varying(10) DEFAULT 'cpu'::character varying NOT NULL,
    gate_passed boolean DEFAULT true NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ml_inferences_device_check CHECK (((device)::text = ANY (ARRAY[('cpu'::character varying)::text, ('gpu'::character varying)::text])))
);


--
-- Name: ml_inferences_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ml_inferences_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ml_inferences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ml_inferences_id_seq OWNED BY public.ml_inferences.id;


--
-- Name: ml_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ml_scores (
    id bigint NOT NULL,
    result_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    model_version_id bigint,
    ml_score numeric(5,2) NOT NULL,
    calibrated_score numeric(5,2) NOT NULL,
    prediction_label character varying(20),
    feature_importance jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ml_scores_calibrated_score_check CHECK (((calibrated_score >= (0)::numeric) AND (calibrated_score <= (100)::numeric)))
);


--
-- Name: ml_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ml_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ml_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ml_scores_id_seq OWNED BY public.ml_scores.id;


--
-- Name: ohlcv_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ohlcv_data (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    trade_date date NOT NULL,
    open numeric(12,2) NOT NULL,
    high numeric(12,2) NOT NULL,
    low numeric(12,2) NOT NULL,
    close numeric(12,2) NOT NULL,
    volume bigint NOT NULL,
    adjusted_close numeric(12,2),
    foreign_net bigint,
    institution_net bigint,
    change_pct numeric(6,2),
    market_cap bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    individual_net bigint
);


--
-- Name: ohlcv_data_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ohlcv_data_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ohlcv_data_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ohlcv_data_id_seq OWNED BY public.ohlcv_data.id;


--
-- Name: patent_raw_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patent_raw_details (
    raw_document_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    application_no character varying(30) NOT NULL,
    patent_title text NOT NULL,
    applicant_name character varying(200),
    application_date date NOT NULL,
    tech_category character varying(50),
    is_new_category boolean DEFAULT false NOT NULL,
    extra_payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    llm_features jsonb,
    llm_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    publication_date date,
    CONSTRAINT patent_raw_details_llm_status_check CHECK (((llm_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('success'::character varying)::text, ('failed'::character varying)::text, ('skipped'::character varying)::text])))
);


--
-- Name: COLUMN patent_raw_details.publication_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.patent_raw_details.publication_date IS '특허 공개일(출원 후 ~18개월). 시장에 정보가 노출되는 이벤트 시점. NULL 가능(미상). 출처: KIPRIS OpeningDate / Google Patents publication_date';


--
-- Name: payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payments (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    subscription_id bigint,
    imp_uid character varying(100) NOT NULL,
    merchant_uid character varying(100) NOT NULL,
    amount integer NOT NULL,
    currency character varying(8) DEFAULT 'KRW'::character varying NOT NULL,
    status character varying(20) NOT NULL,
    paid_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    refund_amount integer DEFAULT 0 NOT NULL,
    cancel_reason text,
    raw_response jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT payments_status_check CHECK (((status)::text = ANY (ARRAY[('paid'::character varying)::text, ('cancelled'::character varying)::text, ('partial_cancelled'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: payments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.payments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.payments_id_seq OWNED BY public.payments.id;


--
-- Name: portone_verifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portone_verifications (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    imp_uid character varying(100) NOT NULL,
    merchant_uid character varying(100) NOT NULL,
    verification_type character varying(20),
    status character varying(20) NOT NULL,
    verified_at timestamp with time zone,
    raw_response jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT portone_verifications_verification_type_check CHECK (((verification_type)::text = ANY (ARRAY[('identity'::character varying)::text, ('payment'::character varying)::text])))
);


--
-- Name: portone_verifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portone_verifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portone_verifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portone_verifications_id_seq OWNED BY public.portone_verifications.id;


--
-- Name: price_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_snapshots (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    trade_date date NOT NULL,
    current_price numeric(12,2) NOT NULL,
    open numeric(12,2),
    high numeric(12,2),
    low numeric(12,2),
    volume bigint,
    trade_value bigint,
    market_cap bigint,
    shares_outstanding bigint,
    per numeric(10,2),
    pbr numeric(10,2),
    eps numeric(12,2),
    bps numeric(12,2),
    roe numeric(8,2),
    roa numeric(8,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: price_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.price_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: price_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.price_snapshots_id_seq OWNED BY public.price_snapshots.id;


--
-- Name: processing_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.processing_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: processing_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.processing_queue_id_seq OWNED BY public.processing_queue.id;


--
-- Name: program_trading; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.program_trading (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    trade_date date NOT NULL,
    prog_buy_qty bigint,
    prog_sell_qty bigint,
    prog_net_qty bigint,
    prog_net_amt bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: program_trading_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.program_trading_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: program_trading_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.program_trading_id_seq OWNED BY public.program_trading.id;


--
-- Name: quant_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quant_scores (
    id bigint NOT NULL,
    result_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    score_breakdown jsonb NOT NULL,
    overall_score numeric(5,2) NOT NULL,
    available_sources text[] NOT NULL,
    missing_sources text[],
    source_agreement character varying(10) NOT NULL,
    failed_agent_count smallint DEFAULT 0 NOT NULL,
    alert_level smallint DEFAULT 0 NOT NULL,
    score_cap_applied boolean DEFAULT false NOT NULL,
    score_cap_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT quant_scores_alert_level_check CHECK ((alert_level = ANY (ARRAY[0, 1, 2, 3]))),
    CONSTRAINT quant_scores_source_agreement_check CHECK (((source_agreement)::text = ANY (ARRAY[('HIGH'::character varying)::text, ('MEDIUM'::character varying)::text, ('LOW'::character varying)::text])))
);


--
-- Name: quant_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.quant_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quant_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.quant_scores_id_seq OWNED BY public.quant_scores.id;


--
-- Name: raw_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_documents (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    collector_run_id bigint,
    source_type character varying(20) NOT NULL,
    source_name character varying(100) NOT NULL,
    external_id character varying(200) NOT NULL,
    source_hash character varying(64) NOT NULL,
    title text NOT NULL,
    source_url text,
    published_at timestamp with time zone NOT NULL,
    collect_status character varying(20) DEFAULT 'success'::character varying NOT NULL,
    collect_error text,
    collected_at timestamp with time zone DEFAULT now() NOT NULL,
    collector_ver character varying(20) DEFAULT '1.0'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT raw_documents_collect_status_check CHECK (((collect_status)::text = ANY (ARRAY[('success'::character varying)::text, ('partial'::character varying)::text, ('failed'::character varying)::text]))),
    CONSTRAINT raw_documents_source_type_check CHECK (((source_type)::text = ANY (ARRAY[('DART'::character varying)::text, ('REPORT'::character varying)::text, ('HIRING'::character varying)::text, ('PATENT'::character varying)::text, ('DATALAB'::character varying)::text])))
);


--
-- Name: raw_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.raw_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raw_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.raw_documents_id_seq OWNED BY public.raw_documents.id;


--
-- Name: recommendations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recommendations (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    asof_date date NOT NULL,
    run_key character varying(30) DEFAULT 'REC'::character varying NOT NULL,
    rank smallint NOT NULL,
    recommendation_score numeric(6,2) NOT NULL,
    basis character varying(10) NOT NULL,
    signal character varying(10),
    final_score numeric(5,2),
    confidence numeric(6,4),
    combined_vol double precision,
    components jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT recommendations_basis_check CHECK (((basis)::text = ANY (ARRAY[('final'::character varying)::text, ('meta'::character varying)::text]))),
    CONSTRAINT recommendations_recommendation_score_check CHECK (((recommendation_score >= (0)::numeric) AND (recommendation_score <= (100)::numeric))),
    CONSTRAINT recommendations_signal_check CHECK (((signal)::text = ANY (ARRAY[('positive'::character varying)::text, ('negative'::character varying)::text, ('neutral'::character varying)::text, ('mixed'::character varying)::text])))
);


--
-- Name: recommendations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.recommendations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: recommendations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.recommendations_id_seq OWNED BY public.recommendations.id;


--
-- Name: report_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.report_chunks (
    id bigint NOT NULL,
    report_raw_detail_id bigint NOT NULL,
    chunk_index integer NOT NULL,
    chunk_text text NOT NULL,
    embedding public.vector(768) NOT NULL,
    token_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    stock_id bigint NOT NULL
);


--
-- Name: report_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.report_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: report_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.report_chunks_id_seq OWNED BY public.report_chunks.id;


--
-- Name: report_issuances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.report_issuances (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    final_signal_id bigint NOT NULL,
    run_key character varying(30) NOT NULL,
    issued_via character varying(20) NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT report_issuances_issued_via_check CHECK (((issued_via)::text = ANY (ARRAY[('free'::character varying)::text, ('subscription'::character varying)::text])))
);


--
-- Name: report_issuances_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.report_issuances_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: report_issuances_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.report_issuances_id_seq OWNED BY public.report_issuances.id;


--
-- Name: report_raw_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.report_raw_details (
    raw_document_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    securities_firm character varying(100) NOT NULL,
    analyst_name character varying(100),
    publish_date date NOT NULL,
    investment_opinion character varying(20),
    target_price integer,
    previous_target_price integer,
    current_price_at_publish integer,
    upside_pct numeric(6,2),
    has_pdf boolean DEFAULT false NOT NULL,
    pdf_url text,
    local_file_path text,
    extracted_text text,
    extracted_text_path text,
    parsing_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    parsing_error text,
    extra_payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    s3_key character varying(500),
    parsed_at timestamp with time zone,
    key_rationale text,
    CONSTRAINT report_raw_details_parsing_status_check CHECK (((parsing_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('success'::character varying)::text, ('failed'::character varying)::text, ('skipped'::character varying)::text])))
);


--
-- Name: report_valuation_facts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.report_valuation_facts (
    raw_document_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    ticker character varying(20) NOT NULL,
    broker character varying(100),
    analyst character varying(100),
    publish_date date,
    target_price integer,
    forward_eps_est integer,
    eps_fy integer,
    methodology character varying(20) DEFAULT 'unknown'::character varying NOT NULL,
    applied_multiple numeric(12,4),
    implied_multiple numeric(12,4),
    peer_group jsonb DEFAULT '[]'::jsonb NOT NULL,
    category_tag character varying(80),
    rerating_thesis text,
    extraction_source character varying(30) DEFAULT 'rules'::character varying NOT NULL,
    needs_review boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT report_valuation_facts_extraction_source_check CHECK (((extraction_source)::text = ANY (ARRAY[('rules'::character varying)::text, ('llm'::character varying)::text, ('rules_fallback'::character varying)::text]))),
    CONSTRAINT report_valuation_facts_methodology_check CHECK (((methodology)::text = ANY (ARRAY[('PER'::character varying)::text, ('PBR'::character varying)::text, ('EV_EBITDA'::character varying)::text, ('SOTP'::character varying)::text, ('DCF'::character varying)::text, ('mixed'::character varying)::text, ('unknown'::character varying)::text])))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    filename text NOT NULL,
    checksum character(64) NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: score_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.score_history (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    final_signal_id bigint,
    analysis_result_id bigint,
    signal_date date NOT NULL,
    scored_at timestamp with time zone DEFAULT now() NOT NULL,
    final_score numeric(5,2) NOT NULL,
    pre_xgb_score numeric(5,2),
    reliability_score numeric(5,2),
    model_version character varying(20),
    scoring_version character varying(20),
    reanalysis_reason character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_score_history_reference CHECK (((final_signal_id IS NOT NULL) OR (analysis_result_id IS NOT NULL)))
);


--
-- Name: score_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.score_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: score_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.score_history_id_seq OWNED BY public.score_history.id;


--
-- Name: securities_lending_trend; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.securities_lending_trend (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    trade_date date NOT NULL,
    lending_contract bigint,
    lending_repay bigint,
    lending_change bigint,
    lending_balance bigint,
    lending_balance_amount bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: securities_lending_trend_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.securities_lending_trend_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: securities_lending_trend_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.securities_lending_trend_id_seq OWNED BY public.securities_lending_trend.id;


--
-- Name: short_selling_trend; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.short_selling_trend (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    trade_date date NOT NULL,
    close_price bigint,
    change_rate numeric(8,2),
    volume bigint,
    short_volume bigint,
    short_weight_pct numeric(8,2),
    short_value_thousand_krw bigint,
    short_avg_price bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: short_selling_trend_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.short_selling_trend_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: short_selling_trend_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.short_selling_trend_id_seq OWNED BY public.short_selling_trend.id;


--
-- Name: signal_episodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_episodes (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    signal_date date NOT NULL,
    run_key text NOT NULL,
    direction text,
    score double precision,
    sources jsonb,
    embedding public.vector(768) NOT NULL,
    outcome jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: signal_episodes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_episodes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_episodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_episodes_id_seq OWNED BY public.signal_episodes.id;


--
-- Name: signal_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_events_id_seq OWNED BY public.signal_events.id;


--
-- Name: signal_journal_chart_prices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_journal_chart_prices (
    stock_id bigint NOT NULL,
    trade_date date NOT NULL,
    close_price numeric(12,2) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: signal_journal_outcomes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_journal_outcomes (
    id bigint NOT NULL,
    journal_id bigint NOT NULL,
    horizon character varying(10) NOT NULL,
    base_trade_date date NOT NULL,
    base_price numeric(12,2) NOT NULL,
    outcome_trade_date date NOT NULL,
    outcome_price numeric(12,2) NOT NULL,
    change_pct numeric(6,2) NOT NULL,
    checked_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_journal_outcome_horizon CHECK (((horizon)::text = ANY ((ARRAY['7td'::character varying, '30td'::character varying])::text[])))
);


--
-- Name: signal_journal_outcomes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.signal_journal_outcomes ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.signal_journal_outcomes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: signal_journals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_journals (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    final_signal_id bigint,
    stock_id bigint NOT NULL,
    user_view character varying(20) NOT NULL,
    user_memo text,
    signal_score_at_time numeric(5,2),
    signal_value_at_time character varying(10),
    source_agreement_at_time character varying(10),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    retrospective_memo text,
    retro_outcome_class character varying(20),
    CONSTRAINT signal_journals_user_view_check CHECK (((user_view)::text = ANY (ARRAY[('watch'::character varying)::text, ('research_more'::character varying)::text, ('not_relevant'::character varying)::text])))
);


--
-- Name: signal_journals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_journals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_journals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_journals_id_seq OWNED BY public.signal_journals.id;


--
-- Name: signal_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_metrics (
    id bigint NOT NULL,
    signal_event_id bigint NOT NULL,
    metric_name character varying(50) NOT NULL,
    metric_value numeric(15,4) NOT NULL,
    metric_unit character varying(20),
    previous_value numeric(15,4),
    change_pct numeric(8,2),
    period_start date,
    period_end date,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: signal_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_metrics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_metrics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_metrics_id_seq OWNED BY public.signal_metrics.id;


--
-- Name: signal_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_subscriptions (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    plan_id bigint NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    payment_method character varying(50),
    billing_cycle character varying(10),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    next_billing_at timestamp with time zone,
    auto_renew boolean DEFAULT false NOT NULL,
    CONSTRAINT signal_subscriptions_billing_cycle_check CHECK ((((billing_cycle)::text = ANY (ARRAY[('monthly'::character varying)::text, ('yearly'::character varying)::text])) OR (billing_cycle IS NULL))),
    CONSTRAINT signal_subscriptions_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('expired'::character varying)::text, ('cancelled'::character varying)::text, ('trial'::character varying)::text])))
);


--
-- Name: signal_subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_subscriptions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_subscriptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_subscriptions_id_seq OWNED BY public.signal_subscriptions.id;


--
-- Name: social_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_accounts (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    provider character varying(20) NOT NULL,
    provider_user_id character varying(100) NOT NULL,
    access_token text,
    refresh_token text,
    token_expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT social_accounts_provider_check CHECK (((provider)::text = ANY (ARRAY[('google'::character varying)::text, ('kakao'::character varying)::text, ('naver'::character varying)::text])))
);


--
-- Name: social_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.social_accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: social_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.social_accounts_id_seq OWNED BY public.social_accounts.id;


--
-- Name: source_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.source_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: source_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.source_documents_id_seq OWNED BY public.source_documents.id;


--
-- Name: stock_news_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.stock_news ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.stock_news_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: stock_logo_published; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_logo_published (
    stock_id bigint NOT NULL,
    image bytea NOT NULL,
    mime_type character varying(30) DEFAULT 'image/png'::character varying NOT NULL,
    source character varying(40),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: stock_price_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_price_daily (
    stock_id bigint NOT NULL,
    trade_date date NOT NULL,
    close_price numeric(12,2) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: stocks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.stocks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: stocks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.stocks_id_seq OWNED BY public.stocks.id;


--
-- Name: subscription_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscription_plans (
    id bigint NOT NULL,
    plan_type character varying(20) NOT NULL,
    plan_display_name character varying(50) NOT NULL,
    max_watchlist integer DEFAULT 3 NOT NULL,
    signal_delay_hours integer DEFAULT 24 NOT NULL,
    journal_max_entries integer DEFAULT 50 NOT NULL,
    has_alt_data boolean DEFAULT false NOT NULL,
    has_detail_report boolean DEFAULT false NOT NULL,
    has_backtesting boolean DEFAULT false NOT NULL,
    price_monthly integer DEFAULT 0 NOT NULL,
    price_yearly integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: subscription_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subscription_plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subscription_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subscription_plans_id_seq OWNED BY public.subscription_plans.id;


--
-- Name: ta_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ta_scores (
    id bigint NOT NULL,
    result_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    ta_score numeric(5,2),
    ta_detail jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ta_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ta_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ta_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ta_scores_id_seq OWNED BY public.ta_scores.id;


--
-- Name: terms_agreements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.terms_agreements (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    terms_type character varying(50) NOT NULL,
    version character varying(20) NOT NULL,
    agreed boolean DEFAULT true NOT NULL,
    agreed_at timestamp with time zone DEFAULT now() NOT NULL,
    ip_address inet
);


--
-- Name: terms_agreements_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.terms_agreements_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: terms_agreements_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.terms_agreements_id_seq OWNED BY public.terms_agreements.id;


--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sessions (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    refresh_token_hash text NOT NULL,
    user_agent text,
    ip_address inet,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_sessions_id_seq OWNED BY public.user_sessions.id;


--
-- Name: user_signal_reads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_signal_reads (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    final_signal_id bigint NOT NULL,
    read_date date DEFAULT CURRENT_DATE NOT NULL,
    read_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_signal_reads_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_signal_reads_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_signal_reads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_signal_reads_id_seq OWNED BY public.user_signal_reads.id;


--
-- Name: user_trade_fills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_trade_fills (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    stock_id bigint,
    ticker character varying(20) NOT NULL,
    side character varying(4) NOT NULL,
    filled_at timestamp with time zone NOT NULL,
    quantity numeric(18,4) NOT NULL,
    price numeric(18,4) NOT NULL,
    fee numeric(18,4),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_trade_fill_qty CHECK ((quantity > (0)::numeric)),
    CONSTRAINT chk_trade_fill_side CHECK (((side)::text = ANY ((ARRAY['buy'::character varying, 'sell'::character varying])::text[])))
);


--
-- Name: user_trade_fills_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.user_trade_fills ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.user_trade_fills_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_trade_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_trade_plans (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    stock_id bigint,
    ticker character varying(20) NOT NULL,
    thesis text DEFAULT ''::text NOT NULL,
    target_price numeric(18,4),
    stop_price numeric(18,4),
    sell_condition text,
    planned_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_trade_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.user_trade_plans ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.user_trade_plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_trade_signal_overlays; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_trade_signal_overlays (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    ticker character varying(20) NOT NULL,
    signal_date date NOT NULL,
    kind character varying(20) NOT NULL,
    source_ref character varying(40) DEFAULT ''::character varying NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_overlay_kind CHECK (((kind)::text = ANY ((ARRAY['insider_sell'::character varying, 'insider_buy'::character varying])::text[])))
);


--
-- Name: user_trade_signal_overlays_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.user_trade_signal_overlays ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.user_trade_signal_overlays_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    member_code character varying(20) NOT NULL,
    email character varying(255) NOT NULL,
    password_hash text,
    nickname character varying(50),
    agreed_risk boolean DEFAULT false NOT NULL,
    is_verified boolean DEFAULT false NOT NULL,
    email_verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    phone character varying(20),
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    CONSTRAINT chk_users_status CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('suspended'::character varying)::text, ('deleted'::character varying)::text])))
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: validation_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.validation_logs (
    id bigint NOT NULL,
    target_type character varying(30) NOT NULL,
    target_id_int bigint,
    target_id_uuid uuid,
    validation_type character varying(50) NOT NULL,
    passed boolean NOT NULL,
    message text,
    retry_count smallint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_validation_target_id CHECK ((num_nonnulls(target_id_int, target_id_uuid) = 1)),
    CONSTRAINT validation_logs_target_type_check CHECK (((target_type)::text = ANY (ARRAY[('signal_event'::character varying)::text, ('signal_metric'::character varying)::text, ('analysis_result'::character varying)::text, ('agent_result'::character varying)::text, ('final_signal'::character varying)::text, ('llm_output'::character varying)::text])))
);


--
-- Name: validation_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.validation_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: validation_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.validation_logs_id_seq OWNED BY public.validation_logs.id;


--
-- Name: watchlists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlists (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    notification_enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: watchlists_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.watchlists_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: watchlists_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.watchlists_id_seq OWNED BY public.watchlists.id;


--
-- Name: xgb_model_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.xgb_model_versions (
    id bigint NOT NULL,
    model_version character varying(20) NOT NULL,
    trained_at timestamp with time zone,
    feature_names jsonb,
    feature_importance jsonb,
    validation_score numeric(5,2),
    is_active boolean DEFAULT false NOT NULL,
    training_samples integer,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: xgb_model_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.xgb_model_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: xgb_model_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.xgb_model_versions_id_seq OWNED BY public.xgb_model_versions.id;


--
-- Name: admin_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_accounts ALTER COLUMN id SET DEFAULT nextval('public.admin_accounts_id_seq'::regclass);


--
-- Name: admin_audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_audit_log ALTER COLUMN id SET DEFAULT nextval('public.admin_audit_log_id_seq'::regclass);


--
-- Name: admin_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_sessions ALTER COLUMN id SET DEFAULT nextval('public.admin_sessions_id_seq'::regclass);


--
-- Name: agent_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_results ALTER COLUMN id SET DEFAULT nextval('public.agent_results_id_seq'::regclass);


--
-- Name: ai_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_scores ALTER COLUMN id SET DEFAULT nextval('public.ai_scores_id_seq'::regclass);


--
-- Name: analysis_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_requests ALTER COLUMN id SET DEFAULT nextval('public.analysis_requests_id_seq'::regclass);


--
-- Name: analysis_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results ALTER COLUMN id SET DEFAULT nextval('public.analysis_results_id_seq'::regclass);


--
-- Name: backtest_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_results ALTER COLUMN id SET DEFAULT nextval('public.backtest_results_id_seq'::regclass);


--
-- Name: collector_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collector_runs ALTER COLUMN id SET DEFAULT nextval('public.collector_runs_id_seq'::regclass);


--
-- Name: credit_trade_trend id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_trade_trend ALTER COLUMN id SET DEFAULT nextval('public.credit_trade_trend_id_seq'::regclass);


--
-- Name: dart_corp_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_corp_codes ALTER COLUMN id SET DEFAULT nextval('public.dart_corp_codes_id_seq'::regclass);


--
-- Name: dart_employee_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_employee_stats ALTER COLUMN id SET DEFAULT nextval('public.dart_employee_stats_id_seq'::regclass);


--
-- Name: dart_financial_facts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_financial_facts ALTER COLUMN id SET DEFAULT nextval('public.dart_financial_facts_id_seq'::regclass);


--
-- Name: dart_ownership_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_ownership_events ALTER COLUMN id SET DEFAULT nextval('public.dart_ownership_events_id_seq'::regclass);


--
-- Name: datalab_categories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_categories ALTER COLUMN id SET DEFAULT nextval('public.datalab_categories_id_seq'::regclass);


--
-- Name: datalab_raw_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_documents ALTER COLUMN id SET DEFAULT nextval('public.datalab_raw_documents_id_seq'::regclass);


--
-- Name: dead_letter id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter ALTER COLUMN id SET DEFAULT nextval('public.dead_letter_id_seq'::regclass);


--
-- Name: event_study_panel id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_study_panel ALTER COLUMN id SET DEFAULT nextval('public.event_study_panel_id_seq'::regclass);


--
-- Name: final_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals ALTER COLUMN id SET DEFAULT nextval('public.final_signals_id_seq'::regclass);


--
-- Name: fundamentals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fundamentals ALTER COLUMN id SET DEFAULT nextval('public.fundamentals_id_seq'::regclass);


--
-- Name: fx_rates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fx_rates ALTER COLUMN id SET DEFAULT nextval('public.fx_rates_id_seq'::regclass);


--
-- Name: hiring_baseline id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_baseline ALTER COLUMN id SET DEFAULT nextval('public.hiring_baseline_id_seq'::regclass);


--
-- Name: hiring_job_functions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_job_functions ALTER COLUMN id SET DEFAULT nextval('public.hiring_job_functions_id_seq'::regclass);


--
-- Name: hiring_portal_company_ids id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_portal_company_ids ALTER COLUMN id SET DEFAULT nextval('public.hiring_portal_company_ids_id_seq'::regclass);


--
-- Name: hiring_quarantine id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_quarantine ALTER COLUMN id SET DEFAULT nextval('public.hiring_quarantine_id_seq'::regclass);


--
-- Name: hiring_search_trend id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_search_trend ALTER COLUMN id SET DEFAULT nextval('public.hiring_search_trend_id_seq'::regclass);


--
-- Name: hiring_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_signals ALTER COLUMN id SET DEFAULT nextval('public.hiring_signals_id_seq'::regclass);


--
-- Name: hiring_sources id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_sources ALTER COLUMN id SET DEFAULT nextval('public.hiring_sources_id_seq'::regclass);


--
-- Name: meta_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_signals ALTER COLUMN id SET DEFAULT nextval('public.meta_signals_id_seq'::regclass);


--
-- Name: ml_inferences id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_inferences ALTER COLUMN id SET DEFAULT nextval('public.ml_inferences_id_seq'::regclass);


--
-- Name: ml_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_scores ALTER COLUMN id SET DEFAULT nextval('public.ml_scores_id_seq'::regclass);


--
-- Name: ohlcv_data id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ohlcv_data ALTER COLUMN id SET DEFAULT nextval('public.ohlcv_data_id_seq'::regclass);


--
-- Name: payments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments ALTER COLUMN id SET DEFAULT nextval('public.payments_id_seq'::regclass);


--
-- Name: portone_verifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portone_verifications ALTER COLUMN id SET DEFAULT nextval('public.portone_verifications_id_seq'::regclass);


--
-- Name: price_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_snapshots ALTER COLUMN id SET DEFAULT nextval('public.price_snapshots_id_seq'::regclass);


--
-- Name: processing_queue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_queue ALTER COLUMN id SET DEFAULT nextval('public.processing_queue_id_seq'::regclass);


--
-- Name: program_trading id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_trading ALTER COLUMN id SET DEFAULT nextval('public.program_trading_id_seq'::regclass);


--
-- Name: quant_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quant_scores ALTER COLUMN id SET DEFAULT nextval('public.quant_scores_id_seq'::regclass);


--
-- Name: raw_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_documents ALTER COLUMN id SET DEFAULT nextval('public.raw_documents_id_seq'::regclass);


--
-- Name: recommendations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recommendations ALTER COLUMN id SET DEFAULT nextval('public.recommendations_id_seq'::regclass);


--
-- Name: report_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_chunks ALTER COLUMN id SET DEFAULT nextval('public.report_chunks_id_seq'::regclass);


--
-- Name: report_issuances id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances ALTER COLUMN id SET DEFAULT nextval('public.report_issuances_id_seq'::regclass);


--
-- Name: score_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_history ALTER COLUMN id SET DEFAULT nextval('public.score_history_id_seq'::regclass);


--
-- Name: securities_lending_trend id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.securities_lending_trend ALTER COLUMN id SET DEFAULT nextval('public.securities_lending_trend_id_seq'::regclass);


--
-- Name: short_selling_trend id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.short_selling_trend ALTER COLUMN id SET DEFAULT nextval('public.short_selling_trend_id_seq'::regclass);


--
-- Name: signal_episodes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_episodes ALTER COLUMN id SET DEFAULT nextval('public.signal_episodes_id_seq'::regclass);


--
-- Name: signal_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events ALTER COLUMN id SET DEFAULT nextval('public.signal_events_id_seq'::regclass);


--
-- Name: signal_journals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journals ALTER COLUMN id SET DEFAULT nextval('public.signal_journals_id_seq'::regclass);


--
-- Name: signal_metrics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_metrics ALTER COLUMN id SET DEFAULT nextval('public.signal_metrics_id_seq'::regclass);


--
-- Name: signal_subscriptions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_subscriptions ALTER COLUMN id SET DEFAULT nextval('public.signal_subscriptions_id_seq'::regclass);


--
-- Name: social_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts ALTER COLUMN id SET DEFAULT nextval('public.social_accounts_id_seq'::regclass);


--
-- Name: source_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_documents ALTER COLUMN id SET DEFAULT nextval('public.source_documents_id_seq'::regclass);


--
-- Name: stocks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stocks ALTER COLUMN id SET DEFAULT nextval('public.stocks_id_seq'::regclass);


--
-- Name: subscription_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_plans ALTER COLUMN id SET DEFAULT nextval('public.subscription_plans_id_seq'::regclass);


--
-- Name: ta_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ta_scores ALTER COLUMN id SET DEFAULT nextval('public.ta_scores_id_seq'::regclass);


--
-- Name: terms_agreements id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.terms_agreements ALTER COLUMN id SET DEFAULT nextval('public.terms_agreements_id_seq'::regclass);


--
-- Name: user_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions ALTER COLUMN id SET DEFAULT nextval('public.user_sessions_id_seq'::regclass);


--
-- Name: user_signal_reads id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_signal_reads ALTER COLUMN id SET DEFAULT nextval('public.user_signal_reads_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: validation_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.validation_logs ALTER COLUMN id SET DEFAULT nextval('public.validation_logs_id_seq'::regclass);


--
-- Name: watchlists id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists ALTER COLUMN id SET DEFAULT nextval('public.watchlists_id_seq'::regclass);


--
-- Name: xgb_model_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.xgb_model_versions ALTER COLUMN id SET DEFAULT nextval('public.xgb_model_versions_id_seq'::regclass);


--
-- Name: admin_accounts admin_accounts_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_accounts
    ADD CONSTRAINT admin_accounts_email_key UNIQUE (email);


--
-- Name: admin_accounts admin_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_accounts
    ADD CONSTRAINT admin_accounts_pkey PRIMARY KEY (id);


--
-- Name: admin_audit_log admin_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_pkey PRIMARY KEY (id);


--
-- Name: admin_sessions admin_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_sessions
    ADD CONSTRAINT admin_sessions_pkey PRIMARY KEY (id);


--
-- Name: admin_sessions admin_sessions_session_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_sessions
    ADD CONSTRAINT admin_sessions_session_token_key UNIQUE (session_token);


--
-- Name: agent_results agent_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_results
    ADD CONSTRAINT agent_results_pkey PRIMARY KEY (id);


--
-- Name: ai_scores ai_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_scores
    ADD CONSTRAINT ai_scores_pkey PRIMARY KEY (id);


--
-- Name: ai_scores ai_scores_result_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_scores
    ADD CONSTRAINT ai_scores_result_id_key UNIQUE (result_id);


--
-- Name: analysis_requests analysis_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_requests
    ADD CONSTRAINT analysis_requests_pkey PRIMARY KEY (id);


--
-- Name: analysis_results analysis_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_pkey PRIMARY KEY (id);


--
-- Name: backtest_results backtest_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_results
    ADD CONSTRAINT backtest_results_pkey PRIMARY KEY (id);


--
-- Name: collection_schedule_runs collection_schedule_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_schedule_runs
    ADD CONSTRAINT collection_schedule_runs_pkey PRIMARY KEY (id);


--
-- Name: collection_schedules collection_schedules_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_schedules
    ADD CONSTRAINT collection_schedules_name_key UNIQUE (name);


--
-- Name: collection_schedules collection_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_schedules
    ADD CONSTRAINT collection_schedules_pkey PRIMARY KEY (id);


--
-- Name: collector_runs collector_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collector_runs
    ADD CONSTRAINT collector_runs_pkey PRIMARY KEY (id);


--
-- Name: community_comments community_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_comments
    ADD CONSTRAINT community_comments_pkey PRIMARY KEY (id);


--
-- Name: community_post_views community_post_views_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_post_views
    ADD CONSTRAINT community_post_views_pkey PRIMARY KEY (id);


--
-- Name: community_posts community_posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_posts
    ADD CONSTRAINT community_posts_pkey PRIMARY KEY (id);


--
-- Name: community_reactions community_reactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_reactions
    ADD CONSTRAINT community_reactions_pkey PRIMARY KEY (id);


--
-- Name: community_reports community_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_reports
    ADD CONSTRAINT community_reports_pkey PRIMARY KEY (id);


--
-- Name: credit_trade_trend credit_trade_trend_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_trade_trend
    ADD CONSTRAINT credit_trade_trend_pkey PRIMARY KEY (id);


--
-- Name: dart_collection_states dart_collection_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_collection_states
    ADD CONSTRAINT dart_collection_states_pkey PRIMARY KEY (stock_id);


--
-- Name: dart_corp_codes dart_corp_codes_corp_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_corp_codes
    ADD CONSTRAINT dart_corp_codes_corp_code_key UNIQUE (corp_code);


--
-- Name: dart_corp_codes dart_corp_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_corp_codes
    ADD CONSTRAINT dart_corp_codes_pkey PRIMARY KEY (id);


--
-- Name: dart_employee_stats dart_employee_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_employee_stats
    ADD CONSTRAINT dart_employee_stats_pkey PRIMARY KEY (id);


--
-- Name: dart_financial_facts dart_financial_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_financial_facts
    ADD CONSTRAINT dart_financial_facts_pkey PRIMARY KEY (id);


--
-- Name: dart_ownership_events dart_ownership_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_ownership_events
    ADD CONSTRAINT dart_ownership_events_pkey PRIMARY KEY (id);


--
-- Name: dart_raw_details dart_raw_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_raw_details
    ADD CONSTRAINT dart_raw_details_pkey PRIMARY KEY (raw_document_id);


--
-- Name: dart_raw_details dart_raw_details_receipt_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_raw_details
    ADD CONSTRAINT dart_raw_details_receipt_no_key UNIQUE (receipt_no);


--
-- Name: datalab_categories datalab_categories_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_categories
    ADD CONSTRAINT datalab_categories_name_key UNIQUE (name);


--
-- Name: datalab_categories datalab_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_categories
    ADD CONSTRAINT datalab_categories_pkey PRIMARY KEY (id);


--
-- Name: datalab_category_keywords datalab_category_keywords_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_category_keywords
    ADD CONSTRAINT datalab_category_keywords_pkey PRIMARY KEY (category_id, keyword);


--
-- Name: datalab_category_stocks datalab_category_stocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_category_stocks
    ADD CONSTRAINT datalab_category_stocks_pkey PRIMARY KEY (category_id, stock_id);


--
-- Name: datalab_raw_details datalab_raw_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_details
    ADD CONSTRAINT datalab_raw_details_pkey PRIMARY KEY (raw_document_id);


--
-- Name: datalab_raw_documents datalab_raw_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_documents
    ADD CONSTRAINT datalab_raw_documents_pkey PRIMARY KEY (id);


--
-- Name: datalab_raw_documents datalab_raw_documents_source_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_documents
    ADD CONSTRAINT datalab_raw_documents_source_hash_key UNIQUE (source_hash);


--
-- Name: dead_letter dead_letter_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter
    ADD CONSTRAINT dead_letter_pkey PRIMARY KEY (id);


--
-- Name: dead_letter dead_letter_processing_queue_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter
    ADD CONSTRAINT dead_letter_processing_queue_id_key UNIQUE (processing_queue_id);


--
-- Name: event_study_panel event_study_panel_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_study_panel
    ADD CONSTRAINT event_study_panel_pkey PRIMARY KEY (id);


--
-- Name: final_signals final_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals
    ADD CONSTRAINT final_signals_pkey PRIMARY KEY (id);


--
-- Name: fundamentals fundamentals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fundamentals
    ADD CONSTRAINT fundamentals_pkey PRIMARY KEY (id);


--
-- Name: fx_rates fx_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fx_rates
    ADD CONSTRAINT fx_rates_pkey PRIMARY KEY (id);


--
-- Name: guard_news_events guard_news_events_article_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guard_news_events
    ADD CONSTRAINT guard_news_events_article_hash_key UNIQUE (article_hash);


--
-- Name: guard_news_events guard_news_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guard_news_events
    ADD CONSTRAINT guard_news_events_pkey PRIMARY KEY (id);


--
-- Name: guard_recommendations guard_recommendations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guard_recommendations
    ADD CONSTRAINT guard_recommendations_pkey PRIMARY KEY (id);


--
-- Name: guard_site_status guard_site_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guard_site_status
    ADD CONSTRAINT guard_site_status_pkey PRIMARY KEY (id);


--
-- Name: guard_status_audit guard_status_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guard_status_audit
    ADD CONSTRAINT guard_status_audit_pkey PRIMARY KEY (id);


--
-- Name: hiring_baseline hiring_baseline_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_baseline
    ADD CONSTRAINT hiring_baseline_pkey PRIMARY KEY (id);


--
-- Name: hiring_job_function_stocks hiring_job_function_stocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_job_function_stocks
    ADD CONSTRAINT hiring_job_function_stocks_pkey PRIMARY KEY (job_function_id, stock_id);


--
-- Name: hiring_job_functions hiring_job_functions_function_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_job_functions
    ADD CONSTRAINT hiring_job_functions_function_key_key UNIQUE (function_key);


--
-- Name: hiring_job_functions hiring_job_functions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_job_functions
    ADD CONSTRAINT hiring_job_functions_pkey PRIMARY KEY (id);


--
-- Name: hiring_portal_company_ids hiring_portal_company_ids_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_portal_company_ids
    ADD CONSTRAINT hiring_portal_company_ids_pkey PRIMARY KEY (id);


--
-- Name: hiring_quarantine hiring_quarantine_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_quarantine
    ADD CONSTRAINT hiring_quarantine_pkey PRIMARY KEY (id);


--
-- Name: hiring_raw_details hiring_raw_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_raw_details
    ADD CONSTRAINT hiring_raw_details_pkey PRIMARY KEY (raw_document_id);


--
-- Name: hiring_search_trend hiring_search_trend_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_search_trend
    ADD CONSTRAINT hiring_search_trend_pkey PRIMARY KEY (id);


--
-- Name: hiring_signals hiring_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_pkey PRIMARY KEY (id);


--
-- Name: hiring_signals hiring_signals_stock_id_observed_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_stock_id_observed_date_key UNIQUE (stock_id, observed_date);


--
-- Name: hiring_sources hiring_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_sources
    ADD CONSTRAINT hiring_sources_pkey PRIMARY KEY (id);


--
-- Name: hiring_sources hiring_sources_stock_id_crawler_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_sources
    ADD CONSTRAINT hiring_sources_stock_id_crawler_type_key UNIQUE (stock_id, crawler_type);


--
-- Name: meta_signals meta_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_signals
    ADD CONSTRAINT meta_signals_pkey PRIMARY KEY (id);


--
-- Name: ml_inferences ml_inferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_inferences
    ADD CONSTRAINT ml_inferences_pkey PRIMARY KEY (id);


--
-- Name: ml_scores ml_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_scores
    ADD CONSTRAINT ml_scores_pkey PRIMARY KEY (id);


--
-- Name: ml_scores ml_scores_result_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_scores
    ADD CONSTRAINT ml_scores_result_id_key UNIQUE (result_id);


--
-- Name: ohlcv_data ohlcv_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ohlcv_data
    ADD CONSTRAINT ohlcv_data_pkey PRIMARY KEY (id);


--
-- Name: patent_raw_details patent_raw_details_application_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patent_raw_details
    ADD CONSTRAINT patent_raw_details_application_no_key UNIQUE (application_no);


--
-- Name: patent_raw_details patent_raw_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patent_raw_details
    ADD CONSTRAINT patent_raw_details_pkey PRIMARY KEY (raw_document_id);


--
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


--
-- Name: community_post_rankings pk_community_post_rankings; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_post_rankings
    ADD CONSTRAINT pk_community_post_rankings PRIMARY KEY (post_id, window_kind);


--
-- Name: portone_verifications portone_verifications_imp_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portone_verifications
    ADD CONSTRAINT portone_verifications_imp_uid_key UNIQUE (imp_uid);


--
-- Name: portone_verifications portone_verifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portone_verifications
    ADD CONSTRAINT portone_verifications_pkey PRIMARY KEY (id);


--
-- Name: price_snapshots price_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_snapshots
    ADD CONSTRAINT price_snapshots_pkey PRIMARY KEY (id);


--
-- Name: processing_queue processing_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_queue
    ADD CONSTRAINT processing_queue_pkey PRIMARY KEY (id);


--
-- Name: program_trading program_trading_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_trading
    ADD CONSTRAINT program_trading_pkey PRIMARY KEY (id);


--
-- Name: quant_scores quant_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quant_scores
    ADD CONSTRAINT quant_scores_pkey PRIMARY KEY (id);


--
-- Name: quant_scores quant_scores_result_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quant_scores
    ADD CONSTRAINT quant_scores_result_id_key UNIQUE (result_id);


--
-- Name: raw_documents raw_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_documents
    ADD CONSTRAINT raw_documents_pkey PRIMARY KEY (id);


--
-- Name: raw_documents raw_documents_source_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_documents
    ADD CONSTRAINT raw_documents_source_hash_key UNIQUE (source_hash);


--
-- Name: recommendations recommendations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recommendations
    ADD CONSTRAINT recommendations_pkey PRIMARY KEY (id);


--
-- Name: report_chunks report_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_chunks
    ADD CONSTRAINT report_chunks_pkey PRIMARY KEY (id);


--
-- Name: report_issuances report_issuances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances
    ADD CONSTRAINT report_issuances_pkey PRIMARY KEY (id);


--
-- Name: report_raw_details report_raw_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_raw_details
    ADD CONSTRAINT report_raw_details_pkey PRIMARY KEY (raw_document_id);


--
-- Name: report_valuation_facts report_valuation_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_valuation_facts
    ADD CONSTRAINT report_valuation_facts_pkey PRIMARY KEY (raw_document_id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (filename);


--
-- Name: score_history score_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_history
    ADD CONSTRAINT score_history_pkey PRIMARY KEY (id);


--
-- Name: securities_lending_trend securities_lending_trend_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.securities_lending_trend
    ADD CONSTRAINT securities_lending_trend_pkey PRIMARY KEY (id);


--
-- Name: short_selling_trend short_selling_trend_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.short_selling_trend
    ADD CONSTRAINT short_selling_trend_pkey PRIMARY KEY (id);


--
-- Name: signal_episodes signal_episodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_episodes
    ADD CONSTRAINT signal_episodes_pkey PRIMARY KEY (id);


--
-- Name: signal_events signal_events_event_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events
    ADD CONSTRAINT signal_events_event_hash_key UNIQUE (event_hash);


--
-- Name: signal_events signal_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events
    ADD CONSTRAINT signal_events_pkey PRIMARY KEY (id);


--
-- Name: signal_journal_chart_prices signal_journal_chart_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journal_chart_prices
    ADD CONSTRAINT signal_journal_chart_prices_pkey PRIMARY KEY (stock_id, trade_date);


--
-- Name: signal_journal_outcomes signal_journal_outcomes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journal_outcomes
    ADD CONSTRAINT signal_journal_outcomes_pkey PRIMARY KEY (id);


--
-- Name: signal_journals signal_journals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journals
    ADD CONSTRAINT signal_journals_pkey PRIMARY KEY (id);


--
-- Name: signal_metrics signal_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_metrics
    ADD CONSTRAINT signal_metrics_pkey PRIMARY KEY (id);


--
-- Name: signal_subscriptions signal_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_subscriptions
    ADD CONSTRAINT signal_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: social_accounts social_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts
    ADD CONSTRAINT social_accounts_pkey PRIMARY KEY (id);


--
-- Name: source_documents source_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_documents
    ADD CONSTRAINT source_documents_pkey PRIMARY KEY (id);


--
-- Name: source_documents source_documents_raw_document_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_documents
    ADD CONSTRAINT source_documents_raw_document_id_key UNIQUE (raw_document_id);


--
-- Name: stock_news_digest stock_news_digest_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_news_digest
    ADD CONSTRAINT stock_news_digest_pkey PRIMARY KEY (stock_id);


--
-- Name: stock_news stock_news_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_news
    ADD CONSTRAINT stock_news_pkey PRIMARY KEY (id);


--
-- Name: stock_news stock_news_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_news
    ADD CONSTRAINT stock_news_uniq UNIQUE (stock_id, article_hash);


--
-- Name: stock_logo_published stock_logo_published_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_logo_published
    ADD CONSTRAINT stock_logo_published_pkey PRIMARY KEY (stock_id);


--
-- Name: stock_price_daily stock_price_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_price_daily
    ADD CONSTRAINT stock_price_daily_pkey PRIMARY KEY (stock_id, trade_date);


--
-- Name: stocks stocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stocks
    ADD CONSTRAINT stocks_pkey PRIMARY KEY (id);


--
-- Name: stocks stocks_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stocks
    ADD CONSTRAINT stocks_ticker_key UNIQUE (ticker);


--
-- Name: subscription_plans subscription_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_plans
    ADD CONSTRAINT subscription_plans_pkey PRIMARY KEY (id);


--
-- Name: subscription_plans subscription_plans_plan_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_plans
    ADD CONSTRAINT subscription_plans_plan_type_key UNIQUE (plan_type);


--
-- Name: ta_scores ta_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ta_scores
    ADD CONSTRAINT ta_scores_pkey PRIMARY KEY (id);


--
-- Name: ta_scores ta_scores_result_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ta_scores
    ADD CONSTRAINT ta_scores_result_id_key UNIQUE (result_id);


--
-- Name: terms_agreements terms_agreements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.terms_agreements
    ADD CONSTRAINT terms_agreements_pkey PRIMARY KEY (id);


--
-- Name: agent_results uq_agent_result_method; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_results
    ADD CONSTRAINT uq_agent_result_method UNIQUE (result_id, debate_method);


--
-- Name: analysis_results uq_analysis; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT uq_analysis UNIQUE (stock_id, analysis_date, analysis_mode, run_key, version);


--
-- Name: community_post_views uq_community_post_view; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_post_views
    ADD CONSTRAINT uq_community_post_view UNIQUE (post_id, viewer_key, viewed_on);


--
-- Name: community_reactions uq_community_reaction; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_reactions
    ADD CONSTRAINT uq_community_reaction UNIQUE (user_id, target_type, target_id, type);


--
-- Name: community_reports uq_community_report; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_reports
    ADD CONSTRAINT uq_community_report UNIQUE (reporter_user_id, target_type, target_id);


--
-- Name: credit_trade_trend uq_credit_trade_trend_stock_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_trade_trend
    ADD CONSTRAINT uq_credit_trade_trend_stock_date UNIQUE (stock_id, trade_date);


--
-- Name: dart_corp_codes uq_dart_corp_ticker; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_corp_codes
    ADD CONSTRAINT uq_dart_corp_ticker UNIQUE (ticker);


--
-- Name: datalab_raw_details uq_datalab; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_details
    ADD CONSTRAINT uq_datalab UNIQUE (category_id, keyword, observed_date, period_type, device, gender, age_group);


--
-- Name: datalab_raw_documents uq_datalab_raw_doc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_documents
    ADD CONSTRAINT uq_datalab_raw_doc UNIQUE (source_name, external_id);


--
-- Name: event_study_panel uq_event_study_panel; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_study_panel
    ADD CONSTRAINT uq_event_study_panel UNIQUE (signal_event_id, asof_date);


--
-- Name: final_signals uq_final_signal_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals
    ADD CONSTRAINT uq_final_signal_version UNIQUE (stock_id, signal_date, run_key, version);


--
-- Name: fundamentals uq_fundamentals; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fundamentals
    ADD CONSTRAINT uq_fundamentals UNIQUE (stock_id, fiscal_date, period_type);


--
-- Name: fx_rates uq_fx_rates; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fx_rates
    ADD CONSTRAINT uq_fx_rates UNIQUE (pair, trade_date);


--
-- Name: hiring_baseline uq_hiring_baseline_stock; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_baseline
    ADD CONSTRAINT uq_hiring_baseline_stock UNIQUE (stock_id);


--
-- Name: hiring_portal_company_ids uq_hiring_portal_company_ids; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_portal_company_ids
    ADD CONSTRAINT uq_hiring_portal_company_ids UNIQUE (stock_id, portal);


--
-- Name: hiring_search_trend uq_hiring_search_trend_stock_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_search_trend
    ADD CONSTRAINT uq_hiring_search_trend_stock_date UNIQUE (stock_id, period_date);


--
-- Name: signal_journal_outcomes uq_journal_outcome; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journal_outcomes
    ADD CONSTRAINT uq_journal_outcome UNIQUE (journal_id, horizon);


--
-- Name: meta_signals uq_meta_signal; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_signals
    ADD CONSTRAINT uq_meta_signal UNIQUE (stock_id, run_key, asof_date, horizon);


--
-- Name: ml_inferences uq_ml_inference; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_inferences
    ADD CONSTRAINT uq_ml_inference UNIQUE (stock_id, run_key, asof_date, model_name, horizon);


--
-- Name: ohlcv_data uq_ohlcv; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ohlcv_data
    ADD CONSTRAINT uq_ohlcv UNIQUE (stock_id, trade_date);


--
-- Name: dart_ownership_events uq_ownership_event; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_ownership_events
    ADD CONSTRAINT uq_ownership_event UNIQUE (corp_code, rcept_no, holder_name, holder_type, line_seq);


--
-- Name: price_snapshots uq_price_snapshot; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_snapshots
    ADD CONSTRAINT uq_price_snapshot UNIQUE (stock_id, captured_at);


--
-- Name: program_trading uq_program_trading; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_trading
    ADD CONSTRAINT uq_program_trading UNIQUE (stock_id, trade_date);


--
-- Name: raw_documents uq_raw_document; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_documents
    ADD CONSTRAINT uq_raw_document UNIQUE (source_type, external_id);


--
-- Name: raw_documents uq_raw_document_stock; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_documents
    ADD CONSTRAINT uq_raw_document_stock UNIQUE (id, stock_id);


--
-- Name: user_signal_reads uq_read; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_signal_reads
    ADD CONSTRAINT uq_read UNIQUE (user_id, final_signal_id);


--
-- Name: recommendations uq_recommendation; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recommendations
    ADD CONSTRAINT uq_recommendation UNIQUE (stock_id, asof_date, run_key);


--
-- Name: report_chunks uq_report_chunks_detail_chunk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_chunks
    ADD CONSTRAINT uq_report_chunks_detail_chunk UNIQUE (report_raw_detail_id, chunk_index);


--
-- Name: report_issuances uq_report_issuance; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances
    ADD CONSTRAINT uq_report_issuance UNIQUE (user_id, final_signal_id);


--
-- Name: securities_lending_trend uq_securities_lending_trend_stock_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.securities_lending_trend
    ADD CONSTRAINT uq_securities_lending_trend_stock_date UNIQUE (stock_id, trade_date);


--
-- Name: short_selling_trend uq_short_selling_trend_stock_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.short_selling_trend
    ADD CONSTRAINT uq_short_selling_trend_stock_date UNIQUE (stock_id, trade_date);


--
-- Name: signal_episodes uq_signal_episodes_stock_date_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_episodes
    ADD CONSTRAINT uq_signal_episodes_stock_date_run UNIQUE (stock_id, signal_date, run_key);


--
-- Name: signal_metrics uq_signal_metric; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_metrics
    ADD CONSTRAINT uq_signal_metric UNIQUE (signal_event_id, metric_name);


--
-- Name: social_accounts uq_social; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts
    ADD CONSTRAINT uq_social UNIQUE (provider, provider_user_id);


--
-- Name: terms_agreements uq_terms_agreement; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.terms_agreements
    ADD CONSTRAINT uq_terms_agreement UNIQUE (user_id, terms_type, version);


--
-- Name: user_trade_signal_overlays uq_trade_overlay; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_signal_overlays
    ADD CONSTRAINT uq_trade_overlay UNIQUE (user_id, stock_id, signal_date, kind, source_ref);


--
-- Name: user_trade_plans uq_trade_plan; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_plans
    ADD CONSTRAINT uq_trade_plan UNIQUE (user_id, ticker);


--
-- Name: watchlists uq_watchlist; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT uq_watchlist UNIQUE (user_id, stock_id);


--
-- Name: user_sessions user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);


--
-- Name: user_sessions user_sessions_refresh_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_refresh_token_hash_key UNIQUE (refresh_token_hash);


--
-- Name: user_signal_reads user_signal_reads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_signal_reads
    ADD CONSTRAINT user_signal_reads_pkey PRIMARY KEY (id);


--
-- Name: user_trade_fills user_trade_fills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_fills
    ADD CONSTRAINT user_trade_fills_pkey PRIMARY KEY (id);


--
-- Name: user_trade_plans user_trade_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_plans
    ADD CONSTRAINT user_trade_plans_pkey PRIMARY KEY (id);


--
-- Name: user_trade_signal_overlays user_trade_signal_overlays_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_signal_overlays
    ADD CONSTRAINT user_trade_signal_overlays_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_member_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_member_code_key UNIQUE (member_code);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: validation_logs validation_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.validation_logs
    ADD CONSTRAINT validation_logs_pkey PRIMARY KEY (id);


--
-- Name: watchlists watchlists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_pkey PRIMARY KEY (id);


--
-- Name: xgb_model_versions xgb_model_versions_model_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.xgb_model_versions
    ADD CONSTRAINT xgb_model_versions_model_version_key UNIQUE (model_version);


--
-- Name: xgb_model_versions xgb_model_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.xgb_model_versions
    ADD CONSTRAINT xgb_model_versions_pkey PRIMARY KEY (id);


--
-- Name: idx_admin_accounts_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_accounts_email ON public.admin_accounts USING btree (email);


--
-- Name: idx_admin_accounts_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_accounts_is_active ON public.admin_accounts USING btree (is_active);


--
-- Name: idx_admin_audit_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_audit_actor ON public.admin_audit_log USING btree (actor_admin_id, created_at DESC);


--
-- Name: idx_admin_audit_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_audit_target ON public.admin_audit_log USING btree (target_type, target_id, created_at DESC);


--
-- Name: idx_admin_sessions_admin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_sessions_admin ON public.admin_sessions USING btree (admin_id);


--
-- Name: idx_admin_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_sessions_expires_at ON public.admin_sessions USING btree (expires_at);


--
-- Name: idx_admin_sessions_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_sessions_token ON public.admin_sessions USING btree (session_token);


--
-- Name: idx_agent_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_method ON public.agent_results USING btree (result_id, debate_method);


--
-- Name: idx_agent_result_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_result_id ON public.agent_results USING btree (result_id);


--
-- Name: idx_agent_source_signal_event_ids; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_source_signal_event_ids ON public.agent_results USING gin (source_signal_event_ids);


--
-- Name: idx_analysis_run_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analysis_run_key ON public.analysis_results USING btree (stock_id, analysis_date DESC, run_key);


--
-- Name: idx_analysis_signal_events; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analysis_signal_events ON public.analysis_results USING gin (source_signal_event_ids);


--
-- Name: idx_analysis_stock_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analysis_stock_date ON public.analysis_results USING btree (stock_id, analysis_date DESC);


--
-- Name: idx_collection_schedule_runs_schedule_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_collection_schedule_runs_schedule_started ON public.collection_schedule_runs USING btree (schedule_id, started_at DESC);


--
-- Name: idx_collector_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_collector_runs_status ON public.collector_runs USING btree (status, started_at DESC) WHERE ((status)::text <> 'success'::text);


--
-- Name: idx_collector_runs_type_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_collector_runs_type_time ON public.collector_runs USING btree (collector_type, started_at DESC);


--
-- Name: idx_community_comments_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_comments_parent ON public.community_comments USING btree (parent_comment_id);


--
-- Name: idx_community_comments_post; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_comments_post ON public.community_comments USING btree (post_id, created_at);


--
-- Name: idx_community_posts_author; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_posts_author ON public.community_posts USING btree (author_user_id);


--
-- Name: idx_community_posts_feed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_posts_feed ON public.community_posts USING btree (created_at DESC, id DESC) WHERE ((deleted_at IS NULL) AND ((status)::text = 'visible'::text));


--
-- Name: idx_community_posts_journal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_posts_journal ON public.community_posts USING btree (journal_id);


--
-- Name: idx_community_rankings_board; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_rankings_board ON public.community_post_rankings USING btree (window_kind, score DESC, post_id DESC);


--
-- Name: idx_community_reactions_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_reactions_target ON public.community_reactions USING btree (target_type, target_id, type);


--
-- Name: idx_community_reports_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_community_reports_target ON public.community_reports USING btree (target_type, target_id);


--
-- Name: idx_credit_trade_trend_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_trade_trend_date ON public.credit_trade_trend USING btree (trade_date);


--
-- Name: idx_dart_collection_states_end_de; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_collection_states_end_de ON public.dart_collection_states USING btree (last_end_de DESC);


--
-- Name: idx_dart_corp_codes_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_corp_codes_stock ON public.dart_corp_codes USING btree (stock_id) WHERE (stock_id IS NOT NULL);


--
-- Name: idx_dart_corp_codes_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_corp_codes_ticker ON public.dart_corp_codes USING btree (ticker);


--
-- Name: idx_dart_employee_corp_year; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_employee_corp_year ON public.dart_employee_stats USING btree (corp_code, bsns_year);


--
-- Name: idx_dart_employee_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_employee_stock ON public.dart_employee_stats USING btree (stock_id) WHERE (stock_id IS NOT NULL);


--
-- Name: idx_dart_fin_facts_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_fin_facts_account ON public.dart_financial_facts USING btree (account_id);


--
-- Name: idx_dart_fin_facts_corp_year; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_fin_facts_corp_year ON public.dart_financial_facts USING btree (corp_code, bsns_year);


--
-- Name: idx_dart_fin_facts_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_fin_facts_stock ON public.dart_financial_facts USING btree (stock_id) WHERE (stock_id IS NOT NULL);


--
-- Name: idx_dart_ownership_corp_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_ownership_corp_date ON public.dart_ownership_events USING btree (corp_code, report_date);


--
-- Name: idx_dart_ownership_holder_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_ownership_holder_type ON public.dart_ownership_events USING btree (holder_type);


--
-- Name: idx_dart_ownership_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_ownership_stock ON public.dart_ownership_events USING btree (stock_id) WHERE (stock_id IS NOT NULL);


--
-- Name: idx_dart_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_priority ON public.dart_raw_details USING btree (priority);


--
-- Name: idx_dart_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_stock ON public.dart_raw_details USING btree (stock_id);


--
-- Name: idx_dart_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dart_type ON public.dart_raw_details USING btree (disclosure_type);


--
-- Name: idx_datalab_cat_stocks_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_cat_stocks_stock ON public.datalab_category_stocks USING btree (stock_id);


--
-- Name: idx_datalab_categories_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_categories_active ON public.datalab_categories USING btree (is_active) WHERE (is_active = true);


--
-- Name: idx_datalab_category_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_category_date ON public.datalab_raw_details USING btree (category_id, observed_date DESC);


--
-- Name: idx_datalab_category_keywords_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_category_keywords_active ON public.datalab_category_keywords USING btree (category_id, is_active) WHERE (is_active = true);


--
-- Name: idx_datalab_category_keywords_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_category_keywords_pending ON public.datalab_category_keywords USING btree (category_id) WHERE ((review_status)::text = 'pending'::text);


--
-- Name: idx_datalab_raw_doc_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_raw_doc_category ON public.datalab_raw_documents USING btree (category_id, published_at DESC);


--
-- Name: idx_datalab_raw_doc_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_raw_doc_run ON public.datalab_raw_documents USING btree (collector_run_id) WHERE (collector_run_id IS NOT NULL);


--
-- Name: idx_datalab_spike; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datalab_spike ON public.datalab_raw_details USING btree (category_id, is_spike) WHERE (is_spike = true);


--
-- Name: idx_dead_letter_task_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dead_letter_task_type ON public.dead_letter USING btree (task_type, archived_at DESC);


--
-- Name: idx_dead_letter_unreplayed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dead_letter_unreplayed ON public.dead_letter USING btree (archived_at DESC) WHERE (replayed_at IS NULL);


--
-- Name: idx_event_study_panel_asof; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_study_panel_asof ON public.event_study_panel USING btree (asof_date, universe_snapshot);


--
-- Name: idx_event_study_panel_stock_asof; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_study_panel_stock_asof ON public.event_study_panel USING btree (stock_id, asof_date DESC);


--
-- Name: idx_final_published; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_final_published ON public.final_signals USING btree (is_published, published_at DESC) WHERE (is_published = true);


--
-- Name: idx_final_run_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_final_run_key ON public.final_signals USING btree (stock_id, signal_date DESC, run_key);


--
-- Name: idx_final_stock_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_final_stock_date ON public.final_signals USING btree (stock_id, signal_date DESC);


--
-- Name: idx_fx_rates_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fx_rates_date ON public.fx_rates USING btree (trade_date);


--
-- Name: idx_guard_news_events_judged_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_guard_news_events_judged_at ON public.guard_news_events USING btree (judged_at DESC);


--
-- Name: idx_guard_recommendations_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_guard_recommendations_status ON public.guard_recommendations USING btree (status, created_at DESC);


--
-- Name: idx_guard_status_audit_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_guard_status_audit_created ON public.guard_status_audit USING btree (created_at DESC);


--
-- Name: idx_hiring_baseline_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_baseline_stock ON public.hiring_baseline USING btree (stock_id);


--
-- Name: idx_hiring_change; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_change ON public.hiring_raw_details USING btree (stock_id, change_pct DESC);


--
-- Name: idx_hiring_function_stocks_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_function_stocks_stock ON public.hiring_job_function_stocks USING btree (stock_id);


--
-- Name: idx_hiring_observed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_observed ON public.hiring_raw_details USING btree (stock_id, observed_date DESC);


--
-- Name: idx_hiring_ocr_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_ocr_pending ON public.hiring_raw_details USING btree (raw_document_id DESC) WHERE ((ocr_status)::text = 'pending'::text);


--
-- Name: idx_hiring_quarantine_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_quarantine_label ON public.hiring_quarantine USING btree (source_label, created_at DESC);


--
-- Name: idx_hiring_quarantine_unreplayed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_quarantine_unreplayed ON public.hiring_quarantine USING btree (created_at DESC) WHERE (replayed_at IS NULL);


--
-- Name: idx_hiring_signals_spike; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_signals_spike ON public.hiring_signals USING btree (observed_date DESC) WHERE (is_spike = true);


--
-- Name: idx_hiring_signals_stock_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_signals_stock_date ON public.hiring_signals USING btree (stock_id, observed_date DESC);


--
-- Name: idx_hiring_sources_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_sources_active ON public.hiring_sources USING btree (stock_id) WHERE (is_active = true);


--
-- Name: idx_hiring_stock_keyword; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hiring_stock_keyword ON public.hiring_raw_details USING btree (stock_id, keyword);


--
-- Name: idx_journal_outcomes_journal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_journal_outcomes_journal ON public.signal_journal_outcomes USING btree (journal_id);


--
-- Name: idx_meta_signals_stock_asof; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_meta_signals_stock_asof ON public.meta_signals USING btree (stock_id, asof_date DESC);


--
-- Name: idx_ml_inferences_stock_asof; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ml_inferences_stock_asof ON public.ml_inferences USING btree (stock_id, asof_date DESC);


--
-- Name: idx_ohlcv_stock_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ohlcv_stock_date ON public.ohlcv_data USING btree (stock_id, trade_date DESC);


--
-- Name: idx_overlay_user_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_overlay_user_stock ON public.user_trade_signal_overlays USING btree (user_id, stock_id, signal_date);


--
-- Name: idx_patent_llm_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patent_llm_pending ON public.patent_raw_details USING btree (application_date DESC, raw_document_id DESC) WHERE ((llm_status)::text = 'pending'::text);


--
-- Name: idx_patent_new_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patent_new_category ON public.patent_raw_details USING btree (stock_id, is_new_category) WHERE (is_new_category = true);


--
-- Name: idx_patent_stock_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patent_stock_date ON public.patent_raw_details USING btree (stock_id, application_date DESC);


--
-- Name: idx_patent_stock_pubdate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patent_stock_pubdate ON public.patent_raw_details USING btree (stock_id, publication_date DESC);


--
-- Name: idx_patent_stock_tech; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patent_stock_tech ON public.patent_raw_details USING btree (stock_id, tech_category);


--
-- Name: idx_payments_imp_uid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payments_imp_uid ON public.payments USING btree (imp_uid);


--
-- Name: idx_payments_subscription; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payments_subscription ON public.payments USING btree (subscription_id) WHERE (subscription_id IS NOT NULL);


--
-- Name: idx_payments_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payments_user_created ON public.payments USING btree (user_id, created_at DESC);


--
-- Name: idx_portone_verifications_merchant_uid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portone_verifications_merchant_uid ON public.portone_verifications USING btree (merchant_uid);


--
-- Name: idx_portone_verifications_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portone_verifications_user_created ON public.portone_verifications USING btree (user_id, created_at DESC);


--
-- Name: idx_price_snapshots_stock_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_snapshots_stock_time ON public.price_snapshots USING btree (stock_id, captured_at DESC);


--
-- Name: idx_price_snapshots_trade_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_snapshots_trade_date ON public.price_snapshots USING btree (trade_date);


--
-- Name: idx_program_trading_stock_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_program_trading_stock_date ON public.program_trading USING btree (stock_id, trade_date);


--
-- Name: idx_queue_analysis_result_ids; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_analysis_result_ids ON public.processing_queue USING gin (source_analysis_result_ids);


--
-- Name: idx_queue_immediate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_immediate ON public.processing_queue USING btree (stock_id, scheduled_at) WHERE (((priority)::text = 'immediate'::text) AND ((status)::text = 'pending'::text));


--
-- Name: idx_queue_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_pending ON public.processing_queue USING btree (task_type, priority, scheduled_at) WHERE ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('retrying'::character varying)::text]));


--
-- Name: idx_queue_raw_ids; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_raw_ids ON public.processing_queue USING gin (source_raw_ids);


--
-- Name: idx_queue_signal_event_ids; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_signal_event_ids ON public.processing_queue USING gin (source_signal_event_ids);


--
-- Name: idx_queue_type_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_type_status ON public.processing_queue USING btree (task_type, status);


--
-- Name: idx_raw_doc_collect_fail; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_doc_collect_fail ON public.raw_documents USING btree (collect_status, created_at DESC) WHERE ((collect_status)::text <> 'success'::text);


--
-- Name: idx_raw_doc_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_doc_run ON public.raw_documents USING btree (collector_run_id) WHERE (collector_run_id IS NOT NULL);


--
-- Name: idx_raw_doc_stock_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_doc_stock_source ON public.raw_documents USING btree (stock_id, source_type, published_at DESC);


--
-- Name: idx_recommendations_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recommendations_rank ON public.recommendations USING btree (asof_date, run_key, rank);


--
-- Name: idx_report_chunks_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_chunks_embedding ON public.report_chunks USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_report_chunks_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_chunks_stock ON public.report_chunks USING btree (stock_id);


--
-- Name: idx_report_detail_firm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_detail_firm ON public.report_raw_details USING btree (securities_firm, stock_id);


--
-- Name: idx_report_detail_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_detail_stock ON public.report_raw_details USING btree (stock_id, publish_date DESC);


--
-- Name: idx_report_issuances_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_issuances_user ON public.report_issuances USING btree (user_id, issued_at DESC);


--
-- Name: idx_report_issuances_user_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_issuances_user_stock ON public.report_issuances USING btree (user_id, stock_id, issued_at DESC);


--
-- Name: idx_report_issuances_user_via; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_issuances_user_via ON public.report_issuances USING btree (user_id, issued_via);


--
-- Name: idx_report_valuation_methodology; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_valuation_methodology ON public.report_valuation_facts USING btree (methodology, stock_id);


--
-- Name: idx_report_valuation_needs_review; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_valuation_needs_review ON public.report_valuation_facts USING btree (needs_review, stock_id) WHERE (needs_review = true);


--
-- Name: idx_report_valuation_stock_publish; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_valuation_stock_publish ON public.report_valuation_facts USING btree (stock_id, publish_date DESC);


--
-- Name: idx_score_history_final_signal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_score_history_final_signal ON public.score_history USING btree (final_signal_id) WHERE (final_signal_id IS NOT NULL);


--
-- Name: idx_score_history_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_score_history_stock ON public.score_history USING btree (stock_id, signal_date DESC, scored_at DESC);


--
-- Name: idx_securities_lending_trend_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_securities_lending_trend_date ON public.securities_lending_trend USING btree (trade_date);


--
-- Name: idx_short_selling_trend_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_short_selling_trend_date ON public.short_selling_trend USING btree (trade_date);


--
-- Name: idx_signal_episodes_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signal_episodes_embedding ON public.signal_episodes USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_signal_journals_final_signal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signal_journals_final_signal ON public.signal_journals USING btree (final_signal_id) WHERE (final_signal_id IS NOT NULL);


--
-- Name: idx_signal_journals_stock_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signal_journals_stock_created ON public.signal_journals USING btree (stock_id, created_at DESC);


--
-- Name: idx_signal_journals_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signal_journals_user_created ON public.signal_journals USING btree (user_id, created_at DESC);


--
-- Name: idx_social_accounts_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_accounts_user ON public.social_accounts USING btree (user_id);


--
-- Name: idx_source_doc_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_source_doc_stock ON public.source_documents USING btree (stock_id, source_type, published_at DESC);


--
-- Name: idx_stock_news_digest_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stock_news_digest_ticker ON public.stock_news_digest USING btree (ticker);


--
-- Name: idx_stock_news_stock_collected; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stock_news_stock_collected ON public.stock_news USING btree (stock_id, collected_at DESC);


--
-- Name: idx_stock_news_stock_published; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stock_news_stock_published ON public.stock_news USING btree (stock_id, published_at DESC NULLS LAST);


--
-- Name: idx_stocks_is_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stocks_is_target ON public.stocks USING btree (is_target) WHERE (is_target = true);


--
-- Name: idx_subscription_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_subscription_active ON public.signal_subscriptions USING btree (user_id) WHERE ((status)::text = 'active'::text);


--
-- Name: idx_terms_agreements_user_agreed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_terms_agreements_user_agreed_at ON public.terms_agreements USING btree (user_id, agreed_at DESC);


--
-- Name: idx_trade_fills_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trade_fills_user_time ON public.user_trade_fills USING btree (user_id, filled_at DESC);


--
-- Name: idx_trade_fills_user_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trade_fills_user_stock ON public.user_trade_fills USING btree (user_id, stock_id, filled_at);


--
-- Name: idx_trade_plans_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trade_plans_user ON public.user_trade_plans USING btree (user_id);


--
-- Name: idx_user_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_sessions_expires_at ON public.user_sessions USING btree (expires_at);


--
-- Name: idx_user_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_sessions_user ON public.user_sessions USING btree (user_id, created_at DESC);


--
-- Name: idx_user_signal_reads_final_signal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_signal_reads_final_signal ON public.user_signal_reads USING btree (final_signal_id);


--
-- Name: idx_user_signal_reads_user_read_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_signal_reads_user_read_at ON public.user_signal_reads USING btree (user_id, read_at DESC);


--
-- Name: idx_watchlists_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlists_stock ON public.watchlists USING btree (stock_id);


--
-- Name: idx_watchlists_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlists_user ON public.watchlists USING btree (user_id);


--
-- Name: idx_xgb_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_xgb_active ON public.xgb_model_versions USING btree (is_active) WHERE (is_active = true);


--
-- Name: uq_dart_collection_states_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_dart_collection_states_ticker ON public.dart_collection_states USING btree (ticker);


--
-- Name: uq_dart_fin_fact; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_dart_fin_fact ON public.dart_financial_facts USING btree (corp_code, bsns_year, reprt_code, fs_div, sj_div, COALESCE(account_id, account_nm));


--
-- Name: uq_employee_stats; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_employee_stats ON public.dart_employee_stats USING btree (corp_code, bsns_year, reprt_code, COALESCE(segment, ''::character varying), COALESCE(sex, ''::character varying), line_seq);


--
-- Name: uq_final_signal_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_final_signal_current ON public.final_signals USING btree (stock_id, signal_date, run_key) WHERE (is_current = true);


--
-- Name: uq_source_doc_external; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_source_doc_external ON public.source_documents USING btree (external_ref_type, external_ref_id, stock_id) WHERE (external_ref_type IS NOT NULL);


--
-- Name: uq_users_phone_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_users_phone_active ON public.users USING btree (phone) WHERE ((phone IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: community_comments trg_community_comments_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_community_comments_updated_at BEFORE UPDATE ON public.community_comments FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: community_posts trg_community_posts_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_community_posts_updated_at BEFORE UPDATE ON public.community_posts FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: dart_collection_states trg_dart_collection_states_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dart_collection_states_updated_at BEFORE UPDATE ON public.dart_collection_states FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: dart_corp_codes trg_dart_corp_codes_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dart_corp_codes_updated_at BEFORE UPDATE ON public.dart_corp_codes FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: datalab_categories trg_datalab_categories_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_datalab_categories_updated_at BEFORE UPDATE ON public.datalab_categories FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: datalab_category_keywords trg_datalab_category_keywords_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_datalab_category_keywords_updated_at BEFORE UPDATE ON public.datalab_category_keywords FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: final_signals trg_final_signal_current; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_final_signal_current BEFORE INSERT OR UPDATE OF is_current, stock_id, signal_date, run_key ON public.final_signals FOR EACH ROW EXECUTE FUNCTION public.set_final_signal_current();


--
-- Name: hiring_baseline trg_hiring_baseline_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_hiring_baseline_updated_at BEFORE UPDATE ON public.hiring_baseline FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: hiring_job_functions trg_hiring_job_functions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_hiring_job_functions_updated_at BEFORE UPDATE ON public.hiring_job_functions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: processing_queue trg_processing_queue_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_processing_queue_updated_at BEFORE UPDATE ON public.processing_queue FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: report_valuation_facts trg_report_valuation_facts_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_report_valuation_facts_updated_at BEFORE UPDATE ON public.report_valuation_facts FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: signal_journals trg_signal_journals_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_signal_journals_updated_at BEFORE UPDATE ON public.signal_journals FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: signal_subscriptions trg_signal_subscriptions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_signal_subscriptions_updated_at BEFORE UPDATE ON public.signal_subscriptions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: stocks trg_stocks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_stocks_updated_at BEFORE UPDATE ON public.stocks FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: user_trade_plans trg_trade_plans_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_trade_plans_updated_at BEFORE UPDATE ON public.user_trade_plans FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: admin_audit_log admin_audit_log_actor_admin_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_audit_log
    ADD CONSTRAINT admin_audit_log_actor_admin_id_fkey FOREIGN KEY (actor_admin_id) REFERENCES public.admin_accounts(id);


--
-- Name: admin_sessions admin_sessions_admin_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_sessions
    ADD CONSTRAINT admin_sessions_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES public.admin_accounts(id);


--
-- Name: agent_results agent_results_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_results
    ADD CONSTRAINT agent_results_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.analysis_results(id);


--
-- Name: agent_results agent_results_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_results
    ADD CONSTRAINT agent_results_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: ai_scores ai_scores_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_scores
    ADD CONSTRAINT ai_scores_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.analysis_results(id);


--
-- Name: ai_scores ai_scores_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_scores
    ADD CONSTRAINT ai_scores_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: analysis_requests analysis_requests_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_requests
    ADD CONSTRAINT analysis_requests_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: analysis_results analysis_results_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: backtest_results backtest_results_final_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_results
    ADD CONSTRAINT backtest_results_final_signal_id_fkey FOREIGN KEY (final_signal_id) REFERENCES public.final_signals(id);


--
-- Name: backtest_results backtest_results_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_results
    ADD CONSTRAINT backtest_results_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: collection_schedule_runs collection_schedule_runs_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_schedule_runs
    ADD CONSTRAINT collection_schedule_runs_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES public.collection_schedules(id) ON DELETE SET NULL;


--
-- Name: community_comments community_comments_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_comments
    ADD CONSTRAINT community_comments_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: community_comments community_comments_parent_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_comments
    ADD CONSTRAINT community_comments_parent_comment_id_fkey FOREIGN KEY (parent_comment_id) REFERENCES public.community_comments(id) ON DELETE CASCADE;


--
-- Name: community_comments community_comments_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_comments
    ADD CONSTRAINT community_comments_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.community_posts(id) ON DELETE CASCADE;


--
-- Name: community_post_rankings community_post_rankings_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_post_rankings
    ADD CONSTRAINT community_post_rankings_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.community_posts(id) ON DELETE CASCADE;


--
-- Name: community_post_views community_post_views_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_post_views
    ADD CONSTRAINT community_post_views_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.community_posts(id) ON DELETE CASCADE;


--
-- Name: community_posts community_posts_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_posts
    ADD CONSTRAINT community_posts_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: community_posts community_posts_journal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_posts
    ADD CONSTRAINT community_posts_journal_id_fkey FOREIGN KEY (journal_id) REFERENCES public.signal_journals(id) ON DELETE SET NULL;


--
-- Name: community_reactions community_reactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_reactions
    ADD CONSTRAINT community_reactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: community_reports community_reports_reporter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_reports
    ADD CONSTRAINT community_reports_reporter_user_id_fkey FOREIGN KEY (reporter_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: credit_trade_trend credit_trade_trend_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_trade_trend
    ADD CONSTRAINT credit_trade_trend_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: dart_collection_states dart_collection_states_last_collector_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_collection_states
    ADD CONSTRAINT dart_collection_states_last_collector_run_id_fkey FOREIGN KEY (last_collector_run_id) REFERENCES public.collector_runs(id);


--
-- Name: dart_collection_states dart_collection_states_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_collection_states
    ADD CONSTRAINT dart_collection_states_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id) ON DELETE CASCADE;


--
-- Name: dart_corp_codes dart_corp_codes_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_corp_codes
    ADD CONSTRAINT dart_corp_codes_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: dart_employee_stats dart_employee_stats_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_employee_stats
    ADD CONSTRAINT dart_employee_stats_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: dart_financial_facts dart_financial_facts_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_financial_facts
    ADD CONSTRAINT dart_financial_facts_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: dart_ownership_events dart_ownership_events_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_ownership_events
    ADD CONSTRAINT dart_ownership_events_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: dart_raw_details dart_raw_details_raw_document_id_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dart_raw_details
    ADD CONSTRAINT dart_raw_details_raw_document_id_stock_id_fkey FOREIGN KEY (raw_document_id, stock_id) REFERENCES public.raw_documents(id, stock_id) ON DELETE CASCADE;


--
-- Name: datalab_category_keywords datalab_category_keywords_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_category_keywords
    ADD CONSTRAINT datalab_category_keywords_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.datalab_categories(id) ON DELETE CASCADE;


--
-- Name: datalab_category_stocks datalab_category_stocks_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_category_stocks
    ADD CONSTRAINT datalab_category_stocks_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.datalab_categories(id) ON DELETE CASCADE;


--
-- Name: datalab_category_stocks datalab_category_stocks_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_category_stocks
    ADD CONSTRAINT datalab_category_stocks_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: datalab_raw_details datalab_raw_details_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_details
    ADD CONSTRAINT datalab_raw_details_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.datalab_categories(id);


--
-- Name: datalab_raw_details datalab_raw_details_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_details
    ADD CONSTRAINT datalab_raw_details_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES public.datalab_raw_documents(id) ON DELETE CASCADE;


--
-- Name: datalab_raw_documents datalab_raw_documents_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_documents
    ADD CONSTRAINT datalab_raw_documents_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.datalab_categories(id);


--
-- Name: datalab_raw_documents datalab_raw_documents_collector_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datalab_raw_documents
    ADD CONSTRAINT datalab_raw_documents_collector_run_id_fkey FOREIGN KEY (collector_run_id) REFERENCES public.collector_runs(id);


--
-- Name: dead_letter dead_letter_processing_queue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter
    ADD CONSTRAINT dead_letter_processing_queue_id_fkey FOREIGN KEY (processing_queue_id) REFERENCES public.processing_queue(id);


--
-- Name: dead_letter dead_letter_replayed_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter
    ADD CONSTRAINT dead_letter_replayed_task_id_fkey FOREIGN KEY (replayed_task_id) REFERENCES public.processing_queue(id);


--
-- Name: dead_letter dead_letter_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter
    ADD CONSTRAINT dead_letter_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: event_study_panel event_study_panel_signal_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_study_panel
    ADD CONSTRAINT event_study_panel_signal_event_id_fkey FOREIGN KEY (signal_event_id) REFERENCES public.signal_events(id) ON DELETE CASCADE;


--
-- Name: event_study_panel event_study_panel_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_study_panel
    ADD CONSTRAINT event_study_panel_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: final_signals final_signals_analysis_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals
    ADD CONSTRAINT final_signals_analysis_result_id_fkey FOREIGN KEY (analysis_result_id) REFERENCES public.analysis_results(id);


--
-- Name: final_signals final_signals_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals
    ADD CONSTRAINT final_signals_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: fundamentals fundamentals_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fundamentals
    ADD CONSTRAINT fundamentals_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: guard_recommendations guard_recommendations_news_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guard_recommendations
    ADD CONSTRAINT guard_recommendations_news_event_id_fkey FOREIGN KEY (news_event_id) REFERENCES public.guard_news_events(id) ON DELETE SET NULL;


--
-- Name: hiring_baseline hiring_baseline_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_baseline
    ADD CONSTRAINT hiring_baseline_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id) ON DELETE CASCADE;


--
-- Name: hiring_job_function_stocks hiring_job_function_stocks_job_function_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_job_function_stocks
    ADD CONSTRAINT hiring_job_function_stocks_job_function_id_fkey FOREIGN KEY (job_function_id) REFERENCES public.hiring_job_functions(id) ON DELETE CASCADE;


--
-- Name: hiring_job_function_stocks hiring_job_function_stocks_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_job_function_stocks
    ADD CONSTRAINT hiring_job_function_stocks_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: hiring_portal_company_ids hiring_portal_company_ids_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_portal_company_ids
    ADD CONSTRAINT hiring_portal_company_ids_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: hiring_quarantine hiring_quarantine_collector_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_quarantine
    ADD CONSTRAINT hiring_quarantine_collector_run_id_fkey FOREIGN KEY (collector_run_id) REFERENCES public.collector_runs(id);


--
-- Name: hiring_quarantine hiring_quarantine_replayed_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_quarantine
    ADD CONSTRAINT hiring_quarantine_replayed_run_id_fkey FOREIGN KEY (replayed_run_id) REFERENCES public.collector_runs(id);


--
-- Name: hiring_raw_details hiring_raw_details_raw_document_id_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_raw_details
    ADD CONSTRAINT hiring_raw_details_raw_document_id_stock_id_fkey FOREIGN KEY (raw_document_id, stock_id) REFERENCES public.raw_documents(id, stock_id) ON DELETE CASCADE;


--
-- Name: hiring_search_trend hiring_search_trend_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_search_trend
    ADD CONSTRAINT hiring_search_trend_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: hiring_signals hiring_signals_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: hiring_sources hiring_sources_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hiring_sources
    ADD CONSTRAINT hiring_sources_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: meta_signals meta_signals_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_signals
    ADD CONSTRAINT meta_signals_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: ml_inferences ml_inferences_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_inferences
    ADD CONSTRAINT ml_inferences_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: ml_scores ml_scores_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_scores
    ADD CONSTRAINT ml_scores_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.xgb_model_versions(id);


--
-- Name: ml_scores ml_scores_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_scores
    ADD CONSTRAINT ml_scores_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.analysis_results(id);


--
-- Name: ml_scores ml_scores_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_scores
    ADD CONSTRAINT ml_scores_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: ohlcv_data ohlcv_data_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ohlcv_data
    ADD CONSTRAINT ohlcv_data_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: patent_raw_details patent_raw_details_raw_document_id_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patent_raw_details
    ADD CONSTRAINT patent_raw_details_raw_document_id_stock_id_fkey FOREIGN KEY (raw_document_id, stock_id) REFERENCES public.raw_documents(id, stock_id) ON DELETE CASCADE;


--
-- Name: payments payments_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.signal_subscriptions(id);


--
-- Name: payments payments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: portone_verifications portone_verifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portone_verifications
    ADD CONSTRAINT portone_verifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: price_snapshots price_snapshots_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_snapshots
    ADD CONSTRAINT price_snapshots_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: processing_queue processing_queue_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_queue
    ADD CONSTRAINT processing_queue_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: program_trading program_trading_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_trading
    ADD CONSTRAINT program_trading_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: quant_scores quant_scores_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quant_scores
    ADD CONSTRAINT quant_scores_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.analysis_results(id);


--
-- Name: quant_scores quant_scores_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quant_scores
    ADD CONSTRAINT quant_scores_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: raw_documents raw_documents_collector_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_documents
    ADD CONSTRAINT raw_documents_collector_run_id_fkey FOREIGN KEY (collector_run_id) REFERENCES public.collector_runs(id);


--
-- Name: raw_documents raw_documents_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_documents
    ADD CONSTRAINT raw_documents_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: recommendations recommendations_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recommendations
    ADD CONSTRAINT recommendations_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: report_chunks report_chunks_report_raw_detail_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_chunks
    ADD CONSTRAINT report_chunks_report_raw_detail_id_fkey FOREIGN KEY (report_raw_detail_id) REFERENCES public.report_raw_details(raw_document_id) ON DELETE CASCADE;


--
-- Name: report_chunks report_chunks_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_chunks
    ADD CONSTRAINT report_chunks_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: report_issuances report_issuances_final_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances
    ADD CONSTRAINT report_issuances_final_signal_id_fkey FOREIGN KEY (final_signal_id) REFERENCES public.final_signals(id);


--
-- Name: report_issuances report_issuances_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances
    ADD CONSTRAINT report_issuances_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: report_issuances report_issuances_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances
    ADD CONSTRAINT report_issuances_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: report_raw_details report_raw_details_raw_document_id_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_raw_details
    ADD CONSTRAINT report_raw_details_raw_document_id_stock_id_fkey FOREIGN KEY (raw_document_id, stock_id) REFERENCES public.raw_documents(id, stock_id) ON DELETE CASCADE;


--
-- Name: report_valuation_facts report_valuation_facts_raw_document_id_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_valuation_facts
    ADD CONSTRAINT report_valuation_facts_raw_document_id_stock_id_fkey FOREIGN KEY (raw_document_id, stock_id) REFERENCES public.raw_documents(id, stock_id) ON DELETE CASCADE;


--
-- Name: score_history score_history_analysis_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_history
    ADD CONSTRAINT score_history_analysis_result_id_fkey FOREIGN KEY (analysis_result_id) REFERENCES public.analysis_results(id);


--
-- Name: score_history score_history_final_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_history
    ADD CONSTRAINT score_history_final_signal_id_fkey FOREIGN KEY (final_signal_id) REFERENCES public.final_signals(id);


--
-- Name: score_history score_history_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_history
    ADD CONSTRAINT score_history_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: securities_lending_trend securities_lending_trend_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.securities_lending_trend
    ADD CONSTRAINT securities_lending_trend_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: short_selling_trend short_selling_trend_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.short_selling_trend
    ADD CONSTRAINT short_selling_trend_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: signal_episodes signal_episodes_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_episodes
    ADD CONSTRAINT signal_episodes_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: signal_events signal_events_source_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events
    ADD CONSTRAINT signal_events_source_document_id_fkey FOREIGN KEY (source_document_id) REFERENCES public.source_documents(id);


--
-- Name: signal_events signal_events_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events
    ADD CONSTRAINT signal_events_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: signal_journal_chart_prices signal_journal_chart_prices_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journal_chart_prices
    ADD CONSTRAINT signal_journal_chart_prices_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: signal_journal_outcomes signal_journal_outcomes_journal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journal_outcomes
    ADD CONSTRAINT signal_journal_outcomes_journal_id_fkey FOREIGN KEY (journal_id) REFERENCES public.signal_journals(id) ON DELETE CASCADE;


--
-- Name: signal_journals signal_journals_final_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journals
    ADD CONSTRAINT signal_journals_final_signal_id_fkey FOREIGN KEY (final_signal_id) REFERENCES public.final_signals(id);


--
-- Name: signal_journals signal_journals_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journals
    ADD CONSTRAINT signal_journals_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: signal_journals signal_journals_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journals
    ADD CONSTRAINT signal_journals_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: signal_metrics signal_metrics_signal_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_metrics
    ADD CONSTRAINT signal_metrics_signal_event_id_fkey FOREIGN KEY (signal_event_id) REFERENCES public.signal_events(id) ON DELETE CASCADE;


--
-- Name: signal_subscriptions signal_subscriptions_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_subscriptions
    ADD CONSTRAINT signal_subscriptions_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.subscription_plans(id);


--
-- Name: signal_subscriptions signal_subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_subscriptions
    ADD CONSTRAINT signal_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: social_accounts social_accounts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts
    ADD CONSTRAINT social_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: stock_logo_published stock_logo_published_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_logo_published
    ADD CONSTRAINT stock_logo_published_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: stock_price_daily stock_price_daily_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_price_daily
    ADD CONSTRAINT stock_price_daily_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: ta_scores ta_scores_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ta_scores
    ADD CONSTRAINT ta_scores_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.analysis_results(id);


--
-- Name: ta_scores ta_scores_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ta_scores
    ADD CONSTRAINT ta_scores_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: terms_agreements terms_agreements_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.terms_agreements
    ADD CONSTRAINT terms_agreements_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_sessions user_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_signal_reads user_signal_reads_final_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_signal_reads
    ADD CONSTRAINT user_signal_reads_final_signal_id_fkey FOREIGN KEY (final_signal_id) REFERENCES public.final_signals(id);


--
-- Name: user_signal_reads user_signal_reads_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_signal_reads
    ADD CONSTRAINT user_signal_reads_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_trade_fills user_trade_fills_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_fills
    ADD CONSTRAINT user_trade_fills_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id) ON DELETE SET NULL;


--
-- Name: user_trade_fills user_trade_fills_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_fills
    ADD CONSTRAINT user_trade_fills_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_trade_plans user_trade_plans_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_plans
    ADD CONSTRAINT user_trade_plans_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id) ON DELETE SET NULL;


--
-- Name: user_trade_plans user_trade_plans_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_plans
    ADD CONSTRAINT user_trade_plans_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_trade_signal_overlays user_trade_signal_overlays_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_signal_overlays
    ADD CONSTRAINT user_trade_signal_overlays_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id) ON DELETE CASCADE;


--
-- Name: user_trade_signal_overlays user_trade_signal_overlays_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_trade_signal_overlays
    ADD CONSTRAINT user_trade_signal_overlays_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: watchlists watchlists_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--
-- Name: watchlists watchlists_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict ryuiOmqpigFZpPKVMTsp1VVefGCZF8o2ajyuhMq0ZDJV1BMsjfnbuCgYaUKeiYe

