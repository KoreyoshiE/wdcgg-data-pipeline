"""Real frozen-source reproduction and current-provider reacquisition entry point."""
from __future__ import annotations
import argparse, csv, hashlib, json, shutil, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path: sys.path.insert(0, str(SRC))
from wdcgg_pipeline import acquisition, analysis, experiments
from wdcgg_pipeline.provenance import sha256, verify_sha256

def write_csv(path, rows, columns=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None: columns = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as h:
        w=csv.DictWriter(h, fieldnames=columns, extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def hash_index(data_root):
    paths=list(data_root.rglob("*.tar.gz")); result={}
    for i,p in enumerate(paths,1):
        result[sha256(p)]=p
        print(f"hash {i}/{len(paths)} {p.name}", flush=True)
    return result

def compare_csv(frozen, reproduced, ignore_columns=()):
    a=pd.read_csv(frozen); b=pd.read_csv(reproduced)
    a=a.drop(columns=list(ignore_columns),errors="ignore"); b=b.drop(columns=list(ignore_columns),errors="ignore")
    if list(a.columns)!=list(b.columns): return False, np.nan, len(b)-len(a), "column mismatch"
    if len(a)!=len(b): return False, np.nan, len(b)-len(a), "row mismatch"
    maxdiff=0.0
    for c in a.columns:
        an=pd.to_numeric(a[c], errors="coerce"); bn=pd.to_numeric(b[c], errors="coerce")
        numeric=(an.notna()|bn.notna()).sum() >= max(1, len(a)//2)
        if numeric:
            d=np.abs(an.to_numpy(float)-bn.to_numpy(float)); finite=d[np.isfinite(d)]
            if len(finite): maxdiff=max(maxdiff,float(finite.max()))
            if not np.allclose(an, bn, rtol=1e-9, atol=1e-10, equal_nan=True): return False,maxdiff,0,f"numeric mismatch: {c}"
        elif not a[c].fillna("").astype(str).equals(b[c].fillna("").astype(str)):
            return False,maxdiff,0,f"text mismatch: {c}"
    return True,maxdiff,0,"identical or numerically equivalent"

def frozen_run(data_root, out, skip_validation, make_figures, verify):
    frozen=ROOT/"results"; manifest=pd.read_csv(ROOT/"provenance/DOWNLOAD_MANIFEST_V2.csv")
    metadata=pd.read_csv(ROOT/"provenance/HARMONISATION_RESULTS_V2.csv", dtype=str).fillna("")
    by_hash=hash_index(data_root)
    # Prefer exact frozen archive containers. WDCGG can regenerate a tar/gzip
    # wrapper while leaving the scientific hourly member byte-identical. Such a
    # fallback is accepted only after the locked observation SHA-256 matches and
    # is explicitly reported as archive-container drift.
    candidate_paths={p.name.lower():p for p in data_root.rglob("*.tar.gz")}
    out.mkdir(parents=True,exist_ok=True); (out/"figures").mkdir(exist_ok=True)
    shutil.copy2(ROOT/"data/manifests/ne_110m_admin_0_countries.geojson",out/"ne_110m_admin_0_countries.geojson")
    v1=out/"_v1"; v1.mkdir(exist_ok=True)
    shutil.copy2(ROOT/"data/manifests/FROZEN_CANDIDATE_DATASETS_V1.csv",v1/"CANDIDATE_DATASETS.csv")
    results=[]; months=[]; archive_drift=[]; exact_archives=0
    for i,item in metadata.iterrows():
        candidate=f"{item.gas.lower()}_{item.wdcgg_id.lower()}"
        archive=by_hash.get(item.archive_sha256)
        if archive is None:
            archive=candidate_paths.get(candidate+".tar.gz")
            if archive is None: raise SystemExit(f"missing frozen archive and candidate fallback for {candidate}")
            archive_drift.append({"candidate_id":candidate,"expected_archive_sha256":item.archive_sha256,"actual_archive_sha256":sha256(archive),"expected_observation_sha256":item.observation_sha256})
        else:
            verify_sha256(archive,item.archive_sha256); exact_archives+=1
        result,mrows=acquisition.process(item.to_dict(),archive,out/"extracted"/candidate)
        if result["observation_sha256"] != item.observation_sha256:
            raise SystemExit(f"scientific source mismatch for {candidate}: locked observation hash not reproduced")
        results.append(result); months.extend(mrows)
        print(f"process {i+1}/38 {candidate}: {result['record_count']} rows",flush=True)
    write_csv(out/"HARMONISATION_RESULTS_V2.csv",results,list(metadata.columns))
    write_csv(out/"MONTHLY_PROVIDER_VALID_V2.csv",months)
    analysis.V1=v1; analysis.V2=out; analysis.FIG=out/"figures"; analysis.main()
    if not skip_validation:
        experiments.V2=out; experiments.FIG=out/"figures"; experiments.main()
    comparisons=[]
    generated=sorted(p for p in out.glob("*.csv") if (frozen/p.name).exists())
    for p in generated:
        provenance_tables={"HARMONISATION_RESULTS_V2.csv","FINAL_GLOBAL_COHORT_V2.csv"}
        ignored=("archive_path","archive_sha256","observation_path") if p.name in provenance_tables else ()
        same,diff,rowdiff,note=compare_csv(frozen/p.name,p,ignored)
        if ignored and same: note="scientifically equivalent; local paths and archive-container hash excluded, observation hash verified"
        comparisons.append({"artifact":p.name,"frozen_path":str(frozen/p.name),"reproduced_path":str(p),"comparison_method":"column/row/text exact; numeric rtol=1e-9 atol=1e-10","max_abs_difference":diff,"row_difference":rowdiff,"hash_equal":sha256(frozen/p.name)==sha256(p),"scientifically_equivalent":same,"notes":note})
    write_csv(out/"REPRODUCTION_COMPARISON.csv",comparisons)
    mismatches=[r for r in comparisons if not r["scientifically_equivalent"]]
    (out/"ARCHIVE_VERSION_DRIFT.json").write_text(json.dumps(archive_drift,indent=2)+"\n",encoding="utf-8")
    summary={"mode":"frozen","source_archives_processed":len(results),"exact_frozen_archive_containers":exact_archives,"archive_container_drift_with_identical_observation":len(archive_drift),"rows_processed":sum(int(r["record_count"]) for r in results),"outputs_compared":len(comparisons),"scientific_mismatches":len(mismatches),"status":"PASS" if not mismatches else "MISMATCH"}
    (out/"REPRODUCTION_SUMMARY.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))
    if verify and mismatches: raise SystemExit(2)

def reacquire(out):
    out.mkdir(parents=True,exist_ok=True)
    print("Fresh mode retrieves the current WDCGG catalogue and archives; compare returned DOI/version/hash against provenance/DOWNLOAD_MANIFEST_V2.csv.")
    acquisition.EXP=out; acquisition.RAW=out/"data"; acquisition.main()

def main():
    p=argparse.ArgumentParser(); p.add_argument("--mode",choices=["frozen","reacquire"],default="frozen")
    p.add_argument("--data-root",type=Path); p.add_argument("--output-dir",type=Path,default=ROOT/"reproduction_output")
    p.add_argument("--skip-validation-experiments",action="store_true"); p.add_argument("--figures",action="store_true"); p.add_argument("--verify-against-freeze",action="store_true")
    a=p.parse_args()
    if a.mode=="frozen":
        if not a.data_root: p.error("--data-root is required in frozen mode")
        frozen_run(a.data_root,a.output_dir,a.skip_validation_experiments,a.figures,a.verify_against_freeze)
    else: reacquire(a.output_dir)
if __name__=="__main__": main()
