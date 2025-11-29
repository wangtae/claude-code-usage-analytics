# CLAUDE.md - Claude Code Usage Analytics

이 문서는 AI 어시스턴트(Claude)가 이 프로젝트를 이해하고 효과적으로 작업하기 위한 컨텍스트를 제공합니다.

## 프로젝트 개요

**이름**: claude-code-usage-analytics
**버전**: 1.8.1
**목적**: Claude Code 사용량을 추적, 시각화하고 여러 기기 간에 동기화하는 CLI TUI 대시보드

### 핵심 기능
- Claude Code JSONL 로그 파싱 및 토큰 사용량 추적
- 실시간 대시보드 (일별/주별/월별/연간 뷰)
- GitHub Gist를 통한 멀티 디바이스 동기화
- 사용량 제한 추적 (세션, 주간, 모델별)
- 비용 계산 (모델별 가격 적용)

## 디렉토리 구조

```
src/
├── cli.py                    # 메인 CLI 진입점 (typer 기반)
├── aggregation/              # 데이터 집계 레이어
│   ├── daily_stats.py        # 일일 통계 집계
│   └── usage_limits.py       # 사용량 제한 계산
├── commands/                 # CLI 명령어들
│   ├── usage.py              # 메인 대시보드 (키보드 내비게이션)
│   ├── heatmap.py            # GitHub 스타일 히트맵
│   ├── gist_cmd.py           # Gist 동기화 명령어
│   └── settings.py           # 설정 메뉴
├── config/                   # 설정 관리
│   ├── user_config.py        # 사용자 설정 저장/로드
│   └── defaults.py           # 기본값 정의
├── data/                     # 데이터 파싱
│   └── jsonl_parser.py       # Claude Code JSONL 파싱
├── models/                   # 데이터 모델
│   ├── usage_record.py       # UsageRecord, TokenUsage 데이터클래스
│   └── pricing.py            # 모델별 가격 정보
├── storage/                  # 데이터베이스 레이어
│   ├── snapshot_db.py        # 메인 SQLite DB (1400+ lines)
│   └── machines_db.py        # 기기 메타데이터 DB
├── sync/                     # GitHub Gist 동기화
│   ├── sync_manager.py       # 동기화 오케스트레이션
│   ├── gist_client.py        # GitHub API 클라이언트
│   ├── json_export.py        # JSON 내보내기 (읽기 전용)
│   ├── json_import.py        # JSON 가져오기
│   └── manifest.py           # 동기화 매니페스트
├── utils/                    # 유틸리티
│   └── timezone.py           # 타임존 처리
└── visualization/            # TUI 렌더링
    └── dashboard.py          # 대시보드 렌더링 엔진
```

## 데이터베이스 스키마

### 메인 DB: `usage_history_{machine_name}.db`

**핵심 테이블:**

1. **usage_records** - 상세 트랜잭션 로그
   - UNIQUE(session_id, message_uuid)로 중복 방지
   - 모든 user prompt와 assistant response 저장

2. **daily_snapshots** - 일일 집계 데이터
   - date(PK), total_tokens, input_tokens, output_tokens 등
   - 빠른 집계 쿼리용 (전체 토큰 합계는 여기서)

3. **limits_snapshots** - 사용량 제한 추적
   - session_pct, week_pct, sonnet_pct 등 저장

4. **device_usage_aggregates** - 기기별 일일 집계
5. **device_monthly_stats** - 기기별 월간 집계 (성능 최적화)

### 보조 DB: `machines.db`
- 동기화된 모든 기기 목록 관리

## 주요 CLI 명령어

```bash
ccu                          # 메인 대시보드 (기본)
ccu --anon                   # 프로젝트명 익명화
ccu heatmap                  # GitHub 스타일 히트맵
ccu config show              # 설정 표시
ccu gist push [--force]      # Gist에 푸시
ccu gist pull                # Gist에서 풀
ccu gist status              # 동기화 상태
```

**대시보드 키보드 단축키:**
- `u` - Usage 모드 (제한 추적)
- `w` - Weekly 뷰
- `m` - Monthly 뷰
- `y` - Yearly 뷰
- `h` - Heatmap
- `d` - Devices (기기별)
- `s` - Settings
- `q/Esc` - 종료

## 구현 시 주의사항

### 1. 데이터 저장 구조

**중요**: `usage_records`와 `daily_snapshots`의 역할 이해
- `usage_records`: 최근 데이터만 저장 (오래된 데이터는 삭제됨)
- `daily_snapshots`: 전체 기간의 집계 데이터 (절대 삭제 안 됨)
- 전체 토큰 합계는 `daily_snapshots`에서 계산해야 함

