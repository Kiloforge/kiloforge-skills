---
name: kf-new-track
description: Create a new track with specification and phased implementation plan
metadata:
  argument-hint: <feature|bug|chore|refactor> <name>
---

# New Track

Create a new track (feature, bug fix, chore, or refactor) with a detailed specification and phased implementation plan.

## Use this skill when

- Working on new track tasks or workflows
- Needing guidance, best practices, or checklists for new track

## Do not use this skill when

- The task is unrelated to new track
- You need a different domain or tool outside this scope

## Instructions

- Clarify goals, constraints, and required inputs.
- Apply relevant best practices and validate outcomes.
- Provide actionable steps and verification.
- If detailed examples are required, open `resources/implementation-playbook.md`.

## Pre-flight Checks

1. Run pre-flight check:
   ```bash
   eval "$(.agent/kf/bin/kf-preflight)"
   ```
   This verifies all required metadata files exist on the primary branch and sets `PRIMARY_BRANCH`. If it fails, it prints an error suggesting `/kf-setup` — **HALT.**

2. Load context files:
   - Read `.agent/kf/product.yaml` for product context
   - Read `.agent/kf/tech-stack.yaml` for technical context
   - Read `.agent/kf/workflow.yaml` for TDD/commit preferences

## Track Classification

Determine track type based on description or ask user:

```
What type of track is this?

1. Feature - New functionality
2. Bug - Fix for existing issue
3. Chore - Maintenance, dependencies, config
4. Refactor - Code improvement without behavior change
```

## Interactive Specification Gathering

**CRITICAL RULES:**

- Ask ONE question per turn
- Wait for user response before proceeding
- Tailor questions based on track type
- Maximum 6 questions total

### For Feature Tracks

**Q1: Feature Summary**

```
Describe the feature in 1-2 sentences.
[If argument provided, confirm: "You want to: {argument}. Is this correct?"]
```

**Q2: User Story**

```
Who benefits and how?

Format: As a [user type], I want to [action] so that [benefit].
```

**Q3: Acceptance Criteria**

```
What must be true for this feature to be complete?

List 3-5 acceptance criteria (one per line):
```

**Q4: Dependencies**

```
Does this depend on any existing code, APIs, or other tracks?

1. No dependencies
2. Depends on existing code (specify)
3. Depends on incomplete track (specify)
```

**Q5: Scope Boundaries**

```
What is explicitly OUT of scope for this track?
(Helps prevent scope creep)
```

**Q6: Technical Considerations (optional)**

```
Any specific technical approach or constraints?
(Press enter to skip)
```

### For Bug Tracks

**Q1: Bug Summary**

```
What is broken?
[If argument provided, confirm]
```

**Q2: Steps to Reproduce**

```
How can this bug be reproduced?
List steps:
```

**Q3: Expected vs Actual Behavior**

```
What should happen vs what actually happens?
```

**Q4: Affected Areas**

```
What parts of the system are affected?
```

**Q5: Root Cause Hypothesis (optional)**

```
Any hypothesis about the cause?
(Press enter to skip)
```

### For Chore/Refactor Tracks

**Q1: Task Summary**

```
What needs to be done?
[If argument provided, confirm]
```

**Q2: Motivation**

```
Why is this work needed?
```

**Q3: Success Criteria**

```
How will we know this is complete?
```

**Q4: Risk Assessment**

```
What could go wrong? Any risky changes?
```

## Track ID Generation

Generate track ID in format: `{shortname}_{YYYYMMDDHHmmssZ}`

- Extract shortname from feature/bug summary (2-3 words, lowercase, hyphenated)
- Use current UTC date and time so creation time is encoded in the ID
- The trailing `Z` explicitly marks UTC — always include it
- Example: `user-auth_20250115143022Z`, `nav-bug_20250115091205Z`

Validate uniqueness:

- Check `.agent/kf/tracks.yaml` for existing IDs
- If collision, append counter: `user-auth_20250115143022Z_2`

