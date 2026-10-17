# Preflight: Requirements and Design

Version 0.1 | Draft | 2026-10-17

---

## 1. Purpose

Preflight is a personal command-line tool (with a small web dashboard) that uses my own failure history to protect each new project.

Before a project starts, it reads what I plan to build, compares it with the mistakes I have made before, asks me questions, and produces a checklist I must work through. During the project it records my failures against that checklist. At the end it records what I achieved and what failed, so the next project starts smarter.

One sentence: **store my failures, turn them into checklists for the next project, and show me whether I am repeating mistakes less.**

## 2. Problem

- I repeat the same kinds of mistakes across projects (missing timeouts, unbounded retries, concurrency bugs, unclear requirements).
- Notes about past failures are never looked at when a new project starts.
- My first guess at a root cause is often wrong, and I have no record of how often.
- I have no evidence of whether I am improving.

## 3. Goals and non-goals

### Goals

1. Capture failures per project in under one minute.
2. Before a project starts, generate a checklist from my failure history, active rules, and gaps in my requirements.
3. Require the checklist to be worked through before the project is marked as running.
4. Record failures and achievements during and at the end of each project.
5. Show progress across projects on a dashboard.
6. Train logical thinking through guess-vs-cause scoring, predictions, and drills.

### Non-goals (version 1)

- Reading pages behind a login (Confluence, Jira, private documents).
- A browser extension or local capture server.
- Auto-capture from tests, commits, or LeetCode.
- Team or multi-user features.
- Writing or fixing my code.
- A mobile app.

## 4. Core concepts

| Concept          | Meaning                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Project**      | A piece of work with an idea, goals, requirements, links, a checklist, failures, and wins.                               |
| **Requirements** | Text I type in myself. AI reviews it.                                                                                    |
| **Link**         | A saved URL with a title and note. Either internal (reference only) or public (can be captured).                         |
| **Snapshot**     | A saved text copy of a public link, with capture time.                                                                   |
| **Failure**      | Something that went wrong: what happened, my guess, the real cause, the fix, and the check that would have prevented it. |
| **Pattern**      | A group of similar failures (same category, same project or topic).                                                      |
| **Rule**         | A checklist item I committed to after a pattern repeated. It has a status and a verdict.                                 |
| **Checklist**    | The list for one project, built from failures, rules, and requirement gaps.                                              |
| **Drill**        | A practice question generated from one of my failures, reviewed on a spaced schedule.                                    |
| **Prediction**   | A hypothesis and confidence written before debugging, closed with the real outcome.                                      |
| **Win**          | An achievement recorded during or at the end of a project.                                                               |

## 5. Project lifecycle

```
draft  ->  started  ->  closed
```

- **draft**: project created; requirements and links being added.
- **started**: review done, checklist generated, required items resolved. Failures and wins can be logged.
- **closed**: retrospective completed. Data feeds patterns, rules, drills, and the dashboard.

## 6. Main workflow

1. **Create** the project and type the idea, goals, and requirements.
2. **Add links** (internal as references, public optionally captured).
3. **Start**: review what will be sent to AI, approve it, answer AI's questions, receive the checklist, resolve required items.
4. **Work**: log failures and wins against the project, linking failures to checklist items.
5. **Close**: write the retrospective, mark which checklist items helped or were missed.
6. **Review** progress on the dashboard and the weekly report.

## 7. CLI reference

Command name: `preflight`. Pattern: `preflight <project> <action>`.

