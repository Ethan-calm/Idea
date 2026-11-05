# 🌙 Night Shift (MVP)

자는 동안 사용 한도 리셋에 맞춰 일하는 에이전트. Python 표준 라이브러리만 사용하고, 일은 headless Claude Code(`claude -p`)가 합니다.

## 흐름

```
자기 전          리셋마다 (cron)                         아침
queue.md  ──▶  nightshift run  ──▶ 작업별 git 브랜치  ──▶ nightshift brief
                 │  ├ 예산 안에서 근무표 짜기 (큰 일 먼저, 남는 칸은 작은 일)
                 │  ├ 작업마다 worktree + claude -p
                 │  ├ handoff 노트 → 다음 근무가 이어받음
                 │  └ 한도에 걸리면 즉시 퇴근, 작업은 큐에 남김
```

## 사용법

```bash
cp queue.example.md ~/my-project/queue.md      # 할 일 적기
python3 nightshift.py plan  --repo ~/my-project              # 오늘 밤 근무표 미리보기
python3 nightshift.py run   --repo ~/my-project --dry-run    # 토큰 안 쓰고 흐름만 체험
python3 nightshift.py run   --repo ~/my-project --budget 12  # 진짜 근무
python3 nightshift.py brief --repo ~/my-project --out morning.md
```

### 큐 문법
```markdown
- [ ] 로그인 페이지 다크모드 구현 !high     ← 우선순위: !high / !low
- [ ] 결제 모듈 리팩터링 #big               ← 크기: #small(1) #medium(3) #big(8)
- [ ] README 오타 수정                      ← 태그 없으면 키워드로 추정
```
Night Shift가 체크박스를 갱신합니다: `[x]` 끝남 · `[~]` 진행 중(다음 근무에서 handoff 노트와 함께 이어감) · `[!]` 막힘.

### 리셋 시각에 맞춰 예약 (예: 리셋이 23시·04시라면)
```cron
5 23 * * *  cd ~/my-project && python3 ~/Idea/prototypes/night-shift/nightshift.py run --budget 12 >> .nightshift/cron.log 2>&1
5  4 * * *  cd ~/my-project && python3 ~/Idea/prototypes/night-shift/nightshift.py run --budget 12 >> .nightshift/cron.log 2>&1
30 7 * * *  cd ~/my-project && python3 ~/Idea/prototypes/night-shift/nightshift.py brief --out morning.md
```

## 안전장치
- 모든 작업은 `nightshift/<slug>` 브랜치의 별도 worktree에서만 진행 → 원래 작업 트리는 `queue.md` 말고 그대로.
- 머지·푸시는 안 함. 아침에 사람이 보고 결정.
- 기본 권한은 `--permission-mode acceptEdits`. 셸 명령이 필요하면 `ClaudeExecutor(extra_args=[...])`로 `--allowedTools`를 직접 열어주세요.
- 상태는 `.nightshift/`(자동으로 gitignore)에: `log.jsonl`, `handoff/`, `worktrees/`.

## 테스트
```bash
python3 -m unittest discover -s tests -v
```
`tests/fake_claude.sh`가 `claude` 대신 동작해서, worktree·커밋·한도 감지·브리핑까지 토큰 없이 끝까지 검증합니다.

## 다음 단계 아이디어
- 예산(단위)을 실제 토큰 사용량으로 보정: `--output-format json`의 usage를 로그에 남겨 작업 크기 추정을 학습
- 브리핑을 푸시 알림/메신저로 발송
- Chaos Gremlin을 "야간 근무자" 중 하나로 등록
