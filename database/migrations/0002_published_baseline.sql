-- 0002_published_baseline.sql
-- target: all
-- ============================================================================
-- PUBLISHED 발행 테이블 + 전역 객체(타입/함수/확장). 양쪽 DB 에 적용.
-- ※ rebaseline.py 가 생성. 직접 수정 금지 — 스키마 변경은 새 마이그로 추가.
-- ============================================================================

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
    CONSTRAINT stocks_market_check CHECK (((market)::text = ANY ((ARRAY['KOSPI'::character varying, 'KOSDAQ'::character varying])::text[])))
);


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
    CONSTRAINT agent_results_debate_method_check CHECK (((debate_method)::text = ANY ((ARRAY['D-1'::character varying, 'D-2'::character varying, 'D-3'::character varying, 'D-4'::character varying, 'D-5'::character varying])::text[]))),
    CONSTRAINT agent_results_method_score_check CHECK (((method_score >= (0)::numeric) AND (method_score <= (100)::numeric))),
    CONSTRAINT agent_results_method_signal_check CHECK (((method_signal)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying, 'neutral'::character varying, 'mixed'::character varying])::text[])))
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
    CONSTRAINT analysis_results_analysis_mode_check CHECK (((analysis_mode)::text = ANY ((ARRAY['full'::character varying, 'dart_only'::character varying, 'quick'::character varying])::text[]))),
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
    CONSTRAINT chk_final_signal_ml_direction CHECK (((ml_direction IS NULL) OR ((ml_direction)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying, 'neutral'::character varying, 'unknown'::character varying])::text[])))),
    CONSTRAINT chk_final_signal_publish_time CHECK (((is_published = false) OR (published_at IS NOT NULL))),
    CONSTRAINT final_signals_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (100)::numeric))),
    CONSTRAINT final_signals_consensus_score_check CHECK (((consensus_score >= (0)::numeric) AND (consensus_score <= (100)::numeric))),
    CONSTRAINT final_signals_final_score_check CHECK (((final_score >= (0)::numeric) AND (final_score <= (100)::numeric))),
    CONSTRAINT final_signals_min_plan_required_check CHECK (((min_plan_required)::text = ANY ((ARRAY['free'::character varying, 'pro'::character varying, 'premium'::character varying])::text[]))),
    CONSTRAINT final_signals_signal_check CHECK (((signal)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying, 'neutral'::character varying, 'mixed'::character varying])::text[]))),
    CONSTRAINT final_signals_source_agreement_check CHECK (((source_agreement)::text = ANY ((ARRAY['HIGH'::character varying, 'MEDIUM'::character varying, 'LOW'::character varying])::text[]))),
    CONSTRAINT final_signals_warning_level_check CHECK (((warning_level)::text = ANY ((ARRAY['NORMAL'::character varying, 'CAUTION'::character varying, 'WARNING'::character varying])::text[])))
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
    CONSTRAINT signal_events_impact_level_check CHECK (((impact_level)::text = ANY ((ARRAY['high'::character varying, 'medium'::character varying, 'low'::character varying])::text[]))),
    CONSTRAINT signal_events_signal_direction_check CHECK (((signal_direction)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying, 'neutral'::character varying, 'mixed'::character varying, 'unknown'::character varying])::text[]))),
    CONSTRAINT signal_events_source_type_check CHECK (((source_type)::text = ANY ((ARRAY['DART'::character varying, 'REPORT'::character varying, 'HIRING'::character varying, 'PATENT'::character varying, 'DATALAB'::character varying])::text[])))
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
    CONSTRAINT source_documents_reliability_level_check CHECK (((reliability_level)::text = ANY ((ARRAY['high'::character varying, 'medium'::character varying, 'low'::character varying])::text[]))),
    CONSTRAINT source_documents_source_type_check CHECK (((source_type)::text = ANY ((ARRAY['DART'::character varying, 'REPORT'::character varying, 'HIRING'::character varying, 'PATENT'::character varying, 'DATALAB'::character varying])::text[])))
);


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

-- Name: agent_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_results ALTER COLUMN id SET DEFAULT nextval('public.agent_results_id_seq'::regclass);


--

-- Name: analysis_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results ALTER COLUMN id SET DEFAULT nextval('public.analysis_results_id_seq'::regclass);


--

-- Name: final_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals ALTER COLUMN id SET DEFAULT nextval('public.final_signals_id_seq'::regclass);


--

-- Name: signal_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events ALTER COLUMN id SET DEFAULT nextval('public.signal_events_id_seq'::regclass);


--

-- Name: source_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_documents ALTER COLUMN id SET DEFAULT nextval('public.source_documents_id_seq'::regclass);


--

-- Name: stocks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stocks ALTER COLUMN id SET DEFAULT nextval('public.stocks_id_seq'::regclass);


--

-- Name: agent_results agent_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_results
    ADD CONSTRAINT agent_results_pkey PRIMARY KEY (id);


--

-- Name: analysis_results analysis_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_pkey PRIMARY KEY (id);


--

-- Name: final_signals final_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals
    ADD CONSTRAINT final_signals_pkey PRIMARY KEY (id);


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

-- Name: final_signals uq_final_signal_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.final_signals
    ADD CONSTRAINT uq_final_signal_version UNIQUE (stock_id, signal_date, run_key, version);


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

-- Name: idx_source_doc_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_source_doc_stock ON public.source_documents USING btree (stock_id, source_type, published_at DESC);


--

-- Name: idx_stocks_is_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stocks_is_target ON public.stocks USING btree (is_target) WHERE (is_target = true);


--

-- Name: uq_final_signal_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_final_signal_current ON public.final_signals USING btree (stock_id, signal_date, run_key) WHERE (is_current = true);


--

-- Name: uq_source_doc_external; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_source_doc_external ON public.source_documents USING btree (external_ref_type, external_ref_id, stock_id) WHERE (external_ref_type IS NOT NULL);


--

-- Name: final_signals trg_final_signal_current; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_final_signal_current BEFORE INSERT OR UPDATE OF is_current, stock_id, signal_date, run_key ON public.final_signals FOR EACH ROW EXECUTE FUNCTION public.set_final_signal_current();


--

-- Name: stocks trg_stocks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_stocks_updated_at BEFORE UPDATE ON public.stocks FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


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

-- Name: analysis_results analysis_results_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_results
    ADD CONSTRAINT analysis_results_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


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
