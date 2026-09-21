from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import interpolate, stats

PUBLIC_ROOT = Path(__file__).resolve().parents[2]
V2 = Path(os.environ.get("WDCGG_V2_DIR", PUBLIC_ROOT / "results"))
FIG = V2 / "figures"
SEED = 20260815
REPRESENTATIVE = {"BRW4003", "JFJ6036", "HAT2031", "RUN1042", "TLL3005"}


def layout(path: Path):
    meta = {}; cols = None; n = 0; awaiting = False
    with path.open(encoding="utf-8") as h:
        for i, line in enumerate(h, 1):
            if not line.startswith("#"): break
            n = i; s = line.lstrip("#; ").strip()
            if s.upper() == "VARIABLE ORDER": awaiting = True; continue
            if not s: continue
            if awaiting: cols = s.split(); awaiting = False; continue
            if " : " in s:
                k,v=s.split(" : ",1); meta[re.sub(r"[^a-z0-9]+","_",k.lower()).strip("_")]=v.strip()
    if cols is None: raise ValueError(f"no variable order in {path}")
    offset=float(meta.get("dataset_lst2utc",0)); unit=meta.get("value_units","").lower(); species=meta.get("dataset_parameter","").upper()
    factor=1.0
    if unit in {"mol/mol","mol mol-1"}: factor=1e6 if species=="CO2" else 1e9
    elif unit=="ppm" and species=="CH4": factor=1000
    return meta, cols, n, offset, factor


def read_obs(path: Path) -> pd.DataFrame:
    meta, cols, n, offset, factor = layout(path); ci={c:i for i,c in enumerate(cols)}; rows=[]
    with path.open(encoding="utf-8") as h:
        for _ in range(n): next(h)
        for line in h:
            p=line.split()
            if len(p)!=len(cols): continue
            try:
                dt=datetime(int(p[ci["st_year"]]),int(p[ci["st_month"]]),int(p[ci["st_day"]]),int(p[ci["st_hour"]]),int(p[ci["st_minute"]]),int(p[ci["st_second"]]),tzinfo=timezone.utc)+timedelta(hours=offset)
                if not (datetime(2015,1,1,tzinfo=timezone.utc)<=dt<datetime(2025,1,1,tzinfo=timezone.utc)): continue
                v=float(p[ci["value"]]); v=np.nan if v<=-999 else v*factor
                qc=p[ci["QCflag"]] if "QCflag" in ci else ""
                rows.append((dt,v,qc,qc in {"1","2"} and np.isfinite(v)))
            except (ValueError,KeyError): continue
    d=pd.DataFrame(rows,columns=["time","value","qc","valid"]).drop_duplicates("time").sort_values("time").set_index("time")
    return d


def longest_segment(d: pd.DataFrame, limit=6000) -> pd.Series:
    s=d.loc[d.valid,"value"].dropna(); split=s.index.to_series().diff().ne(pd.Timedelta(hours=1)).cumsum()
    g=max((x for _,x in s.groupby(split)),key=len)
    if len(g)>limit:
        start=(len(g)-limit)//2; g=g.iloc[start:start+limit]
    return g


def mad(a):
    a=np.asarray(a,float); med=np.nanmedian(a); return np.nanmedian(np.abs(a-med))*1.4826


def anomaly_flags(s: pd.Series, method: str, k: float) -> np.ndarray:
    x=s.astype(float); hour=x.index.hour
    if method=="seasonal_mad":
        base=x.groupby(hour).transform("median"); resid=x-base
        scale=pd.Series(resid,index=x.index).groupby(hour).transform(lambda z:max(mad(z),1e-9))
        return (np.abs(resid)>k*scale).to_numpy()
    if method=="rolling_seasonal_mad":
        seasonal=x.groupby(hour).transform("median"); r=x-seasonal
        med=r.rolling(24*14+1,center=True,min_periods=72).median(); scale=(r-med).abs().rolling(24*14+1,center=True,min_periods=72).median()*1.4826
        return (np.abs(r-med)>k*scale.clip(lower=1e-9)).fillna(False).to_numpy()
    if method=="hampel_7day":
        med=x.rolling(24*7+1,center=True,min_periods=48).median(); scale=(x-med).abs().rolling(24*7+1,center=True,min_periods=48).median()*1.4826
        return (np.abs(x-med)>k*scale.clip(lower=1e-9)).fillna(False).to_numpy()
    if method=="iqr_hour":
        q1=x.groupby(hour).transform(lambda z:z.quantile(.25)); q3=x.groupby(hour).transform(lambda z:z.quantile(.75)); iqr=(q3-q1).clip(lower=1e-9)
        return ((x<q1-k*.5*iqr)|(x>q3+k*.5*iqr)).to_numpy()
    raise KeyError(method)


