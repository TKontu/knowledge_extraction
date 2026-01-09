# Update TODO Files

Review the codebase and update TODO documentation to reflect the actual current state of development.

## Context

```bash
git log --oneline -10
```

```bash
git status --short
```

## Instructions

**CRITICAL: Be diligent and thorough. DO NOT assume any status. Verify everything by inspecting actual code.**

### 1. Locate TODO Files

Find all TODO documentation in the project:
- `TODO.md`, `TODO.txt`, or similar in project root or docs/
- Module-specific TODO files (e.g., `docs/TODO_*.md`)
- Issues or task lists in README files

### 2. Verify Each Item

For every task or checkbox in the TODO files, you MUST verify by:

- **Reading implementation files** - Confirm code exists and is complete (not a stub)
- **Checking tests** - Verify tests exist and pass
- **Running tests** - Don't trust status claims without verification
- **Reviewing git history** - Confirm what was actually merged
- **Inspecting integrations** - Ensure components are wired together, not mocked

**DO NOT mark items complete based on:**
- File existence alone (might be empty/incomplete)
- Function signatures (might be stubs)
- Comments or docstrings (code might not match)
- Assumptions or memory (always verify)

### 3. Update Criteria

Mark items as complete `[x]` ONLY if:
- Implementation exists and is functional (not hardcoded/mocked)
- Tests exist and pass
- Code is integrated into the system
- You verified by reading the actual code

Otherwise keep as incomplete `[ ]`.

### 4. Update Metrics

Verify and update any statistics in the TODO files:
- Test counts (run test framework to count)
- PR/commit counts (check git log)
- Coverage numbers (run coverage tools)
- Feature status (verify implementations)

### 5. Flag Discrepancies

Report any findings where:
- Items marked complete are actually incomplete
- Items marked incomplete are actually done
- Stubs or mocks are marked as finished features
- Test counts or other metrics are inaccurate

---

## Verification Checklist

- [ ] Read actual implementation code for each claimed completion
- [ ] Run tests to verify functionality
- [ ] Check git history for claimed merges/commits
- [ ] Verify metrics (test counts, coverage, etc.)
- [ ] Update all TODO files with accurate status
- [ ] Report discrepancies between docs and reality
