import fnmatch, json, os, sys
from huggingface_hub import HfApi, snapshot_download
repo, rev, dst, pat = sys.argv[1:5]
snapshot_download(repo, revision=rev, local_dir=dst, allow_patterns=[pat])
info = HfApi().model_info(repo, revision=rev, files_metadata=True)
want = {s.rfilename: s.size for s in info.siblings if fnmatch.fnmatch(s.rfilename, pat)}
got = {f: os.path.getsize(os.path.join(dst, f)) for f in want}
print(json.dumps({"want": want, "got": got}))
assert want and got == want, "size mismatch"
