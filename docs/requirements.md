# Preflight requirements

Version 0.2 | 2026-10-18

This document says what Preflight must do. [The design](design.md) says how it is built and what it costs. The [CLI](cli.md), [configuration](configuration.md), [API](api.md), and [data model](data-model.md) references are generated from the code. [The validation report](../validation/report.md) shows which tests prove each requirement here, and `./scripts/check.sh` regenerates it.

Requirement ids (FR-1 and so on) are stable. A changed requirement keeps its id. A new one takes the next free number.

## Purpose

Preflight is a personal tool, a command-line client with a web dashboard, that uses my own failure history to protect each new project.

Before a project starts, it reads what I plan to build, compares it with the mistakes I have made before, asks me questions, and produces a checklist I must work through. During the project it records my failures against that checklist. At the end it records what I achieved and what failed, so the next project starts smarter.

In one sentence: **store my failures, turn them into checklists for the next project, and show me whether I am repeating mistakes less.**

## Problem

- I repeat the same kinds of mistakes across projects: missing timeouts, unbounded retries, concurrency bugs, and unclear requirements.
- Notes about past failures are never looked at when a new project starts.
- My first guess at a root cause is often wrong, and I have no record of how often.
- I have no evidence of whether I am improving.

## Goals and non-goals

### Goals

1. Capture failures per project in under one minute.
2. Before a project starts, generate a checklist from my failure history, active rules, and gaps in my requirements.
3. Require the checklist to be worked through before the project is marked as started.
4. Record failures and achievements during and at the end of each project.
5. Show progress across projects on a dashboard.
6. Train logical thinking through guess-versus-cause scoring, predictions, and drills.

### Non-goals for version 1

- Reading pages behind a login, such as Confluence, Jira, or private documents.
- A browser extension or a local capture server.
- Capturing failures automatically from tests, commits, or practice sites.
- Team or multi-user features. Every table carries a user id, so a second user would not need a data migration.
- Writing or fixing my code.
- A mobile app.

## Core concepts

| Concept | Meaning |
|---|---|
| **Project** | A piece of work with an idea, goals, requirements, links, a checklist, failures, and wins. |
| **Requirements** | Text I type in myself. Every changed save is a new version. AI reviews it. |
| **Link** | A saved URL with a title and a note. Either internal (a reference only) or public (can be captured). |
| **Snapshot** | A saved text copy of a public link, with its capture time and version. |
| **Failure** | Something that went wrong: what happened, my first guess, the real cause, the fix, and the check that would have prevented it. |
| **Pattern** | Failures in the same category, within one project or across projects. |
| **Rule** | A checklist item I committed to after a pattern repeated. It has a status and a verdict. |
| **Checklist** | The list for one project, built from failures, rules, and review results. |
| **Review** | One pass of AI over the material I approved for a project. It returns questions, gaps, and draft checklist items. |
| **Drill** | A practice question generated from one of my failures, reviewed on a spaced schedule. |
| **Prediction** | A hypothesis and a confidence written before debugging, closed with the real outcome. |
| **Win** | An achievement recorded during or at the end of a project. |

## Project lifecycle

```text
draft  ->  started  ->  closed
```

- **draft**: the project exists. Requirements and links are being added.
- **started**: the checklist is built and every required item is resolved, or the gate was overridden with a reason. Failures and wins can be logged at any stage.
- **closed**: the retrospective is done. The project's data feeds patterns, rules, drills, and the dashboard.

## Main workflow

1. **Create** the project and type the idea, goals, and requirements.
2. **Add links.** Internal links are references. Public links can be captured.
3. **Start.** Review what would be sent to AI, approve each item, answer the questions, accept the suggested checklist items I want, and resolve the required items.
4. **Work.** Log failures and wins, and link failures to checklist items.
5. **Close.** Write the retrospective and mark which checklist items helped, were missed, or were irrelevant.
6. **Review progress** on the dashboard and in the weekly report.

Each step is a command in [the CLI reference](cli.md). Steps 1 and 6 also work in the dashboard.