def inject(s: pd.Series, kind: str, rng) -> tuple[pd.Series,np.ndarray]:
    x=s.copy(); truth=np.zeros(len(x),bool); scale=max(mad(np.diff(x)),mad(x)*.05,1e-3); n=max(20,len(x)//100)
    if kind in {"positive_spikes","negative_spikes"}:
        pool=np.arange(50,len(x)-50); ix=rng.choice(pool,min(n,len(pool)),replace=False); truth[ix]=True; x.iloc[ix]+=scale*10*(1 if kind.startswith("positive") else -1)
    elif kind in {"positive_shift","negative_shift"}:
        pool=np.arange(100,len(x)-110); starts=rng.choice(pool,min(max(3,n//2),len(pool)),replace=False)
        for q in starts: truth[q:q+6]=True; x.iloc[q:q+6]+=scale*6*(1 if kind.startswith("positive") else -1)
    else:
        candidates=np.where(np.isin(x.index.month,[1,2,7,8]))[0]; ix=rng.choice(candidates,min(n,len(candidates)),replace=False); truth[ix]=True; x.iloc[ix]+=scale*8
    return x,truth


def metrics(flag,truth):
    tp=int((flag&truth).sum()); fp=int((flag&~truth).sum()); fn=int((~flag&truth).sum()); tn=int((~flag&~truth).sum())
    pr=tp/(tp+fp) if tp+fp else 0; rc=tp/(tp+fn) if tp+fn else 0
    return {"tp":tp,"fp":fp,"fn":fn,"tn":tn,"precision":pr,"recall":rc,"f1":2*pr*rc/(pr+rc) if pr+rc else 0,"false_positive_rate":fp/(fp+tn) if fp+tn else 0}


def fill_gap(s: pd.Series, start: int, length: int, method: str) -> pd.Series:
    y=s.copy(); y.iloc[start:start+length]=np.nan; xx=np.arange(len(y)); good=np.isfinite(y)
    if method=="linear": vals=np.interp(xx,xx[good],y[good])
    elif method=="pchip": vals=interpolate.PchipInterpolator(xx[good],y[good],extrapolate=False)(xx)
    elif method=="local_level_kalman":
        obs=y.to_numpy(float); q=max(np.nanvar(np.diff(obs[good]))*.05,1e-8); r=max(np.nanvar(np.diff(obs[good]))*.5,1e-8)
        def filt(a):
            out=np.empty(len(a)); state=np.nanmedian(a); var=100*r
            for i,z in enumerate(a):
                var+=q
                if np.isfinite(z): gain=var/(var+r); state+=gain*(z-state); var*=1-gain
                out[i]=state
            return out
        fw=filt(obs); bw=filt(obs[::-1])[::-1]; vals=(fw+bw)/2
    else:
        frame=pd.DataFrame({"v":y,"h":y.index.hour})
        seasonal=frame.groupby("h").v.transform("median").to_numpy(); resid=y.to_numpy()-seasonal
        if method=="seasonal_median": vals=seasonal
        elif method=="seasonal_residual_linear": vals=seasonal+np.interp(xx,xx[good],resid[good])
        else: raise KeyError(method)
    out=s.copy(); out.iloc[start:start+length]=vals[start:start+length]; return out


def trend_per_year(s):
    return stats.theilslopes(s.to_numpy(),np.arange(len(s))/(24*365.2425)).slope


def seasonal_amp(s):
    c=s.groupby(s.index.hour).mean(); return c.max()-c.min()


def monthly_trend(s):
    s=s.dropna()
    x=(s.index-s.index.min()).total_seconds()/(365.2425*86400)
    return stats.theilslopes(s.to_numpy(),x).slope if len(s)>=3 else math.nan


def monthly_amp(s):
    c=s.dropna().groupby(s.dropna().index.month).mean()
    return c.max()-c.min() if len(c)>=8 else math.nan


def main():
    harm=pd.read_csv(V2/"HARMONISATION_RESULTS_V2.csv")
    harm["dataset_key"] = harm.wdcgg_id.astype(str) + "_" + harm.gas
    sample=harm[harm.wdcgg_id.isin(REPRESENTATIVE)&harm.gas.isin(["CO2","CH4"])].copy()
    assert len(sample)==10, sample[["wdcgg_id","gas"]]
    series={}; full={}
    for _,r in sample.iterrows():
        print("read",r.dataset_key,flush=True); d=read_obs(Path(r.observation_path)); full[r.dataset_key]=d; series[r.dataset_key]=longest_segment(d)

    rng=np.random.default_rng(SEED); methods=["seasonal_mad","rolling_seasonal_mad","hampel_7day","iqr_hour"]
    arows=[]
    for key,s in series.items():
        gas=key.rsplit("_",1)[1]; station=key.rsplit("_",1)[0]
        for kind in ["positive_spikes","negative_spikes","positive_shift","negative_shift","season_dependent_positive"]:
            altered,truth=inject(s,kind,rng)
            for method in methods:
                for k in (3.0,4.0,5.0):
                    flag=anomaly_flags(altered,method,k)
                    arows.append({"dataset_key":key,"station":station,"gas":gas,"latitude":sample.set_index("dataset_key").loc[key,"latitude"],"anomaly_type":kind,"method":method,"parameter_k":k,"seed":SEED,"n_observations":len(s),**metrics(flag,truth)})
    anomaly=pd.DataFrame(arows); anomaly.to_csv(V2/"ANOMALY_METHOD_COMPARISON_V2.csv",index=False)

    # Real provider-QC overlap at each method's globally best synthetic-F1 parameter.
    best=anomaly.groupby(["method","parameter_k"]).f1.mean().groupby(level=0).idxmax().to_dict(); agree=[]; cases=[]
    for key,d in full.items():
        sampled=d.iloc[:30000].copy(); usable=sampled.value.dropna(); provider_invalid=~sampled.valid.to_numpy()
        for method in methods:
            k=best[method][1]; flag=np.zeros(len(sampled),dtype=bool)
            flag[sampled.index.get_indexer(usable.index)]=anomaly_flags(usable,method,k)
            cats={"provider_invalid_statistical_flag":provider_invalid&flag,"provider_invalid_no_statistical_flag":provider_invalid&~flag,
                  "provider_valid_statistical_review_flag":~provider_invalid&flag,"provider_valid_no_flag":~provider_invalid&~flag}
            agree.append({"dataset_key":key,"method":method,"parameter_k":k,"n":len(sampled),**{c:int(v.sum()) for c,v in cats.items()},
                          "interpretation":"Provider-valid flags are review flags, not false positives; provider-invalid missing values cannot receive a numerical statistical flag"})
            for c,v in cats.items():
                ix=np.where(v)[0]
                if len(ix):
                    q=int(ix[0]); lo=max(0,q-12); hi=min(len(sampled),q+13)
                    cases.append({"dataset_key":key,"method":method,"category":c,"event_time":sampled.index[q],"event_value":sampled.value.iloc[q],"window_start":sampled.index[lo],"window_end":sampled.index[hi-1],"ground_truth":"unknown for real observations","possible_explanations":"seasonality; local atmospheric event; instrument/QC context; method sensitivity; coverage"})
    pd.DataFrame(agree).to_csv(V2/"ANOMALY_REAL_FLAG_AGREEMENT_V2.csv",index=False)
    pd.DataFrame(cases).to_csv(V2/"ANOMALY_DISAGREEMENT_CASES_V2.csv",index=False)

    # Limited representative disagreement windows.
    cdf=pd.DataFrame(cases).head(8); fig,axs=plt.subplots(4,2,figsize=(12,9));
    for ax,(_,c) in zip(axs.ravel(),cdf.iterrows()):
        s=full[c.dataset_key].value.loc[pd.Timestamp(c.window_start):pd.Timestamp(c.window_end)]; ax.plot(s.index,s,lw=.8); ax.axvline(pd.Timestamp(c.event_time),c="red",ls="--"); ax.set_title(f"{c.dataset_key}: {c.category}",fontsize=7); ax.tick_params(axis="x",rotation=25,labelsize=5)
    fig.suptitle("Representative provider-QC / statistical-flag disagreement windows (validity unresolved)"); fig.tight_layout(); fig.savefig(FIG/"ANOMALY_DISAGREEMENT_WINDOWS_V2.png",dpi=250); fig.savefig(FIG/"ANOMALY_DISAGREEMENT_WINDOWS_V2.svg"); plt.close(fig)

    # Gap reconstruction: station/gas/season-stratified blocks and downstream distortions.
    grows=[]; gap_methods=["linear","pchip","local_level_kalman","seasonal_median","seasonal_residual_linear"]
    for key,s in series.items():
        candidates={season:np.where(s.index.month.isin(months))[0] for season,months in {"DJF":[12,1,2],"MAM":[3,4,5],"JJA":[6,7,8],"SON":[9,10,11]}.items()}
        for season,ixes in candidates.items():
            feasible=ixes[(ixes>200)&(ixes<len(s)-300)]
            if not len(feasible): continue
            for length in [1,3,6,12,24,48,72]:
                start=int(feasible[len(feasible)//2]); start=min(start,len(s)-length-200)
                truth=s.iloc[start:start+length]
                for method in gap_methods:
                    rec=fill_gap(s,start,length,method); pred=rec.iloc[start:start+length]; err=pred.to_numpy()-truth.to_numpy()
                    grows.append({"dataset_key":key,"station":key.rsplit("_",1)[0],"gas":key.rsplit("_",1)[1],"latitude":sample.set_index("dataset_key").loc[key,"latitude"],"season":season,"gap_hours":length,"method":method,"seed":SEED,"n_masked":len(truth),
                                  "mae":np.mean(np.abs(err)),"rmse":np.sqrt(np.mean(err**2)),"bias":np.mean(err),"max_absolute_error":np.max(np.abs(err)),"correlation":np.corrcoef(truth,pred)[0,1] if length>=3 else math.nan,
                                  "monthly_mean_distortion":pred.mean()-truth.mean(),"annual_mean_distortion":rec.mean()-s.mean(),"trend_distortion_per_year":trend_per_year(rec)-trend_per_year(s),"seasonal_amplitude_distortion":seasonal_amp(rec)-seasonal_amp(s),"unit":"ppm" if key.endswith("CO2") else "ppb"})
    gap=pd.DataFrame(grows); gap.to_csv(V2/"GAP_RECONSTRUCTION_RESULTS_V2.csv",index=False)

    # Real observed-only versus strictly bounded one-hour reconstruction.
    recrows=[]; approved="linear"
    for key,d in full.items():
        s=d.value.where(d.valid).asfreq("h"); observed=s.copy(); fills=0
        missing=np.where(s.isna().to_numpy())[0]
        for i in missing:
            if 0<i<len(s)-1 and np.isfinite(s.iloc[i-1]) and np.isfinite(s.iloc[i+1]): s.iloc[i]=(s.iloc[i-1]+s.iloc[i+1])/2; fills+=1
        om=observed.resample("MS").mean(); rm=s.resample("MS").mean(); both=pd.concat([om,rm],axis=1).dropna(); oa=observed.resample("YS").mean(); ra=s.resample("YS").mean(); ann=pd.concat([oa,ra],axis=1).dropna()
        recrows.append({"scope":"STATION_GAS","dataset_key":key,"station":key.rsplit("_",1)[0],"gas":key.rsplit("_",1)[1],"latitude":sample.set_index("dataset_key").loc[key,"latitude"],"method":approved,"reconstructed_values":fills,
                        "mean_absolute_monthly_change":np.mean(np.abs(both.iloc[:,1]-both.iloc[:,0])),"max_absolute_monthly_change":np.max(np.abs(both.iloc[:,1]-both.iloc[:,0])),"max_absolute_annual_change":np.max(np.abs(ann.iloc[:,1]-ann.iloc[:,0])) if len(ann) else math.nan,
                        "trend_change_per_year":monthly_trend(rm)-monthly_trend(om),"seasonal_amplitude_change":monthly_amp(rm)-monthly_amp(om),"primary_policy":"observed provider-QC-qualified; reconstructed values separately labelled"})
    rec=pd.DataFrame(recrows)
    for (gas,),g in rec.groupby(["gas"]):
        r0=stats.spearmanr(g.latitude,g.trend_change_per_year).statistic if len(g)>=3 else math.nan
        rec.loc[len(rec)]={"scope":"LATITUDE_SENSITIVITY","dataset_key":"ALL","gas":gas,"method":approved,"reconstructed_values":g.reconstructed_values.sum(),"trend_change_per_year":r0,"primary_policy":"value is Spearman rho(latitude, trend change); representative sample only"}
    rec.to_csv(V2/"RECONSTRUCTION_SENSITIVITY_V2.csv",index=False)

    summary={"representative_datasets":len(series),"stations":len(REPRESENTATIVE),"gases":2,"regions":5,"anomaly_methods":methods,"synthetic_rows":len(anomaly),"gap_methods":gap_methods,"gap_rows":len(gap),"fixed_seed":SEED,
             "best_anomaly_parameters":{m:{"k":best[m][1],"mean_f1":float(anomaly[(anomaly.method==m)&(anomaly.parameter_k==best[m][1])].f1.mean())} for m in methods},
             "one_hour_method_median_mae_by_gas":gap[gap.gap_hours==1].groupby(["gas","method"]).mae.median().unstack().to_dict(orient="index"),"reconstruction_policy":"Primary observed-only. Bounded 1-hour linear is an interpretable downstream sensitivity, not a universal best method; values separately labelled."}
    (V2/"METHOD_EXPERIMENT_SUMMARY_V2.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)


if __name__=="__main__": main()
