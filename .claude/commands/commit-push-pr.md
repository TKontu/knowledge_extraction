# Commit, Push, and Create PR

Stage changes, commit, push to remote, and create a pull request.

## Context
```bash
git status --short
```
```bash
git diff --cached --stat
```
```bash
git diff --stat
```
```bash
git log --oneline -5
```
```bash
git branch --show-current
```

## Instructions

1. **Branch check** - Verify the current branch name reflects the changes:
   - Is it descriptive of the work being done?
   - If on `main`/`master`: STOP and suggest creating a feature branch first
   - If branch name seems mismatched with changes: warn user and confirm before proceeding
2. **Staging check** - Review unstaged vs staged changes:
   - If relevant changes are unstaged, suggest specific `git add` commands
   - Help user decide what should be included in this commit
   - Offer to stage all related changes or specific files
3. **Secrets check** - Scan staged files for exposed secrets:
   - API keys, tokens, passwords, private keys, database URLs
   - If found: STOP and warn user, do not commit
4. Review the final staged changes
5. Write a concise commit message following conventional commits (feat/fix/refactor/docs/test/chore)
6. Commit the changes
7. Push to the current branch
8. Create a PR with:
   - Clear title summarizing the change
   - Brief description of what and why
   - Link any related issues if mentioned in commits

If no changes exist (staged or unstaged), inform the user.