| Command                                                                      | What it does                                                                    |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `preflight new <project>`                                                    | Create a project; prompts for idea, goals, requirements.                        |
| `preflight <project> requirements`                                           | Add or edit requirements (opens editor or prompts). Each save is a new version. |
| `preflight <project> add-link <url> [--internal] [--capture] [--note "..."]` | Save a link; optionally capture a public page.                                  |
| `preflight <project> links`                                                  | List links and snapshots.                                                       |
| `preflight <project> refresh <link-id>`                                      | Re-capture a public link; old snapshot kept.                                    |
| `preflight <project> start`                                                  | Run the review and checklist gate.                                              |
| `preflight <project> checklist`                                              | Show the checklist and item statuses.                                           |
| `preflight <project> check <n> done`                                         | Mark item done.                                                                 |
| `preflight <project> check <n> na --reason "..."`                            | Mark not applicable.                                                            |
| `preflight <project> check <n> defer --reason "..." --until <date>`          | Defer an item.                                                                  |
| `preflight <project> fail "..."`                                             | Log a failure; prompts for guess, cause, fix, prevention.                       |
| `preflight <project> win "..."`                                              | Log an achievement.                                                             |
| `preflight <project> predict "..."`                                          | Record a hypothesis and confidence before debugging.                            |
| `preflight <project> status`                                                 | Show progress for the project.                                                  |
| `preflight <project> close`                                                  | Run the retrospective.                                                          |
| `preflight drill`                                                            | Review today's due drills.                                                      |
| `preflight report [--week]`                                                  | Show the progress or weekly report.                                             |
| `preflight ui`                                                               | Open the dashboard.                                                             |
| `preflight export [--format csv\|json\|md]`                                  | Export all data.                                                                |

## 8. Functional requirements

### 8.1 Projects and requirements

- **FR-1** Create a project with name, idea, goals, and requirements, from the CLI or the UI.
- **FR-2** Edit requirements at any time; every save creates a version with a timestamp.
- **FR-3** Show which requirements version the current checklist was built from.
- **FR-4** Re-run the review when requirements or links change.

### 8.2 Links

- **FR-5** Save any link with URL, title, note, and type (internal or public).
- **FR-6** Internal links are never fetched; they are references only. I can paste key points into the note or requirements.
- **FR-7** Public links can be captured as text snapshots (title, headings, main text, tables).
- **FR-8** Capture tries a plain fetch first and uses a headless browser only when the page needs JavaScript.
- **FR-9** If a page needs a login, capture stops, tells me, and saves the link as a reference only.
- **FR-10** Show a preview of every snapshot. I can edit or delete it.
- **FR-11** Refreshing a link creates a new snapshot version; old versions are kept.
- **FR-12** Capture respects robots rules and rate-limits itself.

### 8.3 Review and checklist

- **FR-13** `start` shows exactly what would be sent to AI, per item, and requires my approval for each item before the review runs.
- **FR-14** Items marked sensitive are never sent.
- **FR-15** AI compares my requirements, approved snapshots, and link notes with my failure history and active rules.
- **FR-16** AI asks targeted questions grouped by past failure category. I can answer, mark not relevant, or defer each.
- **FR-17** AI lists gaps: requirements that are missing, unclear, or risky given my history.
- **FR-18** The checklist is generated from failures, active rules, and gaps. Each item shows its source (failure ID, rule, or gap).
- **FR-19** Each item has a status: not checked, done, not applicable, deferred.
- **FR-20** Not applicable and deferred items require a reason. Deferred items require a revisit date.
- **FR-21** Items are marked required or optional (see section 9).
- **FR-22** A project becomes `started` only when all required items are resolved (done, not applicable with reason, or deferred with reason and date). Overriding the gate requires a typed reason, which is recorded.
- **FR-23** If I decline to send anything to AI, I still receive a checklist built from my failures and rules, without requirement-specific items.

### 8.4 During the project

- **FR-24** Log a failure with project, what happened, first guess, real cause, fix, prevention check, category, severity, and time lost.
- **FR-25** When I log a failure, show checklist items that look related and let me link one, or mark the failure "not on the checklist".
- **FR-26** Show similar past failures when I log a new one.
- **FR-27** Log wins (achievements), optionally tagged (used a rule, caught a risk early, other).
- **FR-28** Record predictions (hypothesis, evidence planned, confidence) and close them with the outcome.
- **FR-29** Logging a failure on a project that has not started prompts me to start it first, but does not block logging.