## Functional requirements

### Projects and requirements

- **FR-1** Create a project with a name, idea, goals, and requirements, from the CLI or the dashboard. Project names cannot be the same as a CLI command.
- **FR-2** Edit requirements at any time. Every changed save creates a new version with a timestamp. Saving identical text creates nothing.
- **FR-3** Show which requirements version the current checklist was built from.
- **FR-4** Offer a new review when anything the review could see has changed: requirements, links, notes, snapshots, or failure history. A review can run again after the project starts, and its accepted results join the checklist without changing the project's status.

### Links

- **FR-5** Save any link with a URL, title, note, and type (internal or public). A link can be marked sensitive.
- **FR-6** Internal links are never fetched. They are references only. I can paste key points into the note or the requirements.
- **FR-7** Public links can be captured as text snapshots that keep the title, headings, main text, and tables.
- **FR-8** Capture tries a plain fetch first and uses a headless browser only when the page needs JavaScript. The browser is off unless the server is configured to allow it.
- **FR-9** If a page needs a login, capture stops, tells me, and keeps the link as a reference only.
- **FR-10** Show a preview of every snapshot. I can edit or delete it, and only snapshots I approve are offered to AI.
- **FR-11** Refreshing a link creates a new snapshot version. Old versions are kept.
- **FR-12** Capture follows robots rules and waits between requests to one site.

### Review and checklist

