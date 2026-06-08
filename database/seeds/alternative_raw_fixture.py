"""
alternative_raw_fixture.py
Fixture 파일 자동 생성기

실행: python database/seeds/alternative_raw_fixture.py
출력: database/seeds/alternative_raw_fixture.json
"""

import json
from datetime import datetime, timedelta

SEED_JOB_DATA = {
    # 반도체 / AI
    "삼성전자": {
        "story": "AI/HBM 인력 채용 대폭 확대 (불일치 신호)",
        "signal_strength": "high_volume",  # 많은 채용
        "jobs": [
            {
                "title": "AI/머신러닝 연구원",
                "description": "생성형 AI 및 칩 설계 최적화 담당, HBM 성능 예측 AI 모델 개발",
                "tech_stack": ["Python", "TensorFlow", "GPU Computing"],
            },
            {
                "title": "HBM(고대역폭메모리) 설계 엔지니어",
                "description": "차세대 HBM4 아키텍처 설계 및 검증, GPU 메모리 최적화",
                "tech_stack": ["VHDL", "Verilog", "Physical Design"],
            },
            {
                "title": "머신러닝 엔지니어",
                "description": "반도체 공정 데이터 분석 및 예측 모델 구축",
                "tech_stack": ["PyTorch", "Pandas", "Data Engineering"],
            },
            {
                "title": "데이터 사이언티스트",
                "description": "칩 수율 예측 및 공정 최적화 분석",
                "tech_stack": ["Python", "SQL", "Statistics"],
            },
        ],
    },
    
    "SK하이닉스": {
        "story": "HBM/어드밴스드 패키징 수시 채용 (정방향 호재)",
        "signal_strength": "steady_high",  # 꾸준한 채용
        "jobs": [
            {
                "title": "HBM 개발 엔지니어",
                "description": "고성능 HBM 제품 개발 및 테스트, 고객 협력",
                "tech_stack": ["HBM Architecture", "Testing", "C"],
            },
            {
                "title": "어드밴스드 패키징 엔지니어",
                "description": "최신 칩 패키징 기술 개발 (CoWoS, 3D 패키징)",
                "tech_stack": ["CAD", "Process Engineering", "Materials"],
            },
            {
                "title": "공정 엔지니어",
                "description": "반도체 공정 개선 및 수율 향상",
                "tech_stack": ["Process Control", "Equipment", "Python"],
            },
        ],
    },
    
    "한미반도체": {
        "story": "공장 증설 및 엔지니어 대거 채용 (정방향 호재)",
        "signal_strength": "high_volume",
        "jobs": [
            {
                "title": "공정 엔지니어",
                "description": "신규 공장 설립 시 공정 라인 구축 및 최적화",
                "tech_stack": ["Process Design", "Equipment", "Automation"],
            },
            {
                "title": "장비 엔지니어",
                "description": "반도체 제조 장비 설치 및 유지보수",
                "tech_stack": ["Equipment Control", "PLC", "Maintenance"],
            },
            {
                "title": "QA / 품질 엔지니어",
                "description": "제품 품질 보증 및 불량 분석",
                "tech_stack": ["Statistics", "SPC", "Test Systems"],
            },
        ],
    },
    
    # IT / 플랫폼
    "NAVER": {
        "story": "AI 융합 인재 제한적 채용 (정방향 평이)",
        "signal_strength": "modest",  # 제한적 채용
        "jobs": [
            {
                "title": "AI 연구원",
                "description": "검색 알고리즘 및 추천 시스템 개선",
                "tech_stack": ["Python", "ML/DL", "Large Language Models"],
            },
            {
                "title": "NLP 엔지니어",
                "description": "자연어처리 기반 검색 고도화",
                "tech_stack": ["NLP", "BERT", "Python"],
            },
        ],
    },
    
    "카카오": {
        "story": "공채 잠정 중단 및 비용 통제 (정방향 악재)",
        "signal_strength": "minimal",  # 거의 채용 없음
        "jobs": [
            {
                "title": "수시 채용: 클라우드 아키텍트",
                "description": "비용 최적화 및 인프라 고도화 (긴급 채용)",
                "tech_stack": ["AWS", "Kubernetes", "Infrastructure"],
            },
        ],
    },
    
    "크래프톤": {
        "story": "신작 기대감으로 검색량 폭발, 개발자 수시 채용 활발 (선행 신호)",
        "signal_strength": "high_activity",  # 활발한 수시 채용
        "jobs": [
            {
                "title": "게임 프로그래머 (Unreal Engine)",
                "description": "신작 개발, AAA급 게임 엔진 개발",
                "tech_stack": ["C++", "Unreal Engine", "Game Development"],
            },
            {
                "title": "AI / 게임플레이 프로그래머",
                "description": "NPC 인공지능 및 게임플레이 로직 개발",
                "tech_stack": ["C++", "AI", "Game Logic"],
            },
            {
                "title": "인프라 / DevOps 엔지니어",
                "description": "게임 서버 기반시설 고도화",
                "tech_stack": ["Go", "Kubernetes", "Cloud"],
            },
        ],
    },
    
    # 미래 모빌리티
    "현대자동차": {
        "story": "SDV(소프트웨어 중심 자동차) 개발자 상시 채용 (정방향 호재)",
        "signal_strength": "steady_high",
        "jobs": [
            {
                "title": "자동차 소프트웨어 개발자",
                "description": "자동차 제어 시스템 및 IVI(인포테인먼트) 개발",
                "tech_stack": ["C", "AUTOSAR", "Real-time OS"],
            },
            {
                "title": "자율주행 엔지니어",
                "description": "자율주행 알고리즘 및 센서 퓨전 개발",
                "tech_stack": ["Python", "C++", "Sensor Fusion", "ROS"],
            },
            {
                "title": "클라우드 아키텍트",
                "description": "자동차 커넥티드 서비스 백엔드 구축",
                "tech_stack": ["AWS", "Microservices", "Python"],
            },
        ],
    },
    
    "기아": {
        "story": "전기차 캐즘으로 관련 채용 및 트렌드 소폭 정체 (불일치 신호)",
        "signal_strength": "low",  # 정체
        "jobs": [
            {
                "title": "배터리 관리 시스템(BMS) 엔지니어",
                "description": "전기차 배터리 안전 및 효율성 관리",
                "tech_stack": ["Embedded C", "Power Electronics", "Battery Tech"],
            },
        ],
    },
    
    "HL만도": {
        "story": "자율주행 부문 인력 채용 지속 증가 (정방향 호재)",
        "signal_strength": "growing",  # 증가
        "jobs": [
            {
                "title": "자율주행 센서 엔지니어",
                "description": "LiDAR, Radar, 카메라 통합 센서 시스템 개발",
                "tech_stack": ["C++", "Sensor Integration", "Signal Processing"],
            },
            {
                "title": "시스템 통합 엔지니어",
                "description": "자율주행 ECU 통합 및 검증",
                "tech_stack": ["AUTOSAR", "Systems Engineering", "Testing"],
            },
        ],
    },
    
    # 엔터 / 콘텐츠
    "HYBE": {
        "story": "이슈로 인한 검색량 폭발 (노이즈), 실적 및 채용은 정상 유지",
        "signal_strength": "normal",  # 정상 채용 (검색량은 노이즈)
        "jobs": [
            {
                "title": "A&R (Artist & Repertoire)",
                "description": "신인 발굴 및 곡 프로덕션 감수",
                "tech_stack": ["Music Production", "Industry Network"],
            },
            {
                "title": "음악 프로듀서",
                "description": "K-POP 곡 제작 및 편곡",
                "tech_stack": ["Logic Pro", "Ableton", "Mixing"],
            },
        ],
    },
    
    "SM엔터테인먼트": {
        "story": "실적 둔화, 아티스트 활동 공백기 검색량 저하, 채용 축소 (정방향 악재)",
        "signal_strength": "minimal",
        "jobs": [
            {
                "title": "음악 프로듀서",
                "description": "기존 아티스트 신곡 프로덕션",
                "tech_stack": ["Music Production", "Composition"],
            },
        ],
    },
    
    "스튜디오드래곤": {
        "story": "OTT 라인업 축소로 채용 동결 (정방향 평이)",
        "signal_strength": "minimal",
        "jobs": [
            {
                "title": "드라마 프로듀서",
                "description": "제한적 규모 드라마 제작",
                "tech_stack": ["Production Management", "Industry"],
            },
        ],
    },
    
    # 바이오 / 헬스
    "삼성바이오로직스": {
        "story": "공장 증설 공시, 리포트 목표가 상향, 대규모 생산 인력 채용 (정방향 호재)",
        "signal_strength": "high_volume",
        "jobs": [
            {
                "title": "생산 공정 관리자",
                "description": "신규 공장 GMP 공정 관리 및 최적화",
                "tech_stack": ["GMP", "Process Management", "Quality"],
            },
            {
                "title": "품질 관리 엔지니어",
                "description": "생물의약품 품질 검증 및 규제 준수",
                "tech_stack": ["QA", "Analytics", "Regulatory"],
            },
            {
                "title": "설비 엔지니어",
                "description": "바이오 생산 설비 설치 및 유지보수",
                "tech_stack": ["Biopharmaceutical Equipment", "Maintenance"],
            },
        ],
    },
    
    "셀트리온": {
        "story": "합병 이후 통합 채용 진행, 신약 승인 트렌드 상승 (선행 신호)",
        "signal_strength": "high_volume",
        "jobs": [
            {
                "title": "신약 임상 연구원",
                "description": "신약 임상시험 설계 및 모니터링",
                "tech_stack": ["Clinical Development", "Regulatory", "Statistics"],
            },
            {
                "title": "제조 공정 엔지니어",
                "description": "바이오 신약 제조 공정 확대",
                "tech_stack": ["Biotech", "Manufacturing", "Process"],
            },
            {
                "title": "규제 과학자",
                "description": "신약 승인 관련 규제 전략 수립",
                "tech_stack": ["Regulatory Affairs", "Compliance"],
            },
        ],
    },
    
    "유한양행": {
        "story": "레이저티닙 글로벌 성과로 검색량 및 연구원 채용 증가 (정방향 호재)",
        "signal_strength": "high_volume",
        "jobs": [
            {
                "title": "신약 개발 연구원",
                "description": "신약 임상 개발 및 데이터 분석",
                "tech_stack": ["Drug Development", "Chemistry", "Analytics"],
            },
            {
                "title": "임상 시험 코디네이터",
                "description": "레이저티닙 등 신약 임상시험 운영",
                "tech_stack": ["Clinical Trials", "Project Management"],
            },
            {
                "title": "의약품 마케팅 매니저",
                "description": "글로벌 신약 런칭 및 마케팅 전략",
                "tech_stack": ["Pharma Marketing", "Strategic Planning"],
            },
        ],
    },
}