### 8.5 Closing a project

- **FR-30** `close` runs a retrospective: wins, failures not yet logged, and for each checklist item whether it helped, was missed, or was irrelevant.
- **FR-31** Closed-project data feeds pattern detection, rules, drills, and the dashboard.
- **FR-32** Whether the retrospective is required to close a project is configurable (default: required).

### 8.6 Patterns, rules, drills

- **FR-33** Detect repeated failures by category within a project and across projects.
- **FR-34** When a pattern repeats, suggest a rule drafted from my prevention notes. I edit and approve it.
- **FR-35** Active rules appear in the checklist of any matching project.
- **FR-36** Each rule gets a verdict after an observation period: working, needs revision, or retired.
- **FR-37** Generate drills from failures and schedule them on a spaced interval (1, 3, 7, 21 days; sooner when answered poorly).
- **FR-38** Drill answers are graded by AI or self-graded.

### 8.7 Reports and export

- **FR-39** Dashboard (section 12) and weekly report.
- **FR-40** Export all data to CSV, JSON, and Markdown. The checklist can be exported as Markdown to paste into project docs.

## 9. Checklist design

### 9.1 Sources

| Source                        | Example item                                                                                          |
| ----------------------------- | ----------------------------------------------------------------------------------------------------- |
| A past failure                | "Every external call has a timeout, retry cap, and backoff (Failure #1: retry loop hung 40 minutes)." |
| An active rule                | "Rule: external-call-safety."                                                                         |
| A requirement gap             | "Requirements mention caching but not what happens when the cache is down."                           |
| An unanswered review question | "No answer yet on expected peak load."                                                                |

### 9.2 Item fields

- Text
- Source type and reference
- Why it is here (one line)
- Priority (high, medium, low)
- Required or optional
- Status (not checked, done, not applicable, deferred)
- Reason and revisit date, when applicable
- Linked failures (filled in during the project)

### 9.3 Priority and required rules

- Priority is based on how often and how costly that mistake was (repeat count, severity, minutes lost).
- **Required**: items tied to a high-severity failure, a repeated pattern, or a rule with status "needs revision".
- **Optional**: everything else.
- Items are ordered by priority.

### 9.4 Gate behavior

- Default: strict gate with override. The project cannot start while required items are unresolved, unless I override with a typed reason.
- The override reason is stored and shown in the retrospective.

## 10. AI review

### 10.1 Inputs (only with my approval)

- Typed requirements, idea, and goals
- Approved snapshots of public links
- For internal links: title, URL, and note only
- Failure history, patterns, and active rules (local data I own)

### 10.2 Outputs

- Questions (grouped by failure category)
- Gap list
- Draft checklist items, each with a source reference

### 10.3 AI also supports

- Suggesting failure category and root cause when I log a failure
- Drafting a rule from several failures
- Grading drill answers

### 10.4 Principles

- AI is required for the pre-project review.
- Without an API key, the app still logs failures, builds rule-based checklists, and shows the dashboard.
- All counts, trends, and verdicts come from plain code, not AI.
- Every AI output that creates data (checklist items, rules) is shown to me for approval or edit first.

## 11. Link handling

| Type         | Examples                                                                                | Behavior                                                                               |
| ------------ | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| **Internal** | Confluence, Jira, private repos, company docs                                           | Saved as a reference. Never fetched. I paste key points into the note or requirements. |
| **Public**   | Library docs, API references, vendor limits and pricing pages, articles, GitHub READMEs | Optional capture to a text snapshot.                                                   |

Capture details:

- Extract title, headings, main text, tables, and capture time.
- Pages that need a login are skipped with a clear message.
- Snapshots are stored locally and versioned.
- Preview is shown before the snapshot is used for anything.

## 12. Dashboard and metrics

### 12.1 Metric definitions

