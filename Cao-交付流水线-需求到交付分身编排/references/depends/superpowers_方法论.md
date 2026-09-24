# obra/superpowers · 方法论原文快照

> 【自包含副本】来源：github.com/obra/superpowers README（MIT，作者 Jesse Vincent / Prime Radiant）。
> 抓取日期：2026-09-20。本文为其 README 中"工作流与哲学"部分的完整快照（安装章节略）。
> 原 skill 若升级，本副本不自动跟随；完整框架请从原仓库获取。

## How it works

It starts from the moment you fire up your coding agent. As soon as it sees that you're building something, it _doesn't_ just jump into trying to write code. Instead, it steps back and asks you what you're really trying to do.

Once it's teased a spec out of the conversation, it shows it to you in chunks short enough to actually read and digest.

After you've signed off on the design, your agent puts together an implementation plan that's clear enough for an enthusiastic junior engineer with poor taste, no judgement, no project context, and an aversion to testing to follow. It emphasizes true red/green TDD, YAGNI (You Aren't Gonna Need It), and DRY.

Next up, once you say "go", it launches a _subagent-driven-development_ process, having agents work through each engineering task, inspecting and reviewing their work, and continuing forward. It's not uncommon for your agent to work autonomously for a couple hours at a time without deviating from the plan you put together.

## The Basic Workflow

1. __brainstorming__ - Activates before writing code. Refines rough ideas through questions, explores alternatives, presents design in sections for validation. Saves design document.
2. __using-git-worktrees__ - Activates after design approval. Creates isolated workspace on new branch, runs project setup, verifies clean test baseline.
3. __writing-plans__ - Activates with approved design. Breaks work into bite-sized tasks (2-5 minutes each). Every task has exact file paths, complete code, verification steps.
4. __subagent-driven-development__ or __executing-plans__ - Activates with plan. Either dispatches a fresh subagent per task with a review after each (most thorough), or implements every task inline in the current session with one fresh review of the whole branch at the end (cheapest).
5. __test-driven-development__ - Activates during implementation. Enforces RED-GREEN-REFACTOR: write failing test, watch it fail, write minimal code, watch it pass, commit.
6. __requesting-code-review__ - Activates between tasks. Reviews against plan, reports issues by severity. Critical issues block progress.
7. __finishing-a-development-branch__ - Activates when tasks complete. Verifies tests, presents options (merge/PR/keep/discard), cleans up worktree.

__The agent checks for relevant skills before any task.__ Mandatory workflows, not suggestions.

## What's Inside (Skills Library)

__Testing__
- __test-driven-development__ - RED-GREEN-REFACTOR cycle

__Debugging__
- __systematic-debugging__ - 4-phase root cause process
- __verification-before-completion__ - Ensure it's actually fixed
- __diagnosing-superpowers__ - Work out what went wrong in a session, with evidence

__Collaboration__
- __brainstorming__ - Socratic design refinement
- __writing-plans__ - Detailed implementation plans
- __executing-plans__ - Inline plan execution: one context, one final review
- __dispatching-parallel-agents__ - Concurrent subagent workflows
- __requesting-code-review__ / __receiving-code-review__ - Review flow
- __using-git-worktrees__ - Parallel development branches
- __finishing-a-development-branch__ - Merge/PR decision workflow
- __subagent-driven-development__ - Fast iteration with two-stage review (spec compliance, then code quality)

__Meta__
- __writing-skills__ / __using-superpowers__

## Philosophy

- __Test-Driven Development__ - Write tests first, always
- __Systematic over ad-hoc__ - Process over guessing
- __Complexity reduction__ - Simplicity as primary goal
- __Evidence over claims__ - Verify before declaring success
