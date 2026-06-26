-- 0004_backend_baseline.sql
-- target: backend
-- ============================================================================
-- BACKEND(회원·구독·결제·관리자·유저콘텐츠) 테이블. 백엔드 DB 에만 적용.
-- ※ rebaseline.py 가 생성. cross-DB FK(→collection)는 제거됨.
-- ============================================================================

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
    CONSTRAINT payments_status_check CHECK (((status)::text = ANY ((ARRAY['paid'::character varying, 'cancelled'::character varying, 'partial_cancelled'::character varying, 'failed'::character varying])::text[])))
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
    CONSTRAINT portone_verifications_verification_type_check CHECK (((verification_type)::text = ANY ((ARRAY['identity'::character varying, 'payment'::character varying])::text[])))
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
    CONSTRAINT report_issuances_issued_via_check CHECK (((issued_via)::text = ANY ((ARRAY['free'::character varying, 'subscription'::character varying])::text[])))
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

-- Name: signal_journals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_journals (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    final_signal_id bigint,
    stock_id bigint NOT NULL,
    user_view character varying(20) NOT NULL,
    user_memo text,
    decision_type character varying(20),
    decision_reason text,
    signal_score_at_time numeric(5,2),
    signal_value_at_time character varying(10),
    price_at_time numeric(12,2),
    source_agreement_at_time character varying(10),
    outcome_price numeric(12,2),
    outcome_change_pct numeric(6,2),
    outcome_checked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT signal_journals_user_view_check CHECK (((user_view)::text = ANY ((ARRAY['watch'::character varying, 'research_more'::character varying, 'not_relevant'::character varying])::text[])))
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
    CONSTRAINT signal_subscriptions_billing_cycle_check CHECK ((((billing_cycle)::text = ANY ((ARRAY['monthly'::character varying, 'yearly'::character varying])::text[])) OR (billing_cycle IS NULL))),
    CONSTRAINT signal_subscriptions_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'expired'::character varying, 'cancelled'::character varying, 'trial'::character varying])::text[])))
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
    CONSTRAINT social_accounts_provider_check CHECK (((provider)::text = ANY ((ARRAY['google'::character varying, 'kakao'::character varying, 'naver'::character varying])::text[])))
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
    CONSTRAINT chk_users_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'suspended'::character varying, 'deleted'::character varying])::text[])))
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

-- Name: payments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments ALTER COLUMN id SET DEFAULT nextval('public.payments_id_seq'::regclass);


--

-- Name: portone_verifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portone_verifications ALTER COLUMN id SET DEFAULT nextval('public.portone_verifications_id_seq'::regclass);


--

-- Name: report_issuances id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances ALTER COLUMN id SET DEFAULT nextval('public.report_issuances_id_seq'::regclass);


--

-- Name: signal_journals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journals ALTER COLUMN id SET DEFAULT nextval('public.signal_journals_id_seq'::regclass);


--

-- Name: signal_subscriptions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_subscriptions ALTER COLUMN id SET DEFAULT nextval('public.signal_subscriptions_id_seq'::regclass);


--

-- Name: social_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts ALTER COLUMN id SET DEFAULT nextval('public.social_accounts_id_seq'::regclass);


--

-- Name: subscription_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_plans ALTER COLUMN id SET DEFAULT nextval('public.subscription_plans_id_seq'::regclass);


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

-- Name: watchlists id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists ALTER COLUMN id SET DEFAULT nextval('public.watchlists_id_seq'::regclass);


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

-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


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

-- Name: report_issuances report_issuances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances
    ADD CONSTRAINT report_issuances_pkey PRIMARY KEY (id);


--

-- Name: signal_journals signal_journals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_journals
    ADD CONSTRAINT signal_journals_pkey PRIMARY KEY (id);


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

-- Name: terms_agreements terms_agreements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.terms_agreements
    ADD CONSTRAINT terms_agreements_pkey PRIMARY KEY (id);


--

-- Name: user_signal_reads uq_read; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_signal_reads
    ADD CONSTRAINT uq_read UNIQUE (user_id, final_signal_id);


--

-- Name: report_issuances uq_report_issuance; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_issuances
    ADD CONSTRAINT uq_report_issuance UNIQUE (user_id, final_signal_id);


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

-- Name: watchlists watchlists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_pkey PRIMARY KEY (id);


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

-- Name: idx_subscription_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_subscription_active ON public.signal_subscriptions USING btree (user_id) WHERE ((status)::text = 'active'::text);


--

-- Name: idx_terms_agreements_user_agreed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_terms_agreements_user_agreed_at ON public.terms_agreements USING btree (user_id, agreed_at DESC);


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

-- Name: uq_users_phone_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_users_phone_active ON public.users USING btree (phone) WHERE ((phone IS NOT NULL) AND (deleted_at IS NULL));


--

-- Name: signal_journals trg_signal_journals_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_signal_journals_updated_at BEFORE UPDATE ON public.signal_journals FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--

-- Name: signal_subscriptions trg_signal_subscriptions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_signal_subscriptions_updated_at BEFORE UPDATE ON public.signal_subscriptions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


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

-- Name: payments payments_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.signal_subscriptions(id);


--

-- Name: payments payments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--

-- Name: portone_verifications portone_verifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portone_verifications
    ADD CONSTRAINT portone_verifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


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
    ADD CONSTRAINT signal_journals_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--

-- Name: signal_subscriptions signal_subscriptions_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_subscriptions
    ADD CONSTRAINT signal_subscriptions_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.subscription_plans(id);


--

-- Name: signal_subscriptions signal_subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_subscriptions
    ADD CONSTRAINT signal_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--

-- Name: social_accounts social_accounts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts
    ADD CONSTRAINT social_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--

-- Name: terms_agreements terms_agreements_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.terms_agreements
    ADD CONSTRAINT terms_agreements_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


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
    ADD CONSTRAINT user_signal_reads_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--

-- Name: watchlists watchlists_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);


--

-- Name: watchlists watchlists_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--