- **FR-13** `start` lists what would be sent to AI, item by item, lets me read each item's exact text, and needs my approval for each item before the review runs.
- **FR-14** Items marked sensitive are never offered and never sent.
- **FR-15** AI compares my requirements, approved snapshots, and link notes with my failure history and active rules.
- **FR-16** AI asks targeted questions, each tagged with a past failure category. I can answer, mark not relevant, or defer each one.
- **FR-17** AI lists gaps: requirements that are missing, unclear, or risky given my history.
- **FR-18** The checklist is built from failures, active rules, and accepted review results. Each item shows its source: a failure, a rule, a gap, or an open question.
- **FR-19** Each item has a status: not checked, done, not applicable, or deferred.
- **FR-20** Not applicable and deferred items need a reason. Deferred items also need a revisit date in the future.
- **FR-21** Each item is required or optional, as [the checklist rules](#checklist-rules) define.
- **FR-22** A project becomes started only when every required item is resolved: done, not applicable with a reason, or deferred with a reason and a date. Overriding the gate needs a typed reason, which is recorded and shown in the retrospective.
- **FR-23** If I send nothing to AI, or no AI is available, I still get a checklist built from my failures and rules, without requirement-specific items.

### During the project

- **FR-24** Log a failure with its project, what happened, my first guess, the real cause, the fix, the prevention check, a category, a severity, and the minutes lost. Only the title is needed at first.
- **FR-25** When I log a failure, show checklist items that look related, and let me link one or mark the failure as not on the checklist.
- **FR-26** Show similar past failures when I log a new one.
- **FR-27** Log wins, optionally tagged as "used a rule", "caught a risk early", or "other".
- **FR-28** Record predictions (a hypothesis, the evidence I plan to check, and a confidence) and close them with the outcome.
- **FR-29** Logging a failure on a project that has not started reminds me to start it, but does not block the failure.

### Closing a project

- **FR-30** `close` runs a retrospective: late wins, failures not yet logged, and for each checklist item whether it helped, was missed, or was irrelevant.
- **FR-31** A closed project's data feeds pattern detection, rules, drills, and the dashboard.
- **FR-32** Whether the retrospective summary is required to close a project is configurable. By default it is required.

### Patterns, rules, and drills

- **FR-33** Detect repeated failures by category, within a project and across projects.
- **FR-34** When a pattern repeats, suggest a rule drafted from my prevention notes. I edit and approve it. AI can draft it instead, with my approval.
- **FR-35** Active rules appear in the checklist of every matching project. A rule from a pattern across projects matches every project. A rule from one project's pattern matches a project whose idea, goals, or requirements share enough keywords with that pattern's failures. Where a rule matches, it replaces the separate failure items of its category.
- **FR-36** Each rule gets a verdict after an observation period: working when its category failed less often after the rule than before, and needs revision otherwise. I retire a rule by hand.
- **FR-37** Generate drills from the failures of closed projects and schedule them at 1, 3, 7, and 21 days, then further apart. A poor answer starts the schedule over.
- **FR-38** I grade a drill answer myself, or AI grades it with my approval.
- **FR-43** Every look at my patterns recounts them from my failures. A category that no longer repeats stops being suggested and its pattern is dropped, unless I already approved a rule from it: that rule stays until I retire it.

### Reports and export

- **FR-39** A dashboard with the views in [metrics and dashboard](#metrics-and-dashboard), and a weekly report.
- **FR-40** Export all data to CSV, JSON, and Markdown. The checklist can be exported as Markdown to paste into project docs.
- **FR-41** With no Anthropic API key, AI features use a local model through Ollama, behind the same per-item approval. Without either, everything except the AI features still works.
- **FR-42** Import past failures from a CSV or JSON file with their original dates. Importing the same file again adds nothing, and a bad row is reported without stopping the rest.

## Checklist rules

### Sources

| Source | Example item |
|---|---|
| A past failure | "Every external call has a timeout, retry cap, and backoff (Failure #1a2b3c4d: retry loop hung 40 minutes)." |
| An active rule | "Rule: external-call-safety." |
| A review gap | "Requirements mention caching but not what happens when the cache is down." |
| An open review question | "No answer yet: what is the expected peak load?" |

### Item fields

- Text
- Source type and reference
- Why it is here, in one line
- Priority: high, medium, or low
- Required or optional
- Status: not checked, done, not applicable, or deferred
- Reason and revisit date, when the status needs them
- Failures linked to the item during the project

### Priority and required items

- Priority comes from how often and how costly the mistake was: repeat count, severity, and minutes lost.
- **Required**: items tied to a high-severity failure, a repeated pattern, or a rule that needs revision.
- **Optional**: everything else.
- Items are ordered by priority.

### Start gate

- The gate is strict by default. A project cannot start while required items are unresolved, unless I override it with a typed reason.
- When the last required item is resolved, the checklist is rebuilt from current history first, so a project never starts from an out-of-date checklist.
- The override reason is stored and shown in the retrospective.

## AI review

### What can be sent, only with my approval

- My requirements, idea, and goals.
- Approved snapshots of public links.
- For internal links: the title, URL, and note only.
- My failure history and active rules, limited to the failures most relevant to the requirements.

### What comes back

- Questions, each tagged with a failure category.
- Gaps.
- Draft checklist items. Each one must cite something its own AI call received. A draft that cites anything else is dropped.

### What else AI helps with

- Suggesting a failure's category, root cause, and prevention when I log it.
- Drafting a rule from a pattern's failures.
- Grading drill answers.

### Providers

- With an Anthropic API key, reviews use a stronger model and the smaller tasks use a cheaper one.
- Without a key, AI uses a local Ollama model. Local calls cost nothing.
- Material too long for the model's context is split across several calls, and the results are merged. Every call carries the approved history and notes.
- A monthly budget refuses any call whose worst-case cost would exceed it.

### Principles

- Every AI call sends only what I approved for that call. Approval is never remembered.
- Every call is logged with the exact text sent, the reply, the prompt version, the tokens, and the cost.
- All counts, trends, and verdicts come from plain code, not AI.
- Nothing AI produces becomes a checklist item or a rule until I accept or edit it.

## Link handling

| Type | Examples | Behavior |
|---|---|---|
| **Internal** | Confluence, Jira, private repos, company docs | Saved as a reference. Never fetched. I paste key points into the note or the requirements. |
| **Public** | Library docs, API references, vendor limits and pricing pages, articles, GitHub READMEs | Can be captured to a text snapshot. |

Capture details:

- A snapshot keeps the title, headings, main text, tables, and capture time.
- Pages that need a login are skipped with a clear message.
- Snapshots are versioned and capped at 200 KB.
- I see a preview before a snapshot is used for anything.

## Metrics and dashboard

### Metric definitions

| Metric | Definition |
|---|---|
| **Failures per project** | Count of failures logged for each project, in project order. |
| **Repeat rate** | Failures that match an earlier failure's category within 60 days, divided by all failures in the period. The main number. It should fall. |
| **Guess accuracy** | Failures where my first guess matched the real cause, divided by failures with both recorded. |
| **Time to root cause** | Time between the failure and the moment its root cause was recorded, when entered. |
| **Minutes lost** | Total, and per week. |
| **Checklist coverage** | Items resolved before the project started, divided by all items. |
| **On-checklist failure rate** | Failures linked to a checklist item, divided by all failures in the project. |
| **Missed-item rate** | Items marked done that later had a failure linked to them, divided by items marked done. |
| **Not-on-checklist failures** | Failures marked as not on the checklist. These seed items for the next project. |
| **Question coverage** | Review questions answered, divided by questions asked. |
| **Wins per project** | Count of wins, by tag. |
| **Calibration gap** | Average confidence minus actual accuracy across closed predictions. Positive means overconfident. |
| **Drill retention** | Share of drill reviews scored 3 or higher out of 5. |
| **Rule status** | Count of rules that are active, working, need revision, or retired. |

Every percentage is shown with its counts. A trend is hidden until it has three points.

### Views

- Project-by-project progress: failures, repeat rate, coverage, missed items, wins by tag.
- Failures per week by category, over 12 weeks.
- Minutes lost per week, over 12 weeks.
- A rule scoreboard with failures before and after each rule.
- Guess accuracy and calibration trends by month.
- Per-project checklist outcomes: helped, missed, and irrelevant.

## Privacy and security

- Nothing is sent to AI without per-item approval at the time of each review. Approval is not remembered for later reviews.
- Items marked sensitive are never sent.
- Internal links are never fetched. Only their title, URL, and note can be sent, with approval.
- The API key is read from an environment variable and never stored in the database.
- With no Anthropic key, review material goes only to the Ollama server I configure.
- The dashboard needs a login, and every form is protected against cross-site requests.
- Each user's data is visible only to that user.
- Link capture fetches only public addresses.
- Captures and snapshots can be deleted at any time. Deleting a project deletes its snapshots.

## Non-functional requirements

- Logging a failure takes under one minute.
- One hosted service serves the dashboard and the CLI on any of my machines.
- The review finishes in reasonable time on long requirements (chunked reading).
- Clear error messages: AI not available, link unreachable, login required, capture failed.
- Export is always available so data is never locked in.
- Monthly AI spend stays within a configured budget.

## Success measures

These need weeks of real use, so the validation report lists them as open.

- Repeat rate falls from project to project.
- Fewer failures per project than the previous project.
- Failures that were on a checklist item and still happened decrease.
- Guess accuracy rises.
- Most review questions are answered before a project starts.
- Every project has a start review and a closing retrospective.
- I log failures consistently for four weeks.

## Decisions

These were open in version 0.1 and are now settled. Each can change through the setting named.

| Decision | Choice | Where it is set |
|---|---|---|
| Gate strictness | Strict, with a typed override reason | Built in |
| Public snapshots sent to AI | In full, after approval, split across calls when long | Built in |
| Retrospective required to close | Yes | `PREFLIGHT_RETROSPECTIVE_REQUIRED` |
| Win tags | Optional tags | Built in |
| Pattern matching | By category, within a project and across projects. Embedding similarity is deferred. | Built in |
| Checklist export to Markdown | Included | `preflight <project> checklist --md` |
| Rule matching | Cross-project rules everywhere, single-project rules by keyword overlap | Built in |
| AI without an Anthropic key | Local Ollama | `PREFLIGHT_AI_PROVIDER` |

Hosting, backups, and an access proxy are still open. See [the design's open decisions](design.md#19-open-decisions).
