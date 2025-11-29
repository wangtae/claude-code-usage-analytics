# CLAUDE.md - Claude Code Usage Analytics

이 문서는 AI 어시스턴트(Claude)가 이 프로젝트를 이해하고 효과적으로 작업하기 위한 컨텍스트를 제공합니다.

## 프로젝트 개요

**이름**: claude-code-usage-analytics
**버전**: 1.8.2
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

## 데이터 견고성 원칙 (핵심!)

**이 프로젝트의 가장 중요한 목표는 데이터 견고성입니다. 모든 구현은 아래 원칙을 준수해야 합니다.**

### 데이터 소스 계층

```
┌─────────────────────────────────────────────────────────────┐
│  1. 원본 데이터: ~/.claude/sessions/*.jsonl (Claude Code)   │
│     - 각 기기의 실제 사용량 원본                            │
│     - 기기 포맷 시 소실됨                                   │
└─────────────────────────────────────────────────────────────┘
                          ↓ ccu 실행 시 파싱
┌─────────────────────────────────────────────────────────────┐
│  2. 로컬 DB: ~/.claude/usage/usage_history_{machine}.db     │
│     - usage_records: 최근 상세 데이터                       │
│     - daily_snapshots: 전체 기간 집계 (핵심!)               │
└─────────────────────────────────────────────────────────────┘
                          ↓ gist push
┌─────────────────────────────────────────────────────────────┐
│  3. Gist 저장소: 모든 기기의 데이터 백업                    │
│     - 기기 포맷 후에도 복원 가능                            │
│     - 멀티 디바이스 동기화의 중심                           │
└─────────────────────────────────────────────────────────────┘
```

### 핵심 원칙

#### 원칙 1: 원본 데이터 보존
- `~/.claude/` 폴더의 원본 JSONL은 **절대 수정/삭제 금지**
- 읽기 전용으로만 접근

#### 원칙 2: 포맷 후 복원
- 기기 포맷 후 gist pull만으로 과거 데이터 전체 복원 가능해야 함
- `daily_snapshots`가 전체 히스토리를 보존하므로 반드시 export/import에 포함

#### 원칙 3: 로컬 ⊆ Gist
- 로컬 데이터는 Gist 데이터의 **부분집합**일 수 있음
- Gist가 더 많은 데이터를 가질 수 있음 (다른 기기 데이터, 포맷 전 데이터)

#### 원칙 4: 중복 시 원본 우선
- `usage_records`: session_id + message_uuid로 중복 방지 (UNIQUE 제약)
- `daily_snapshots`: 동일 날짜 충돌 시 **더 큰 값**이 정확할 가능성 높음
  - 로컬에서 새로 계산한 값이 더 정확할 수 있음
  - 현재는 UPSERT로 최신 값 사용

#### 원칙 5: 멀티 디바이스 투명성
- 사용자가 어느 기기에서 `ccu`를 실행하든 **전체 데이터를 볼 수 있어야 함**
- 각 기기별 데이터는 별도 DB 파일로 관리 (`usage_history_{machine}.db`)

#### 원칙 6: 자동 동기화 (목표)
- **현재**: `ccu gist push/pull`은 수동 명령
- **목표**: `ccu` 실행만으로 자동 동기화 (push/pull 불필요)
- `ccu gist push --force` 같은 수동 명령은 **버그 복구용**으로만 사용

#### 원칙 7: 버전 업그레이드 자동 수정
- 과거 버그로 인한 데이터 불일치는 **버전 업그레이드 시 자동 수정**
- 마이그레이션 시스템을 통해 기존 사용자 데이터 자동 복구
- 사용자가 새 버전 설치만 해도 문제 해결되어야 함

### 구현 시 체크리스트

새 기능/버그 수정 시 반드시 확인:

- [ ] 원본 `~/.claude/` 데이터를 수정하지 않는가?
- [ ] 포맷 후 gist pull로 데이터 복원이 가능한가?
- [ ] `daily_snapshots`가 export/import에 포함되는가?
- [ ] 멀티 디바이스에서 동일한 데이터가 표시되는가?
- [ ] 데이터 충돌 시 적절한 병합 전략이 있는가?
- [ ] 버그 수정 시 마이그레이션으로 기존 데이터 복구가 필요한가?

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

