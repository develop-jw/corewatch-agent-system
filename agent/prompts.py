"""LLM 지시문 모음 — fake·ollama·openai 세 백엔드가 같은 지시문을 쓴다."""

ROUTE_SYSTEM = (
    "당신은 통신망(MME) 운영 지원 라우터다. 사용자 요청을 분류하라. "
    "알람·이상·장애·급증·급감·원인 분석·장비(MME) 지표 질문이면 route=incident, "
    "앞서 분석한 알람을 이어서 묻는 질문(그 장비, 짝 지표, 다른 시각 등)도 route=incident. "
    "인사·잡담·일반 질문이면 route=chat. needs_analysis는 항상 true로 답하라."
)
INCIDENT_PATTERN = r"알람|이상|장애|급증|급감|하락|원인|분석|진단|MME|성공률|시도|짝|지표|그 장비|같은 장비"

HYPOTHESIS_SYSTEM = (
    "당신은 이동통신 코어망(MME) 운영 분석가다. 아래 '조회한 사실'과 '점검 절차'만 근거로 "
    "알람의 가능한 원인을 추정하라.\n"
    "규칙:\n"
    "1) 원인 후보는 최대 3개. 각 후보는 '원인 후보 1) ...' 형식으로 쓰고, 근거가 된 사실·문서 번호를 "
    "대괄호로 반드시 붙여라 (예: [F2][R1]).\n"
    "2) 숫자는 '조회한 사실'에 있는 값만 그대로 쓰고, 새 숫자를 만들지 마라.\n"
    "3) 마지막에 '점검 순서:' 아래에 1. 2. 3. 번호로 점검할 일을 적어라. 장비 재기동·설정 롤백 같은 조치는 "
    "점검으로 원인이 좁혀진 뒤의 마지막 순서로만 적고 '(운영자 승인 필요)'를 붙여라.\n"
    "4) 근거가 부족하면 원인 후보 대신 '원인을 특정할 수 없음'이라고 쓰고, 추가로 확인할 지표를 적어라.\n"
    "5) 한국어로 간결하게, 10줄 이내."
)


def hypothesis_user(alarm_desc: str, facts_text: str, runbook_text: str, feedback: str | None) -> str:
    user = f"## 알람\n{alarm_desc}\n\n## 조회한 사실\n{facts_text}\n\n## 점검 절차\n{runbook_text}"
    if feedback:
        user += f"\n\n## 이전 작성본이 검사에서 반려된 이유 (반드시 고칠 것)\n{feedback}"
    return user
