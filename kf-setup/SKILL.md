---
name: kf-setup
description: Initialize project with Kiloforge artifacts (product definition,
  tech stack, workflow, style guides)
metadata:
  argument-hint: "[--resume]"
---

# Kiloforge Setup

Initialize or resume Kiloforge project setup. This command creates foundational project documentation through interactive Q&A.

## Use this skill when

- Working on kiloforge setup tasks or workflows
- Needing guidance, best practices, or checklists for kiloforge setup

## Do not use this skill when

- The task is unrelated to kiloforge setup
- You need a different domain or tool outside this scope

## Instructions

- Clarify goals, constraints, and required inputs.
- Apply relevant best practices and validate outcomes.
- Provide actionable steps and verification.
- If detailed examples are required, open `resources/implementation-playbook.md`.

## Pre-flight Checks

1. **Determine primary branch:**
   ```
   Kiloforge artifacts must live on your primary coordination branch
   for tracks and other kf-* skills to function properly.

   What is your primary branch?

   1. main (default)
   2. master
   3. develop
   4. Type your own
   ```
   Store the answer in `setup_state.json` as `"primary_branch": "<branch>"`.

   Then ask:
   ```
   Should I commit the setup artifacts to {primary_branch} when complete?

   1. Yes, commit to {primary_branch} (recommended)
   2. No, I'll commit manually
   ```
   Store as `"auto_commit": true|false`.

2. Check if `.agent/kf/` directory already exists in the project root:
   - If `.agent/kf/product.yaml` or `.agent/kf/tracks.yaml` exists: Ask user whether to resume setup or reinitialize
   - If `.agent/kf/setup_state.json` exists with incomplete status: Offer to resume from last step

3. Detect project type by checking for existing indicators:
   - **Greenfield (new project)**: No .git, no package.json, no requirements.txt, no go.mod, no src/ directory
   - **Brownfield (existing project)**: Any of the above exist

4. Load or create `.agent/kf/setup_state.json`:
   ```json
   {
     "status": "in_progress",
     "primary_branch": "main",
     "auto_commit": true,
     "project_type": "greenfield|brownfield",
     "current_section": "product|guidelines|tech_stack|workflow|styleguides",
     "current_question": 1,
     "completed_sections": [],
     "answers": {},
     "files_created": [],
     "started_at": "ISO_TIMESTAMP",
     "last_updated": "ISO_TIMESTAMP"
   }
   ```

## Interactive Q&A Protocol

**CRITICAL RULES:**

- Ask ONE question per turn
- Wait for user response before proceeding
- Offer 2-3 suggested answers plus "Type your own" option
- Maximum 5 questions per section
- Update `setup_state.json` after each successful step
- Validate file writes succeeded before continuing

### Section 1: Product Definition (max 5 questions)

**Q1: Project Name**

```
What is your project name?

Suggested:
1. [Infer from directory name]
2. [Infer from package.json/go.mod if brownfield]
3. Type your own
```

**Q2: Project Description**

```
Describe your project in one sentence.

Suggested:
1. A web application that [does X]
2. A CLI tool for [doing Y]
3. Type your own
```

**Q3: Problem Statement**

```
What problem does this project solve?

Suggested:
1. Users struggle to [pain point]
2. There's no good way to [need]
3. Type your own
```

**Q4: Target Users**

```
Who are the primary users?

Suggested:
1. Developers building [X]
2. End users who need [Y]
3. Internal teams managing [Z]
4. Type your own
```

**Q5: Key Goals (optional)**

```
What are 2-3 key goals for this project? (Press enter to skip)
```

### Section 2: Product Guidelines (max 3 questions)

**Q1: Voice and Tone**

```
What voice/tone should documentation and UI text use?

Suggested:
1. Professional and technical
2. Friendly and approachable
3. Concise and direct
4. Type your own
```

**Q2: Design Principles**

```
What design principles guide this project?

Suggested:
1. Simplicity over features
2. Performance first
3. Developer experience focused
4. User safety and reliability
5. Type your own (comma-separated)
```