### 8. 마이그레이션 시스템 (중요!)

**버전 업데이트 시 기존 사용자 데이터 마이그레이션이 필요한 경우 반드시 마이그레이션을 구현해야 합니다.**

#### 마이그레이션이 필요한 경우
- DB 스키마 변경 (테이블/컬럼 추가/수정/삭제)
- 설정 파일 구조 변경
- 데이터 포맷 변경
- Gist manifest 구조 변경

#### 마이그레이션이 필요 없는 경우
- 단순 버그 수정
- UI/출력 변경
- 새로운 기능 추가 (기존 데이터에 영향 없음)
- 코드 리팩토링

#### 마이그레이션 시스템 구조

```
src/migrations/
├── __init__.py           # run_migrations, get_migration_status export
├── base.py               # Migration 베이스 클래스
├── runner.py             # 마이그레이션 실행 엔진
└── versions/
    ├── __init__.py       # ALL_MIGRATIONS 리스트 (여기에 등록!)
    ├── v1_7_6_manifest_data_files.py
    ├── v1_7_7_sync_check.py
    └── v1_7_9_disable_backup.py
```

#### 마이그레이션 동작 방식
1. **CLI 시작 시 자동 실행** (`src/cli.py:176-183`)
2. `version_info.db`에서 저장된 버전과 현재 버전 비교
3. 적용되지 않은 마이그레이션을 버전 순서대로 실행
4. 실행 결과를 `migration_history` 테이블에 기록

#### 새 마이그레이션 생성 방법

**1단계: 마이그레이션 파일 생성**

`src/migrations/versions/v{major}_{minor}_{patch}_{description}.py`:

```python
"""
Migration: 설명
Version: 1.8.2
"""

from src.migrations.base import Migration, MigrationResult


class MyFeatureMigration(Migration):
    """마이그레이션 설명."""

    version = "1.8.2"  # pyproject.toml 버전과 일치
    name = "Add new feature table"
    description = "새 기능을 위한 테이블 추가"

    def check_required(self) -> bool:
        """
        마이그레이션 실행 여부 확인.

        기본값은 True (항상 실행).
        조건부 실행이 필요한 경우 오버라이드.
        """
        # 예: 특정 테이블이 없을 때만 실행
        # return not self._table_exists("new_table")
        return True

    def up(self) -> MigrationResult:
        """
        마이그레이션 실행 (업그레이드).

        Returns:
            MigrationResult(success=True/False, message="...", error="...")
        """
        try:
            import sqlite3
            from src.storage.snapshot_db import get_current_machine_db_path

            db_path = get_current_machine_db_path()
            conn = sqlite3.connect(db_path, timeout=30.0)
            cursor = conn.cursor()

            # 스키마 변경 예시
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS new_feature (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            # 또는 컬럼 추가
            # cursor.execute("ALTER TABLE existing_table ADD COLUMN new_col TEXT")

            conn.commit()
            conn.close()

            return MigrationResult(
                success=True,
                message="New feature table created"
            )

        except Exception as e:
            return MigrationResult(
                success=False,
                message="Migration failed",
                error=str(e)
            )

    def down(self) -> MigrationResult:
        """
        마이그레이션 롤백 (다운그레이드).

        현재 자동 롤백은 지원하지 않지만, 수동 복구용으로 구현 권장.
        """
        try:
            # 롤백 로직
            return MigrationResult(
                success=True,
                message="Rolled back successfully"
            )
        except Exception as e:
            return MigrationResult(
                success=False,
                message="Rollback failed",
                error=str(e)
            )
```

**2단계: 마이그레이션 등록**

`src/migrations/versions/__init__.py` 수정:

