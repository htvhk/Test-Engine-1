#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[2] / 'crates/te1-search/src/lib.rs'
text = path.read_text(encoding='utf-8')
old = '''            let quiet_history = if tactical {\n                0\n            } else {\n                self.histories.quiet_score(board, mv, previous)\n            };\n            let score = if packed == tt_move {\n'''
new = '''            let quiet_history = if !tactical && self.options.use_adaptive_lmr {\n                self.histories.quiet_score(board, mv, previous)\n            } else {\n                0\n            };\n            let score = if packed == tt_move {\n'''
if text.count(old) != 1:
    raise SystemExit(f'expected one quiet-history target, found {text.count(old)}')
text = text.replace(old, new, 1)
old = '''            } else if tactical {\n                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)\n            } else {\n                quiet_history\n            };\n'''
new = '''            } else if tactical {\n                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)\n            } else if self.options.use_adaptive_lmr {\n                quiet_history\n            } else {\n                self.histories.quiet_score(board, mv, previous)\n            };\n'''
if text.count(old) != 1:
    raise SystemExit(f'expected one quiet-score target, found {text.count(old)}')
text = text.replace(old, new, 1)
path.write_text(text, encoding='utf-8', newline='\n')
print('TE1_ADAPTIVE_LMR_R1_BASELINE_PATH_REPAIRED')