| Metric                        | Definition                                                                                                              |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Failures per project**      | Count of failures logged for each project, in project order.                                                            |
| **Repeat rate**               | Failures that match an earlier pattern within 60 days, divided by all failures in the period. Main number; should fall. |
| **Guess accuracy**            | Failures where my first guess matched the real cause, divided by failures with both recorded.                           |
| **Time to root cause**        | Time between failure start and root cause recorded (when entered).                                                      |
| **Minutes lost**              | Total and per week.                                                                                                     |
| **Checklist coverage**        | Items resolved before start, divided by total items.                                                                    |
| **On-checklist failure rate** | Failures linked to a checklist item, divided by all failures in the project.                                            |
| **Missed-item rate**          | Failures linked to an item I had marked done, divided by items marked done.                                             |
| **Not-on-checklist failures** | Failures marked as not on the checklist; these seed new items for the next project.                                     |
| **Question coverage**         | Review questions answered, divided by questions asked.                                                                  |
| **Wins per project**          | Count of wins, by tag.                                                                                                  |
| **Calibration gap**           | Average confidence minus actual accuracy across closed predictions.                                                     |
| **Drill retention**           | Share of drill reviews scored as remembered.                                                                            |
| **Rule status**               | Count of rules working, needs revision, retired.                                                                        |

### 12.2 Views

- Project-by-project progress (failures, repeat rate, wins)
- Failures per week by category (12 weeks)
- Rule scoreboard with before/after
- Guess accuracy and calibration trends
- Per-project checklist outcome (helped, missed, irrelevant)

## 13. Data model

| Table                  | Key fields                                                                                                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `projects`             | id, name, idea, goals, status, created_at, started_at, closed_at                                                                                                               |
| `requirement_versions` | id, project_id, text, version, created_at                                                                                                                                      |
| `links`                | id, project_id, url, title, note, type (internal/public), sensitive                                                                                                            |
| `snapshots`            | id, link_id, content, version, captured_at                                                                                                                                     |
| `reviews`              | id, project_id, requirements_version, approved_items, created_at                                                                                                               |
| `review_questions`     | id, review_id, category, question, answer, status                                                                                                                              |
| `checklist_items`      | id, project_id, text, source_type, source_ref, priority, required, status, reason, revisit_date                                                                                |
| `failures`             | id, project_id, title, what_happened, guessed_cause, root_cause, fix, prevention, category, severity, minutes_lost, guess_correct, checklist_item_id, on_checklist, created_at |
| `patterns`             | id, project_scope, category, label, created_at                                                                                                                                 |
| `rules`                | id, pattern_id, checklist_text, status, created_at, verdict_at                                                                                                                 |
| `drills`               | id, failure_id, prompt, answer, ease, interval_days, due_date                                                                                                                  |
| `drill_reviews`        | id, drill_id, score, reviewed_at                                                                                                                                               |
| `predictions`          | id, project_id, hypothesis, evidence_planned, confidence, correct, failure_id                                                                                                  |
| `wins`                 | id, project_id, text, tag, created_at                                                                                                                                          |
| `retrospectives`       | id, project_id, summary, created_at                                                                                                                                            |
| `gate_overrides`       | id, project_id, reason, created_at                                                                                                                                             |

Failure categories: missed edge case, wrong assumption, misread requirement, knowledge gap, no verification, wrong approach, state or concurrency bug.

## 14. Architecture

- **CLI** (`preflight`): Python (Typer or Click).
- **Core service**: FastAPI app running locally, used by both the CLI and the UI.
- **Storage**: SQLite for version 1 (move to Postgres with pgvector later if similarity search is needed).
- **Link capture**: HTTP fetch with readable-text extraction; Playwright for pages that need JavaScript.
- **AI**: Claude API for review, classification, rule drafting, and drill grading; structured JSON output.
- **Scheduler**: a local job (cron or APScheduler) for drills, rule verdicts, and weekly reports.
- **UI**: a single-page dashboard served locally.

## 15. Privacy and security

