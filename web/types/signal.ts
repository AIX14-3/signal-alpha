export type Direction = 'POSITIVE' | 'NEGATIVE' | 'MIXED';
export type Consistency = 'HIGH' | 'MED' | 'LOW';
export type AgentKey = 'dart' | 'report' | 'alt' | 'debate';
export type AgentProgress = 'idle' | 'running' | 'completed';
export type PageId =
  | 'home'
  | 'features'
  | 'agents'
  | 'stories'
  | 'journal'
  | 'quote'
  | 'reports'
  | 'running'
  | 'main';
export type StoryFilter = 'all' | 'positive' | 'mixed' | 'negative';
export type QuotePeriod = '1D' | '1W' | '1M' | '6M' | 'YTD' | '1Y' | '5Y';
export type DrilldownKey = 'dart' | 'report' | 'alt';

export interface QuoteData {
  nameEn: string;
  asOf: string;
  price: number;
  change: number;
  changePct: number;
  open: number;
  high: number;
  low: number;
  prevClose: number;
  volume: string;
  marketCap: string;
  per: number;
  week52High: number;
  week52Low: number;
  sourcesAgree: string;
  intraday: number[];
}

export interface Disclosure {
  date: string;
  type: string;
  title: string;
  impact: Direction;
  link: string;
}

export interface DartData {
  score: number;
  direction: Direction;
  disclosures: Disclosure[];
  surprise: { actual: number; consensus: number; unit: string };
}

export interface ReportOpinion {
  firm: string;
  rating: string;
  target: string;
  date: string;
}

export interface ReportData {
  score: number;
  direction: Direction;
  opinions: ReportOpinion[];
  conflict: boolean;
  trend: number[];
}

export interface AltData {
  score: number;
  direction: Direction;
  hiring: { pct: number };
  patent: { growth: number };
  datalab: { trend: number[] };
}

export interface DebateData {
  bull: string[];
  bear: string[];
  judge: string;
}

export interface StockData {
  name: string;
  code: string;
  score: number;
  direction: Direction;
  consistency: Consistency;
  summary: string;
  quote: QuoteData;
  dart: DartData;
  report: ReportData;
  alt: AltData;
  debate: DebateData;
}

export interface StoryItem {
  name: string;
  code: string;
  score: number;
  direction: Direction;
  growth: number;
  consistency: Consistency;
  summary: string;
  dart: number;
  report: number;
  alt: number;
  timeline: { label: string; time: string; up: boolean | null }[];
}

export interface NavItem {
  label: string;
  pageId: PageId;
  href: string;
}

export interface MarketIndex {
  name: string;
  value: string;
  chg: number;
  up: boolean;
  spark: number[];
}

export interface DiffCard {
  icon: string;
  title: string;
  desc: string;
}

export interface SourceRow {
  name: string;
  type: string;
  method: string;
  cost: string;
  trust: number;
}

export interface AgentHubItem {
  label: string;
  icon: string;
  role: string;
  methods: string[];
  logic: string[];
  mock: { score: number; signal: string; field: string };
  json: string;
  owner: string;
}

export interface MockAlert {
  id: number;
  name: string;
  msg: string;
  time: string;
  read: boolean;
}

export interface JournalEntry {
  id: number;
  name: string;
  code: string;
  score: number;
  direction: Direction;
  date: string;
  trade: boolean;
  note: string;
}