```python
# 올바른 전체 토큰 조회
SELECT SUM(total_tokens) FROM daily_snapshots

# 잘못된 방법 (최근 데이터만 있음)
SELECT SUM(total_tokens) FROM usage_records
```

### 2. Gist 동기화 시 주의사항

**export/import 시 daily_snapshots 포함 필수**:
```python
# json_export.py에서 daily_snapshots 내보내기
result["daily_snapshots"] = [...]

# json_import.py에서 daily_snapshots 가져오기 (UPSERT)
INSERT OR REPLACE INTO daily_snapshots (...)
```

**--force 플래그 동작** (v1.8.1+):
- `--force`: 전체 데이터 export + conflict check 스킵
- `--export-all`: 전체 데이터 export (conflict check 수행)

### 3. 기기 DB 경로

각 기기별로 별도의 DB 파일 사용:
```python
# 올바른 경로 가져오기
from src.storage.snapshot_db import get_current_machine_db_path
db_path = get_current_machine_db_path()  # usage_history_{machine_name}.db

# 모든 기기 DB 경로
from src.storage.snapshot_db import get_all_machine_db_paths
paths = get_all_machine_db_paths()  # [(machine_name, path), ...]
```

### 4. 동기화 매니페스트

`manifest.json` 구조:
```json
{
  "version": "1.0",
  "machines": [{
    "machine_name": "...",
    "data_files": ["file1.json", "file2.json"],  // chunked 파일들
    "current_file": "...",  // 호환성용
    "total_records": 12345
  }]
}
```

### 5. 데이터 청킹

대용량 데이터는 연도별로 분할:
- `usage_data_machine_2024.json`
- `usage_data_machine_2025.json`

`daily_snapshots`는 첫 번째 청크에만 포함.

### 6. 타임존 처리

모든 날짜/시간은 로컬 타임존 기준:
```python
from src.utils.timezone import get_local_date
date_key = get_local_date(timestamp)
```

### 7. 캐시 시스템

`snapshot_db.py`의 캐시:
- Device stats: 60초 TTL
- Device records: 6시간 TTL (pickle 저장)

캐시 무효화가 필요한 경우:
```python
_device_stats_cache.clear()
```

### 8. 마이그레이션

새 버전에서 DB 스키마 변경 시:
1. `src/migrations/versions/` 에 새 마이그레이션 파일 생성
2. 버전 형식: `v{major}_{minor}_{patch}_{description}.py`
3. `apply()` 함수 구현

## 테스트

```bash
python3 -m pytest tests/ -v
```

주요 테스트:
- `tests/test_sync/` - 동기화 테스트
- `tests/test_storage/` - 데이터베이스 테스트

## 디버깅 팁

### DB 직접 조회
```bash
python3 -c "
import sqlite3
from src.storage.snapshot_db import get_current_machine_db_path
conn = sqlite3.connect(get_current_machine_db_path())
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM daily_snapshots')
print(cursor.fetchone())
"
```

### Gist export 테스트
```python
from src.sync.json_export import export_to_json
data = export_to_json(since_date=None)  # 전체 export
print(f"Records: {len(data['records'])}")
print(f"Snapshots: {len(data.get('daily_snapshots', []))}")
```

## 보안 고려사항

1. **읽기 전용 export**: JSON export는 `?mode=ro`로 DB 열기
2. **토큰 저장**: 시스템 keyring 우선, 파일 fallback
3. **~/.claude/ 절대 수정 금지**: 원본 Claude Code 데이터 보호

## 자주 발생하는 버그

### 1. daily_snapshots 누락
**증상**: 전체 토큰 수가 맞지 않음
**원인**: export/import 시 daily_snapshots 미포함
**해결**: json_export.py, json_import.py 확인

### 2. --force가 incremental export
**증상**: `ccu gist push --force`가 최근 데이터만 업로드
**원인**: force 플래그가 since_date에 영향 안 줌
**해결**: v1.8.1에서 수정됨 (`force=export_all or force`)

### 3. machines.db fallback
**증상**: 새 기기에서 다른 기기 데이터 안 보임
**원인**: machines.db 없을 때 현재 기기만 반환
**해결**: `get_all_machine_db_paths()`에서 디스크 스캔 fallback

## 버전 히스토리

- **v1.8.1**: --force 플래그가 전체 export 트리거하도록 수정
- **v1.8.0**: daily_snapshots export/import 추가
- **v1.7.9**: 백업 기본값 비활성화
- **v1.7.7**: 동기화 상태 확인 기능
- **v1.7.6**: chunked 파일 지원

## 연락처

Issues: https://github.com/wangtae/claude-code-usage-analytics/issues