### Section 3: Tech Stack (max 5 questions)

For **brownfield projects**, first analyze existing code:

- Run `Glob` to find package.json, requirements.txt, go.mod, Cargo.toml, etc.
- Parse detected files to pre-populate tech stack
- Present findings and ask for confirmation/additions

**Q1: Primary Language(s)**

```
What primary language(s) does this project use?

[For brownfield: "I detected: Python 3.11, JavaScript. Is this correct?"]

Suggested:
1. TypeScript
2. Python
3. Go
4. Rust
5. Type your own (comma-separated)
```

**Q2: Frontend Framework (if applicable)**

```
What frontend framework (if any)?

Suggested:
1. React
2. Vue
3. Next.js
4. None / CLI only
5. Type your own
```

**Q3: Backend Framework (if applicable)**

```
What backend framework (if any)?

Suggested:
1. Express / Fastify
2. Django / FastAPI
3. Go standard library
4. None / Frontend only
5. Type your own
```

**Q4: Database (if applicable)**

```
What database (if any)?

Suggested:
1. PostgreSQL
2. MongoDB
3. SQLite
4. None / Stateless
5. Type your own
```

**Q5: Infrastructure**

```
Where will this be deployed?

Suggested:
1. AWS (Lambda, ECS, etc.)
2. Vercel / Netlify
3. Self-hosted / Docker
4. Not decided yet
5. Type your own
```

### Section 4: Workflow Preferences (max 2 questions)

**Q1: TDD Strictness**

```
How strictly should TDD be enforced?

Suggested:
1. Strict - tests required before implementation
2. Moderate - tests encouraged, not blocked
3. Flexible - tests recommended for complex logic
```

**Q2: Commit Strategy**

```
What commit strategy should be followed?

Suggested:
1. Conventional Commits (feat:, fix:, etc.)
2. Descriptive messages, no format required
3. Squash commits per task
```

**Defaults (not asked):**

The following are set automatically and do not require user input:

- **Code review**: Optional / self-review OK
- **Verification checkpoints**: Track completion only — manual verification is required only when an entire track is complete. Individual phases and tasks do not require manual sign-off.

### Section 5: Code Style Guides (max 2 questions)

**Q1: Languages to Include**

```
Which language style guides should be generated?

[Based on detected languages, pre-select]

Options:
1. TypeScript/JavaScript
2. Python
3. Go
4. Rust
5. All detected languages
6. Skip style guides
```

**Q2: Existing Conventions**

```
Do you have existing linting/formatting configs to incorporate?

[For brownfield: "I found .eslintrc, .prettierrc. Should I incorporate these?"]

Suggested:
1. Yes, use existing configs
2. No, generate fresh guides
3. Skip this step
```

## Artifact Generation

After completing Q&A, generate the following files:

### 1. .agent/kf/config.yaml

```yaml
project_name: "{project name}"
primary_branch: "{primary_branch from pre-flight}"
```

### 2. .agent/kf/product.yaml

Template populated with Q&A answers for:

- Project name and description
- Problem statement
- Target users
- Key goals

### 3. .agent/kf/product-guidelines.yaml

Template populated with:

- Voice and tone
- Design principles
- Any additional standards

### 4. .agent/kf/tech-stack.yaml

Template populated with:

- Languages (with versions if detected)
- Frameworks (frontend, backend)
- Database
- Infrastructure
- Key dependencies (for brownfield, from package files)

### 5. .agent/kf/workflow.yaml

Template populated with:

- TDD policy and strictness level
- Commit strategy and conventions
- Code review: "Optional / self-review OK" (default)
- Verification checkpoints: "Track completion only" (default)
- Task lifecycle definition (pending → in-progress → testing → complete → blocked)

### 6. .agent/kf/tracks.yaml

```yaml
tracks: {}
```

### 7. .agent/kf/tracks/ directory

Create the tracks directory and empty dependency/conflict state files:

```bash
mkdir -p .agent/kf/tracks
```

