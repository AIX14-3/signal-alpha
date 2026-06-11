import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

PARSE_PROMPT = """
당신은 증권사 리포트 분석 전문가입니다.
아래 리포트 텍스트에서 다음 3가지를 추출하세요.

추출 항목:
1. target_price: 목표주가 (숫자만, 없으면 null)
2. opinion: 투자의견 (buy / neutral / sell / unknown 중 하나)
   - 매수, BUY, 강력매수 → "buy"
   - 중립, HOLD, 보유 → "neutral"
   - 매도, SELL → "sell"
   - 불명확 → "unknown"
3. key_rationale: 핵심 투자 근거 (2~4문장 요약, 한국어)
   - 왜 그 투자의견과 목표주가를 제시하는지
   - 실적·업황·밸류에이션 근거 중심
   - 단순 수치 나열 금지, 판단 근거 중심

반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트 없이 JSON만 출력하세요.

{
  "target_price": 숫자 또는 null,
  "opinion": "buy" | "neutral" | "sell" | "unknown",
  "key_rationale": "핵심 근거 2~4문장"
}
"""


def get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-proj-your-key"):
        raise ValueError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            "프로젝트 루트의 .env 파일에 실제 API 키를 입력하세요."
        )
    return OpenAI(api_key=api_key)


def parse_report(text: str, model: str = "gpt-4o-mini") -> dict:
    """리포트 텍스트에서 목표주가·투자의견·핵심 근거 추출"""
    truncated = text[:3000]
    client = get_client()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PARSE_PROMPT},
                {"role": "user", "content": f"리포트 텍스트:\n\n{truncated}"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"  [파싱 오류] {e}")
        return {
            "target_price": None,
            "opinion": "unknown",
            "key_rationale": "",
        }
