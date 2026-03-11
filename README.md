# Kiloforge Skills

[Claude Code](https://claude.com/claude-code) skills for AI-powered project management with [Kiloforge](https://github.com/Kiloforge/kiloforge).

## Install

### macOS / Linux

```bash
git clone --depth 1 https://github.com/Kiloforge/kiloforge-skills.git /tmp/kf-skills && cp -r /tmp/kf-skills/kf-* ~/.claude/skills/ && rm -rf /tmp/kf-skills
```

### Windows (PowerShell)

```powershell
git clone --depth 1 https://github.com/Kiloforge/kiloforge-skills.git $env:TEMP\kf-skills; Copy-Item -Recurse $env:TEMP\kf-skills\kf-* ~\.claude\skills\; Remove-Item -Recurse -Force $env:TEMP\kf-skills
```

After installing, open Claude Code in any project and run `/kf-getting-started` to bootstrap your first Kiloforge project.

## Skills

| Skill | Description |
|-------|-------------|
| `/kf-getting-started` | Interactive project bootstrapper |
| `/kf-setup` | Initialize Kiloforge artifacts |
| `/kf-architect` | Research codebase, create track specs |
| `/kf-developer` | Claim and implement tracks |
| `/kf-implement` | Execute tasks from implementation plans |
| `/kf-status` | Display project status and next actions |
| `/kf-new-track` | Create a new track with spec and plan |
| `/kf-manage` | Archive, restore, delete, rename tracks |
| `/kf-interactive` | General-purpose kf-aware assistant |
| `/kf-report` | Generate project timeline and velocity reports |
| `/kf-validate` | Validate project artifacts |
| `/kf-repair` | Audit and repair system integrity |
| `/kf-conflict-resolver` | Resolve git merge conflicts |
| `/kf-revert` | Git-aware undo by logical work unit |
| `/kf-bulk-archive` | Archive all completed tracks |
| `/kf-compact-archive` | Remove archived track directories |
| `/kf-advisor-product` | Product strategy and competitive analysis |
| `/kf-advisor-reliability` | Codebase reliability audit |
| `/kf-bin` | List available CLI tools (Kiloforge agents only) |
| `/kf-data-guardian` | Data integrity guard (embedded by other skills) |
| `/kf-parallel` | Deprecated — redirects to kf-architect/kf-developer |

## Update

Re-run the install command to update to the latest skills.

## License

MIT