```yaml
# .agent/kf/tracks/deps.yaml
# Track Dependency Graph
#
# PROTOCOL:
#   Canonical source for track dependency ordering (adjacency list).
#   Each key is a track ID; its value is a list of prerequisite track IDs.
#
# RULES:
#   - Only pending/in-progress tracks listed. Completed tracks pruned on cleanup.
#   - Architect appends entries when creating tracks.
#   - Developer checks deps before claiming: all deps must be completed.
#   - Cycles are forbidden.
```

```yaml
# .agent/kf/tracks/conflicts.yaml
# Track Conflict Pairs
#
# PROTOCOL:
#   Records pairs of tracks that risk merge conflicts if worked in parallel.
#   Each key is "{lower-id}/{higher-id}" (alphabetical).
#
# RULES:
#   - Architect adds pairs when genuine file overlap exists.
#   - Pairs auto-cleaned when either track completes.
```

### 8. .agent/kf/bin/kf-primary-branch

Install the shared helper script that resolves the primary branch. Copy from `$SKILL_DIR/../bin/kf-primary-branch`:

```bash
mkdir -p .agent/kf/bin
cp "$SKILL_DIR/../bin/kf-primary-branch" .agent/kf/bin/kf-primary-branch
chmod +x .agent/kf/bin/kf-primary-branch
```

If the source file is not available, create it directly:

```sh
#!/bin/sh
# Resolve the Kiloforge primary branch from config.yaml.
# Tries local file first, then git HEAD, defaults to "main".
PRIMARY_BRANCH=""
if [ -f .agent/kf/config.yaml ]; then
  PRIMARY_BRANCH=$(awk '/^primary_branch:/{print $2}' .agent/kf/config.yaml)
fi
if [ -z "$PRIMARY_BRANCH" ]; then
  PRIMARY_BRANCH=$(git show HEAD:.agent/kf/config.yaml 2>/dev/null | awk '/^primary_branch:/{print $2}')
fi
echo "${PRIMARY_BRANCH:-main}"
```

### 9. .agent/kf/code_styleguides/

Generate selected style guides from `$CLAUDE_PLUGIN_ROOT/templates/code_styleguides/`

## State Management

After each successful file creation:

1. Update `setup_state.json`:
   - Add filename to `files_created` array
   - Update `last_updated` timestamp
   - If section complete, add to `completed_sections`
2. Verify file exists with `Read` tool

## Completion

When all files are created:

1. Set `setup_state.json` status to "complete"
2. **If `auto_commit` is true:**
   - Verify the current branch is `{primary_branch}`. If not, warn and ask to switch.
   - Stage all `.agent/kf/` files: `git add .agent/kf/`
   - Commit: `git commit -m "chore(kf): initialize kiloforge project artifacts"`
   - Inform the user the commit was made to `{primary_branch}`
3. Display summary:

   ```
   Kiloforge setup complete!

   Created artifacts:
   - .agent/kf/config.yaml
   - .agent/kf/product.yaml
   - .agent/kf/product-guidelines.yaml
   - .agent/kf/tech-stack.yaml
   - .agent/kf/workflow.yaml
   - .agent/kf/tracks.yaml
   - .agent/kf/tracks/deps.yaml
   - .agent/kf/tracks/conflicts.yaml
   - .agent/kf/bin/kf-primary-branch
   - .agent/kf/code_styleguides/[languages]

   [If committed: "Artifacts committed to {primary_branch}."]
   [If not committed: "⚠ Remember to commit .agent/kf/ to {primary_branch} — kf-* skills require these artifacts on the primary branch."]

   Next steps:
   1. Review generated files and customize as needed
   2. Run /kf-architect to design and create your first track
   ```

## Resume Handling

If `--resume` argument or resuming from state:

1. Load `setup_state.json`
2. Skip completed sections
3. Resume from `current_section` and `current_question`
4. Verify previously created files still exist
5. If files missing, offer to regenerate

## Error Handling

- If file write fails: Halt and report error, do not update state
- If user cancels: Save current state for future resume
- If state file corrupted: Offer to start fresh or attempt recovery
