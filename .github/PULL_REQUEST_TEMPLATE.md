<!--
  이 템플릿은 PR을 열면 자동으로 채워집니다.
  PR 규칙이 어렵다면 먼저 docs/pr-guide.md(쉬운 말 가이드)를 읽어주세요.
  주석(<!-- -->)은 제출 후 보이지 않으니 지우지 않아도 됩니다.
-->

## 📝 설명 (Description)
<!-- 이 PR이 무엇을, 왜 하는지 한두 문장으로 적어주세요. -->


## 🔧 변경 종류 (Type of Change)
<!-- 해당하는 항목에 [x] 표시 -->
- [ ] ✨ 새 기능 (feat)
- [ ] 🐛 버그 수정 (fix)
- [ ] ♻️ 리팩터링 (refactor) — 동작은 그대로, 코드만 정리
- [ ] 📚 문서 (docs)
- [ ] 🛠️ 설정 / CI·CD (chore)
- [ ] ⚡ 성능 개선 (perf)


## 📋 변경 내용 (Changes Made)
<!-- 구체적으로 무엇을 바꿨는지 목록으로. -->
-
-


## ✅ 테스트 방법 (How to Test)
<!-- 리뷰어가 직접 확인할 수 있게 단계별로. -->
1.
2.


## 🔒 Signal α 안전 체크리스트 (필수)
<!-- 자세한 설명은 docs/pr-guide.md 참고 -->
- [ ] 화면·API·프롬프트에 **투자 추천처럼 들리는 말**이 없다 (사세요/추천/오를 것 등)
- [ ] `confidence` 라는 필드명을 쓰지 않았다 (대신 consensus_score 등)
- [ ] **수집(Collector) / 분석(Analyzer) / 조율(Orchestrator)** 역할을 섞지 않았다
- [ ] 저장 위치를 지켰다 (raw_evidence / source_results / signal_snapshots)
- [ ] 외부 API가 실패해도 화면이 죽지 않는다 (fallback / needs_review 처리)


## 🧰 공통 체크리스트 (Checklist)
- [ ] 한 PR에 한 가지 일만 담았다
- [ ] 셀프 리뷰를 한 번 했다
- [ ] 로컬에서 실행/테스트가 통과한다
- [ ] 필요한 문서(README 등)를 업데이트했다
- [ ] 새 경고나 에러가 생기지 않았다


## 🔗 관련 이슈 (Related Issues)
<!-- 자동으로 닫으려면 'Closes #번호' 형식으로. -->
Closes #


## 📸 스크린샷 (Screenshots)
<!-- UI 변경이 있으면 첨부. 없으면 비워도 됩니다. -->