- All data is stored locally.
- Nothing is sent to AI without per-item approval at the time of each review. Approval is not remembered for later reviews.
- Items marked sensitive are never sent.
- Internal links are never fetched or sent; only title, URL, and note can be sent, with approval.
- The API key is read from an environment variable and never stored in the database.
- The local server binds to `127.0.0.1` only.
- Captures and snapshots can be deleted at any time; deleting a project deletes its snapshots.

## 16. Non-functional requirements

- Logging a failure takes under one minute.
- Single user; runs on one machine.
- The review finishes in reasonable time on long requirements (chunked reading).
- Clear error messages: AI key missing, link unreachable, login required, capture failed.
- Export is always available so data is never locked in.

## 17. Success measures

- Repeat rate falls from project to project.
- Fewer failures per project than the previous project.
- Failures that were on a checklist item and still happened decrease.
- Guess accuracy rises.
- Most review questions are answered before a project starts.
- Every project has a start review and a closing retrospective.
- I log failures consistently for four weeks.

## 18. Build phases

| Phase            | Scope                                                                                      | Result                                |
| ---------------- | ------------------------------------------------------------------------------------------ | ------------------------------------- |
| **1. Core**      | Projects, requirements, failure logging, categories, search, SQLite                        | Failures stored per project           |
| **2. Checklist** | Pattern detection, rules, rule-based checklist, `start` gate, checklist-to-failure linking | Checklist before each project         |
| **3. AI review** | Approval screen, questions, gap list, requirement-aware checklist                          | Questions based on my history         |
| **4. Links**     | Saved links, public capture, snapshots                                                     | Public references available to review |
| **5. Progress**  | Retrospective, wins, dashboard, rule verdicts, weekly report                               | Evidence of improvement               |
| **6. Thinking**  | Drills, spaced repetition, predictions, calibration                                        | Logical-thinking practice             |

## 19. Example session

```
$ preflight new llm-gateway
Idea: Proxy for LLM providers with fallback, caching, and cost tracking
Goals: ...
Requirements: (opens editor)

$ preflight llm-gateway add-link https://docs.example.com/rate-limits --capture
Captured: "Rate limits" (1,240 words). Preview? [y/n]

$ preflight llm-gateway add-link https://wiki.company.com/gateway-spec --internal --note "Latency target 800ms p95"

$ preflight llm-gateway start
Will be sent to AI (approve each):
  [1] Requirements v2 (420 words)            approve? y
  [2] Snapshot: Rate limits (1,240 words)    approve? y
  [3] Note on internal link                  approve? y

Questions based on your history:
  Q1 (missed edge case): Your requirements mention retries. What is the max attempts, backoff, and total deadline?
  Q2 (state bug): Per-user budgets are read then updated. How do you keep that atomic?
  ...

Checklist (3 required, 5 optional):
  1. [required] Every external call has a timeout, retry cap, and backoff   (Failure #1)
  2. [required] Read-then-write on shared state is atomic                    (Failure #3)
  3. [required] Cache lookup cannot block the request path                   (Failure #2)
  4. [optional] Define behavior when the provider returns 429                (Gap)
  ...

$ preflight llm-gateway check 1 done
$ preflight llm-gateway check 2 done
$ preflight llm-gateway check 3 done
Required items resolved. Project started.

$ preflight llm-gateway fail "Semantic cache returned stale answers after prompt change"
Related checklist items: 3. Link to item? [3/none]
```

## 20. Open decisions

1. **Gate strictness**: default is strict with a typed override reason. Confirm or change to warning-only.
2. **Public snapshots sent to AI**: send in full after approval, or summarize locally first to reduce cost. Default: full, after approval.
3. **Retrospective required to close**: default is required.
4. **Achievement tags**: free text only, or tagged. Default: optional tags.
5. **Pattern matching**: category within project (version 1), embedding similarity later.
6. **Export of checklist to Markdown**: included in version 1.
