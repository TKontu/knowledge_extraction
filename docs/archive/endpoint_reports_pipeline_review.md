# Pipeline Review: POST /projects/{project_id}/reports (Table Report Grouping)

## Flow
```
api/v1/reports.py:create_report
  → services/reports/service.py:ReportService.generate
    → _gather_data()
    → _generate_table_report() [async]
      → _aggregate_by_source() OR _aggregate_by_domain()
        → schema_table_generator.py:get_flattened_columns_for_source()
        → smart_merge.py:SmartMergeService.merge_column() [for domain]
          → LLMClient.complete()
```

## Critical - ✅ FIXED

- [x] **src/ke_mcp/client.py:401** - MCP client uses wrong default for `group_by`
  - Changed default from `"source_group"` to `"source"`

- [x] **src/services/reports/smart_merge.py:162-166** - Wrong keyword argument for `LLMClient.complete()`
  - Changed `prompt=prompt` to `user_prompt=prompt` and reordered arguments

- [x] **src/services/reports/smart_merge.py:162+234** - Type mismatch: `complete()` returns `dict`, code expected `str`
  - Updated `_parse_merge_response()` to accept `dict` directly instead of parsing string

## Important - ✅ FIXED

- [x] **src/services/reports/service.py:640** - SmartMergeService ignores config settings
  - Now passes `settings.smart_merge_max_candidates` and `settings.smart_merge_min_confidence`

- [x] **src/services/reports/service.py:711-718** - `avg_confidence` not set when `include_merge_metadata=False`
  - Changed `merge_column()` to always return confidence separately; confidence now collected regardless of metadata flag

- [x] **src/ke_mcp/tools/reports.py** - Missing `include_merge_metadata` parameter
  - Added parameter to MCP tool and client

## Minor - ✅ FIXED

- [x] **src/services/reports/service.py:662** - Type hint uses `any` instead of `Any`
  - Changed to `Any` and added import from `typing`

---

## Summary

All 7 issues have been fixed and verified with passing tests:
- `tests/test_report_table.py`: 14/14 passed
- `tests/test_report_service.py`: 15/15 passed
- `tests/ke_mcp/`: 8/8 passed (2 skipped - integration tests)

The `group_by="domain"` path is now fully functional.