## Specification Generation

Create `.agent/kf/tracks/{trackId}/track.yaml` with initial spec content:

```yaml
id: {trackId}
title: "{Track Title}"
type: feature|bug|chore|refactor
status: pending
created: YYYY-MM-DD
updated: YYYY-MM-DD
spec:
  summary: "{1-2 sentence summary}"
  context: |
    {Product context from product.yaml relevant to this track}
  acceptance_criteria:
    - "{Criterion 1}"
    - "{Criterion 2}"
    - "{Criterion 3}"
  dependencies: "{List or None}"
  out_of_scope: |
    {Explicit exclusions}
  technical_notes: |
    {Technical considerations or None specified}
plan:
  - phase: "Phase Name"
    tasks:
      - text: "Task description"
        done: false
extra: {}
```

Note: The `plan:` section will be populated after spec approval (see Plan Generation below). Initially it can contain an empty list `[]`.

## User Review of Spec

Display the generated spec and ask:

```
Here is the specification I've generated:

{spec content}

Is this specification correct?
1. Yes, proceed to plan generation
2. No, let me edit (opens for inline edits)
3. Start over with different inputs
```

## Plan Generation

After spec approval, populate the `plan:` section of `.agent/kf/tracks/{trackId}/track.yaml`:

### Plan Structure

```yaml
plan:
  - phase: "Setup/Foundation"
    tasks:
      - text: "Task 1.1 description"
        done: false
      - text: "Task 1.2 description"
        done: false
      - text: "Verify: {verification step for phase 1}"
        done: false
  - phase: "Core Implementation"
    tasks:
      - text: "Task 2.1 description"
        done: false
      - text: "Task 2.2 description"
        done: false
      - text: "Verify: {verification step for phase 2}"
        done: false
  - phase: "Integration"
    tasks:
      - text: "Task 3.1 description"
        done: false
  - phase: "Final Verification"
    tasks:
      - text: "All acceptance criteria met"
        done: false
      - text: "Tests passing"
        done: false
      - text: "Documentation updated (if applicable)"
        done: false
      - text: "Ready for review"
        done: false
```

### Phase Guidelines

- Group related tasks into logical phases
- Each phase should be independently verifiable
- Include verification task after each phase
- TDD tracks: Include test writing tasks before implementation tasks
- Typical structure:
  1. **Setup/Foundation** - Initial scaffolding, interfaces
  2. **Core Implementation** - Main functionality
  3. **Integration** - Connect with existing system
  4. **Polish** - Error handling, edge cases, docs

## User Review of Plan

Display the generated plan and ask:

```
Here is the implementation plan:

{plan content}

Is this plan correct?
1. Yes, create the track
2. No, let me edit (opens for inline edits)
3. Add more phases/tasks
4. Start over
```

## Track Creation

After plan approval:

1. Create directory and single track file:

   ```
   .agent/kf/tracks/{trackId}/
   └── track.yaml
   ```

2. Write `track.yaml` with the full spec and plan content gathered above.

3. Register in `.agent/kf/tracks.yaml`:

   ```bash
   .agent/kf/bin/kf-track add {trackId} --title "{title}" --type {type}
   ```

4. If the track has dependencies on other tracks, register them:

   ```bash
   .agent/kf/bin/kf-track deps add {trackId} {dep-id}
   ```

## Completion Message

```
Track created successfully!

Track ID: {trackId}
Location: .agent/kf/tracks/{trackId}/

Files created:
- track.yaml - Specification, plan, and metadata (single source of truth)

Registered in:
- .agent/kf/tracks.yaml

Next steps:
1. Review track.yaml, make any edits
2. Run /kf-implement {trackId} to start implementation
3. Run /kf-status to see project progress
```

## Error Handling

- If directory creation fails: Halt and report, do not register in tracks.yaml
- If track.yaml write fails: Clean up partial track, report error
- If `kf-track add` fails: Warn user to manually register track in tracks.yaml