def generate_fixture():
    """Fixture JSON 생성"""
    seed_job_data = []
    base_date = datetime(2026, 6, 7)
    
    for company_name, company_info in SEED_JOB_DATA.items():
        story = company_info["story"]
        signal_strength = company_info["signal_strength"]
        jobs = company_info["jobs"]
        
        for idx, job in enumerate(jobs):
            closing_date = (base_date + timedelta(days=30 + idx * 2)).isoformat()
            
            # 1. job_link를 먼저 변수에 할당합니다.
            job_link = f"https://seed-job.mock/{company_name.replace(' ', '_')}/{idx}"
            
            # 2. 할당된 job_link를 가공하여 고유한 unique_key를 생성합니다.
            safe_link = job_link.replace("https://", "").replace("/", "_")
            unique_key = f"SEED_JOB_{safe_link}"
            
            seed_job_data.append({
                "source_type": "SEED_JOB",
                "unique_key": unique_key,  # ★ JSON 데이터 자체에 고유 키를 박아줍니다.
                "company_name": company_name,
                "job_title": job["title"],
                "job_description": job["description"],
                "closing_date": closing_date,
                "tech_stack": job["tech_stack"],
                "story": story,
                "signal_strength": signal_strength,
                "posting_date": base_date.isoformat(),
                "job_link": job_link,
            })
    
    fixture = {"seed_job_data": seed_job_data}
    
    # 파일로 저장
    with open('alternative_raw_fixture.json', 'w', encoding='utf-8') as f:
        json.dump(fixture, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Fixture 생성 완료: {len(seed_job_data)}개 직무")
    return fixture

if __name__ == "__main__":
    generate_fixture()