```python
# 새 마이그레이션 import 추가
from src.migrations.versions.v1_8_2_my_feature import MyFeatureMigration

# ALL_MIGRATIONS 리스트에 추가 (순서 무관, 버전으로 자동 정렬)
ALL_MIGRATIONS: list[Type[Migration]] = [
    ManifestDataFilesMigration,
    SyncCheckMigration,
    DisableBackupMigration,
    MyFeatureMigration,  # 새로 추가
]
```

**3단계: 버전 업데이트**

`pyproject.toml`의 version을 마이그레이션 버전과 일치시킴:
```toml
version = "1.8.2"
```

#### 마이그레이션 테스트

```python
# 마이그레이션 상태 확인
from src.migrations import get_migration_status
status = get_migration_status()
print(f"Current: {status['current_version']}")
print(f"Stored: {status['stored_version']}")
print(f"Pending: {status['pending_count']}")

# 수동 마이그레이션 실행
from src.migrations import run_migrations
from rich.console import Console
result = run_migrations(console=Console())
print(result)
```

#### 주의사항

1. **버전 형식**: 반드시 `X.Y.Z` 형식 (예: "1.8.2")
2. **파일명 형식**: `v{major}_{minor}_{patch}_{description}.py`
3. **멱등성**: 마이그레이션은 여러 번 실행해도 안전해야 함
4. **에러 처리**: 실패 시 명확한 에러 메시지 반환
5. **테스트**: 새 설치와 업그레이드 모두 테스트

### 9. 버전 릴리스 프로세스 (필수!)

**사용자가 버전 태그 또는 버전 업을 요청하면 반드시 아래 단계를 따라야 합니다.**

#### 릴리스 단계

**1단계: 버전 번호 증가**

`pyproject.toml`에서 버전을 0.0.1 증가:
```toml
# Before
version = "1.8.1"

# After
version = "1.8.2"
```

**2단계: 버전 문서 생성**

`docs/versions/{version}.md` 파일 생성:
```markdown
# Version 1.8.2

**Release Date:** YYYY-MM-DD
**Focus:** 간단한 설명

## Overview
변경 사항 요약

## 주요 변경사항
### 1. 변경/추가된 기능
- 내용

## 파일 변경 목록
```
변경된 파일 목록
```

## 업그레이드 노트
설치 방법 및 마이그레이션 안내
```

**3단계: CHANGELOG.md 업데이트**

`CHANGELOG.md` 상단에 새 버전 엔트리 추가:
```markdown
## [1.8.2] - YYYY-MM-DD

### Added/Changed/Fixed
- 변경 내용

### Technical Details
- 기술적 세부사항
```

**4단계: CLAUDE.md 버전 업데이트**

`CLAUDE.md` 상단의 버전 정보 수정:
```markdown
**버전**: 1.8.2
```

그리고 "버전 히스토리" 섹션에 새 버전 추가.

**5단계: Git 커밋 및 푸시**

```bash
git add pyproject.toml docs/versions/1.8.2.md CHANGELOG.md CLAUDE.md
git commit -m "Release v1.8.2: 간단한 설명"
git push origin main
```

#### 체크리스트

버전 릴리스 시 확인:
- [ ] `pyproject.toml` 버전 증가 (0.0.1)
- [ ] `docs/versions/{version}.md` 생성
- [ ] `CHANGELOG.md` 업데이트
- [ ] `CLAUDE.md` 버전 및 히스토리 업데이트
- [ ] 마이그레이션 필요 시 `src/migrations/versions/` 추가
- [ ] Git 커밋 및 푸시

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

- **v1.8.2**: CLAUDE.md 문서화 개선 (데이터 견고성 원칙, 마이그레이션 가이드, 릴리스 프로세스)
- **v1.8.1**: --force 플래그가 전체 export 트리거하도록 수정
- **v1.8.0**: daily_snapshots export/import 추가
- **v1.7.9**: 백업 기본값 비활성화
- **v1.7.7**: 동기화 상태 확인 기능
- **v1.7.6**: chunked 파일 지원

## 연락처

Issues: https://github.com/wangtae/claude-code-usage-analytics/issues
