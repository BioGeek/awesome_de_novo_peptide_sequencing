"""Run extraction jobs in parallel, each in its own process. usage: run_jobs.py jobs.jsonl N"""
import json, subprocess, sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed
B = os.path.expanduser("~/data/ninespecies")
jobs = [json.loads(l) for l in open(sys.argv[1])]
todo = [j for j in jobs if not os.path.exists(j["out"])]
print(f"{len(todo)} of {len(jobs)} jobs to run", flush=True)
def run(j):
    r = subprocess.run([f"{B}/.venv/bin/python", f"{B}/scripts/extract.py", json.dumps(j)],
                       capture_output=True, text=True)
    return j, r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] 
done = 0
with ThreadPoolExecutor(int(sys.argv[2])) as ex:
    for fut in as_completed([ex.submit(run, j) for j in todo]):
        j, rc, tail = fut.result(); done += 1
        print(f"[{done}/{len(todo)}] rc={rc} {tail[0] if tail else ''}", flush=True)
