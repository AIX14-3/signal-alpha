"""L6 백테스트 — forward-return 라벨/lift 채택 게이트.

소스별 base ML/DL + 학습형 메타러너의 공통 학습 타깃(forward return)을 event_study_panel 에
적재하고, 소스/그룹별 forward-return 우위(lift)를 검정한다. 모든 계산은 look-ahead 0 규율을
따른다(이벤트 당일 종가 미사용; 진입 = event_date 다음 영업일).
"""
