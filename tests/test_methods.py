import numpy as np, pandas as pd, pytest
from wdcgg_pipeline.anomaly import anomaly_flags, inject, metrics
from wdcgg_pipeline.reconstruction import fill_gap, METHODS

def hourly(n=2000):
    i=pd.date_range("2020-01-01",periods=n,freq="h",tz="UTC"); return pd.Series(400+np.sin(np.arange(n)*2*np.pi/24),index=i)
@pytest.mark.parametrize("method",["seasonal_mad","rolling_seasonal_mad","hampel_7day","iqr_hour"])
def test_all_anomaly_families(method):
    s=hourly(); f=anomaly_flags(s,method,3); assert f.dtype==bool and len(f)==len(s)
@pytest.mark.parametrize("kind",["positive_spikes","negative_spikes","positive_shift","negative_shift","season_dependent_positive"])
def test_all_injections_reversible(kind):
    s=hourly(); changed,truth=inject(s,kind,np.random.default_rng(20260815)); assert truth.any() and s.iloc[0]==hourly().iloc[0]
@pytest.mark.parametrize("method",METHODS)
def test_all_reconstruction_methods(method):
    s=hourly(); r=fill_gap(s,500,6,method); assert r.iloc[500:506].notna().all()
def test_gap_generation_does_not_mutate_source():
    s=hourly(); original=s.copy(); fill_gap(s,500,12,"linear"); pd.testing.assert_series_equal(s,original)
def test_metrics():
    q=metrics(np.array([1,0,1],bool),np.array([1,0,0],bool)); assert q["tp"]==1 and q["fp"]==1
