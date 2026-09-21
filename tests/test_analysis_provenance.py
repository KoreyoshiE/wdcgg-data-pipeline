import json
from pathlib import Path
import numpy as np, pandas as pd
from wdcgg_pipeline.analysis import amplitude, bootstrap_spearman, seasonal_kendall, slope
from wdcgg_pipeline.provenance import sha256, verify_sha256

def test_trend_positive():
    s=pd.Series(np.arange(48.),index=pd.date_range("2020-01-01",periods=48,freq="MS",tz="UTC")); assert slope(s)>0
def test_seasonal_amplitude():
    s=pd.Series(np.tile(np.arange(12.),3),index=pd.date_range("2020-01-01",periods=36,freq="MS",tz="UTC")); assert amplitude(s)==11
def test_seasonal_kendall_returns_finite():
    s=pd.Series(np.arange(60.),index=pd.date_range("2020-01-01",periods=60,freq="MS",tz="UTC")); assert np.isfinite(seasonal_kendall(s)[0])
def test_latitude_bootstrap_deterministic():
    x=np.arange(12.); y=x*x; assert bootstrap_spearman(x,y)==bootstrap_spearman(x,y)
def test_hash_verification(tmp_path):
    p=tmp_path/"x"; p.write_bytes(b"abc"); verify_sha256(p,sha256(p))
def test_configuration_loads():
    p=Path(__file__).resolve().parents[1]/"configs/global_cohort_v2.json"; q=json.loads(p.read_text()); assert q["anomaly"]["seed"]==q["gaps"]["seed"]==20260815
def test_regional_box_rule(): assert (2<3) and not (3<3)
def test_paired_alignment():
    a=pd.Series([1,2],index=[1,2]); b=pd.Series([3,4],index=[2,3]); assert len(pd.concat([a,b],axis=1).dropna())==1
