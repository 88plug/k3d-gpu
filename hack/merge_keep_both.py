#!/usr/bin/env python3
# merge_keep_both.py FILE [ours]: resolve conflict blocks (ours+theirs, or ours only).
import re, sys
p = sys.argv[1]; mode = sys.argv[2] if len(sys.argv) > 2 else "both"
s = open(p).read()
pat = re.compile(r'^<<<<<<< [^\n]*\n(.*?)^=======\n(.*?)^>>>>>>> [^\n]*\n', re.S | re.M)
def res(m):
    a, b = m.group(1), m.group(2)
    if mode == "ours": return a
    table = a.lstrip().startswith('|') and b.lstrip().startswith('|')
    sep = '' if table or a.endswith('\n\n') or len(a.splitlines()) <= 6 else '\n'
    return a + sep + b
n = len(pat.findall(s)); s = pat.sub(res, s)
assert '<<<<<<<' not in s and '>>>>>>>' not in s
open(p, 'w').write(s); print(p, n, "blocks", mode)
