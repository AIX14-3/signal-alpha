import { apiFetch } from "./core";

export type PosthocAlignmentItem = {
  horizon: string;
  label: string;
  alignment_rate: number | null;
  confirmed_count: number;
  aligned_count: number;
  not_aligned_count: number;
  pending_count: number;
  sample_status: string;
  first_outcome_trade_date: string | null;
  last_outcome_trade_date: string | null;
  checked_at: string | null;
};

export type PosthocAlignmentResponse = {
  scope: "journal_based";
  metric_label: string;
  items: PosthocAlignmentItem[];
  methodology: {
    basis: string;
    included: string;
    excluded: string;
  };
  notice: string;
};

export async function getPosthocAlignment(): Promise<PosthocAlignmentResponse> {
  return apiFetch("/api/methodology/posthoc-alignment", { auth: "none" });
}
