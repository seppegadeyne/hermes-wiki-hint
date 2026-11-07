# Constraints

Last reviewed: 2026-11-07

## Floor (always enforced)
- The plugin never writes to the wiki from hooks (see AGENTS.md).
- No private paths, hostnames or names in code, tests or docs.
- No new suppression comments (`# noqa`, `# type: ignore`) and no bare `except:`.
- No stubs or silent failures (empty `except: pass`).
- No skipped or deleted tests without a reason in the commit message.
- No secrets in source.
- This file is not weakened to make a change pass.

## Enforced with numbers
| Dimension | Rule | Checked by | Runs at |
|---|---|---|---|
| Tests | all pass (46 total today) | `python3 test_wiki_hint.py` and `python3 test_maintenance.py` | every change |
| Plugin validity | doctor passes | `hermes plugins doctor . --ci` | every change |

## Measured, not yet enforced (ratchets)
| Metric | Today | Direction | Command |
|---|---|---|---|
| `# type: ignore` comments | 2 | must not grow | `git grep -nE "# noqa\|# type: ignore" \| wc -l` |
| Bare `except:` | 0 | must stay 0 | `git grep -nE "except:" -- '*.py' \| wc -l` |
| Skipped tests | 0 | must stay 0 | `git grep -nE "pytest.mark.skip\|unittest.skip\|\.skip\(" \| wc -l` |

No linter or type checker is configured.

## Exceptions
| ID | Rule | Path | Reason | Expires |
|---|---|---|---|---|
