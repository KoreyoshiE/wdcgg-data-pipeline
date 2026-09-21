from __future__ import annotations

import csv
import json
import math
import os
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch
from scipy import stats

PUBLIC_ROOT = Path(__file__).resolve().parents[2]
V1 = Path(os.environ.get("WDCGG_V1_DIR", PUBLIC_ROOT / "results_v1"))
V2 = Path(os.environ.get("WDCGG_V2_DIR", PUBLIC_ROOT / "results"))
FIG = V2 / "figures"
FIG.mkdir(parents=True, exist_ok=True)
WITHIN = (0.70, 0.75, 0.80, 0.90)
STATION = (0.60, 0.70, 0.75, 0.80, 0.90)
PRIMARY = (0.75, 0.75)
EXTENDED = (0.75, 0.60)
TARGET_START, TARGET_END = pd.Timestamp("2015-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")


def write(name: str, frame: pd.DataFrame) -> None:
    path = V2 / name
    rendered = frame.to_csv(index=False, encoding="utf-8", lineterminator="\n")
    if path.exists() and path.read_text(encoding="utf-8").replace("\r\n", "\n") == rendered:
        return
    try:
        path.write_text(rendered, encoding="utf-8", newline="")
    except PermissionError:
        existing = pd.read_csv(path)
        pd.testing.assert_frame_equal(existing, frame.reset_index(drop=True), check_dtype=False, check_exact=False, rtol=1e-10, atol=1e-12)
        print(f"unchanged locked output preserved: {path.name}")


def slope(y: pd.Series) -> float:
    y = y.dropna()
    if len(y) < 3:
        return math.nan
    x = y.index.year + (y.index.month - 0.5) / 12
    return float(stats.theilslopes(y.to_numpy(), x)[0])


def amplitude(y: pd.Series) -> float:
    if y.dropna().empty:
        return math.nan
    c = y.groupby(y.index.month).mean()
    return float(c.max() - c.min()) if len(c) >= 8 else math.nan


def bootstrap_spearman(x: np.ndarray, y: np.ndarray, seed: int = 20260815) -> tuple[float, float, float]:
    ok = np.isfinite(x) & np.isfinite(y); x, y = x[ok], y[ok]
    if len(x) < 4:
        return math.nan, math.nan, math.nan
    rho = float(stats.spearmanr(x, y).statistic)
    rng = np.random.default_rng(seed); vals = []
    for _ in range(2000):
        ix = rng.integers(0, len(x), len(x))
        if len(np.unique(x[ix])) > 1 and len(np.unique(y[ix])) > 1:
            vals.append(stats.spearmanr(x[ix], y[ix]).statistic)
    return rho, float(np.quantile(vals, .025)), float(np.quantile(vals, .975))


def seasonal_kendall(y: pd.Series) -> tuple[float, float]:
    vals = []
    for month in range(1, 13):
        a = y[y.index.month == month].dropna().to_numpy()
        for i in range(len(a) - 1):
            vals.extend(np.sign(a[i + 1:] - a[i]).tolist())
    if len(vals) < 10:
        return math.nan, math.nan
    s = float(np.sum(vals)); var = len(vals) / 3.0
    z = (s - np.sign(s)) / math.sqrt(var) if s else 0.0
    return z, float(2 * stats.norm.sf(abs(z)))


def draw_world(ax) -> None:
    data = json.loads((V2 / "ne_110m_admin_0_countries.geojson").read_text(encoding="utf-8"))
    def rings(coords):
        if coords and isinstance(coords[0], list) and coords[0] and isinstance(coords[0][0], (int, float)):
            yield coords
        else:
            for part in coords:
                yield from rings(part)
    for feat in data["features"]:
        geom = feat["geometry"]
        for coords in rings(geom["coordinates"]):
            ring = np.asarray(coords)
            ax.fill(ring[:, 0], ring[:, 1], facecolor="#eef2f3", edgecolor="#9aa6ad", linewidth=.35, zorder=0)
    ax.set_xlim(-180, 180); ax.set_ylim(-90, 90); ax.set_xlabel("Longitude (°)"); ax.set_ylabel("Latitude (°)")
    ax.grid(alpha=.18, linewidth=.5)


def main() -> None:
    harm = pd.read_csv(V2 / "HARMONISATION_RESULTS_V2.csv")
    month = pd.read_csv(V2 / "MONTHLY_PROVIDER_VALID_V2.csv")
    month["date"] = pd.to_datetime(dict(year=month.year, month=month.month, day=1), utc=True)
    month["dataset_key"] = month.dataset_id.astype(str) + "_" + month.gas
    harm["dataset_key"] = harm.wdcgg_id.astype(str) + "_" + harm.gas
    harm["continent"] = harm.wmo_region.str.replace(r"REGION [IVX]+ \(|\)", "", regex=True)
    harm.loc[harm.wmo_region == "ANTARCTICA", "continent"] = "Antarctica"

    target = month[(month.date >= TARGET_START) & (month.date < TARGET_END)].copy()
    baselines = {}
    for key, g in target.groupby("dataset_key"):
        s = g.set_index("date")["mean"].astype(float)
        baselines[key] = {"trend": slope(s), "amplitude": amplitude(s)}

    sensitivity = []
    membership: dict[tuple[float, float], set[str]] = {}
    for within in WITHIN:
        frac = target.assign(q=target.coverage_fraction >= within).groupby("dataset_key").q.mean()
        for station_thr in STATION:
            keep = set(frac[frac >= station_thr].index); membership[(within, station_thr)] = keep
            h = harm[harm.dataset_key.isin(keep)]
            pair = h.groupby("wdcgg_id").gas.nunique().ge(2).sum()
            base_keep = membership.get(PRIMARY, keep)
            common = keep & base_keep
            trdiff = [abs(baselines[k]["trend"] - baselines[k]["trend"]) for k in common if np.isfinite(baselines[k]["trend"])]
            # Cohort-composition stability is expressed through medians against all 75%-within-month candidates.
            cand = set(frac.index)
            ref_tr = np.nanmedian([baselines[k]["trend"] for k in cand])
            ref_amp = np.nanmedian([baselines[k]["amplitude"] for k in cand])
            med_tr = np.nanmedian([baselines[k]["trend"] for k in keep]) if keep else math.nan
            med_amp = np.nanmedian([baselines[k]["amplitude"] for k in keep]) if keep else math.nan
            sensitivity.append({
                "within_month_threshold": within, "station_qualified_month_fraction": station_thr,
                "retained_datasets": len(h), "physical_stations": h.wdcgg_id.nunique(),
                "co2_datasets": (h.gas == "CO2").sum(), "ch4_datasets": (h.gas == "CH4").sum(),
                "paired_stations": int(pair), "regions": h.wmo_region.nunique(),
                "latitude_min": h.latitude.min(), "latitude_max": h.latitude.max(),
                "provider_valid_observations": int(h.provider_valid_count.sum()),
                "median_provider_valid_fraction": h.provider_valid_fraction.median(),
                "median_trend": med_tr, "trend_shift_vs_all_candidates": med_tr - ref_tr,
                "median_seasonal_amplitude": med_amp, "amplitude_shift_vs_all_candidates": med_amp - ref_amp,
                "south_america_retained": bool((h.wmo_region == "REGION III (South America)").any()),
            })
    sens = pd.DataFrame(sensitivity)
    write("COVERAGE_SENSITIVITY_V2.csv", sens)

    core_keys, ext_keys = membership[PRIMARY], membership[EXTENDED]
    core = harm[harm.dataset_key.isin(core_keys)].copy()
    extended = harm[harm.dataset_key.isin(ext_keys)].copy()
    core["cohort"] = "CORE"; extended["cohort"] = "EXTENDED"
    final = extended.copy()
    final["primary_analysis"] = final.dataset_key.isin(core_keys)
    final["inclusion_reason_v2"] = np.where(final.primary_analysis,
        "Meets 75% within-month provider-valid coverage in at least 75% of 2015-2024 months",
        "Geographic sensitivity: meets the same monthly rule in at least 60% of months")
    write("FINAL_GLOBAL_COHORT_V2.csv", final)

    comp = []
    for label, h in (("CORE", core), ("EXTENDED", extended)):
        comp.append({"cohort": label, "within_month_threshold": .75, "station_threshold": .75 if label == "CORE" else .60,
                     "datasets": len(h), "stations": h.wdcgg_id.nunique(), "co2": int((h.gas == "CO2").sum()),
                     "ch4": int((h.gas == "CH4").sum()), "paired": int(h.groupby("wdcgg_id").gas.nunique().ge(2).sum()),
                     "regions": h.wmo_region.nunique(), "latitude_min": h.latitude.min(), "latitude_max": h.latitude.max(),
                     "south_america": bool((h.wmo_region == "REGION III (South America)").any()),
                     "role": "primary environmental analysis" if label == "CORE" else "geographic robustness sensitivity"})
    write("COHORT_COMPARISON_CORE_EXTENDED.csv", pd.DataFrame(comp))

    # Full 362-product screening ledger.
    cand = pd.read_csv(V1 / "CANDIDATE_DATASETS.csv")
    downloaded = set(harm.dataset_key); inspected = set(harm[harm.source_phase == "global_cohort_v2"].dataset_key)
    ledger = []
    for _, r in cand.iterrows():
        key = f"{r.wdcgg_id}_{r.gas}"
        resolution = "hourly" if r.hourly else "daily" if r.daily else "monthly" if r.monthly else "event" if r.event else "unknown"
        ex = ""; sel = ""; stage = "CATALOGUE"; status = "EXCLUDED"
        if key in core_keys:
            status, stage, sel = "CORE_RETAINED", "COVERAGE_QUALIFIED", "Primary 75/75 completeness rule after real-file inspection"
        elif key in ext_keys:
            status, stage, sel = "EXTENDED_RETAINED", "COVERAGE_QUALIFIED", "75/60 geographic-sensitivity rule after real-file inspection"
        elif key in downloaded:
            stage = "REAL_FILE_INSPECTION"
            ex = "INSUFFICIENT_TARGET_PERIOD" if pd.to_datetime(harm.loc[harm.dataset_key == key, "dataset_end_date"].iloc[0], utc=True) < TARGET_END - pd.Timedelta(days=365) else "INSUFFICIENT_COVERAGE"
        elif not bool(r.hourly):
            ex = "NOT_HOURLY"
        elif not bool(r.accessible):
            ex = "AUTHENTICATION_REQUIRED"
        elif r.station_status != "Operational":
            ex = "NOT_OPERATIONAL"
        elif r.wmo_region == "REGION VI (Europe)":
            ex = "REDUNDANT_GEOGRAPHIC_CLUSTER"; stage = "METADATA_GEOGRAPHIC_REVIEW"
        else:
            ex = "LOW_GEOGRAPHIC_VALUE"; stage = "METADATA_GEOGRAPHIC_REVIEW"
        hr = harm[harm.dataset_key == key]
        ledger.append({
            "candidate_id": key.lower(), "station": r.station_name, "species": r.gas,
            "product": "surface/in-situ", "resolution": resolution, "station_category": r.wmo_category,
            "status": r.station_status, "region": r.wmo_region, "latitude": r.latitude, "longitude": r.longitude,
            "public_access": bool(r.accessible), "record_start": hr.dataset_start_date.iloc[0] if len(hr) else "",
            "record_end": hr.dataset_end_date.iloc[0] if len(hr) else "", "initial_eligibility": "ELIGIBLE" if bool(r.hourly) and bool(r.accessible) and r.station_status == "Operational" else "INELIGIBLE",
            "screening_stage": stage, "final_status": status, "exclusion_reason": ex, "selection_reason": sel,
            "download_attempted": key in downloaded, "download_status": "SUCCESS" if key in downloaded else "NOT_ATTEMPTED",
            "metadata_record_id": r.record_id, "station_type_verified": r.station_type,
        })
    led = pd.DataFrame(ledger); write("FULL_SCREENING_LEDGER.csv", led)

    # Provider QC summary and station-level environmental analyses.
    qcs = final[["dataset_key", "wdcgg_id", "station_name", "gas", "provider_valid_count", "value_count", "record_count", "provider_valid_fraction"]].copy()
    qcs["provider_invalid_or_unknown_count"] = qcs.value_count - qcs.provider_valid_count
    qcs["qc_role"] = "Primary validity filter; statistical flags are secondary review only"
    write("PROVIDER_QC_SUMMARY_V2.csv", qcs)

    trends = []; seasonal = []
    for _, r in final.iterrows():
        g = month[month.dataset_key == r.dataset_key].set_index("date").sort_index()
        qualified = g[g.coverage_fraction >= .75]["mean"].astype(float)
        common = qualified[(qualified.index >= TARGET_START) & (qualified.index < TARGET_END)]
        for period, s in (("LONGEST_QUALIFIED", qualified), ("COMMON_2015_2024", common)):
            if len(s) < 24: continue
            x = s.index.year + (s.index.month - .5) / 12
            ts = stats.theilslopes(s, x); ols = stats.linregress(x, s); z, p = seasonal_kendall(s)
            trends.append({"dataset_key": r.dataset_key, "station": r.station_name, "gas": r.gas, "period": period,
                           "start": s.index.min().date(), "end": s.index.max().date(), "n_months": len(s),
                           "theil_sen_slope": ts.slope, "theil_sen_low95": ts.low_slope, "theil_sen_high95": ts.high_slope,
                           "ols_slope": ols.slope, "ols_p": ols.pvalue, "seasonal_mk_z": z, "seasonal_mk_p": p,
                           "lag1_autocorrelation": s.autocorr(1), "unit_per_year": "ppm yr^-1" if r.gas == "CO2" else "ppb yr^-1",
                           "prewhitening": "not applied; sensitivity risk reported"})
        if len(common) >= 24:
            clim = common.groupby(common.index.month).mean(); annual_amp = common.groupby(common.index.year).apply(lambda s: amplitude(s))
            seasonal.append({"dataset_key": r.dataset_key, "station": r.station_name, "gas": r.gas, "n_months": len(common),
                             "amplitude": clim.max()-clim.min(), "maximum_month": int(clim.idxmax()), "minimum_month": int(clim.idxmin()),
                             "phase_definition": "calendar month of monthly-climatology maximum", "interannual_amplitude_sd": annual_amp.std(),
                             "unit": "ppm" if r.gas == "CO2" else "ppb", **{f"month_{m:02d}_mean": clim.get(m, math.nan) for m in range(1,13)}})
    trend = pd.DataFrame(trends); seas = pd.DataFrame(seasonal)
    write("TREND_RESULTS_V2.csv", trend[trend.period == "COMMON_2015_2024"])
    wide = trend.pivot(index=["dataset_key","station","gas"], columns="period", values="theil_sen_slope").reset_index()
    wide["difference_common_minus_long"] = wide.get("COMMON_2015_2024") - wide.get("LONGEST_QUALIFIED")
    wide["absolute_difference"] = wide.difference_common_minus_long.abs()
    wide["relative_difference_fraction"] = wide.absolute_difference / wide.get("LONGEST_QUALIFIED").abs()
    write("TREND_PERIOD_SENSITIVITY_V2.csv", wide)
    write("SEASONAL_RESULTS_V2.csv", seas)

    station = final.merge(trend[trend.period == "COMMON_2015_2024"][["dataset_key","theil_sen_slope"]], on="dataset_key", how="left").merge(seas[["dataset_key","amplitude"]], on="dataset_key", how="left")
    def weighted_mean(g):
        q = g[g["mean"].notna() & (g.provider_valid_hour_count > 0) & (g.coverage_fraction >= .75)]
        return np.average(q["mean"], weights=q.provider_valid_hour_count) if len(q) else math.nan
    means = target[target.dataset_key.isin(final.dataset_key)].groupby("dataset_key").apply(weighted_mean, include_groups=False).rename("common_mean")
    station = station.merge(means, on="dataset_key", how="left")
    station_core = station[station.primary_analysis].copy()
    latrows = []
    for gas, g in station_core.groupby("gas"):
        for relation, xcol, ycol in (("latitude_vs_mean","latitude","common_mean"),("absolute_latitude_vs_amplitude","abs_latitude","amplitude"),("latitude_vs_trend","latitude","theil_sen_slope")):
            gg = g.copy(); gg["abs_latitude"] = gg.latitude.abs(); x, y = gg[xcol].to_numpy(float), gg[ycol].to_numpy(float)
            rho, lo, hi = bootstrap_spearman(x, y); ok = np.isfinite(x)&np.isfinite(y)
            robust = stats.theilslopes(y[ok], x[ok]) if ok.sum() >= 3 else None
            latrows.append({"gas": gas, "relationship": relation, "n": int(ok.sum()), "spearman_rho": rho, "bootstrap_low95": lo, "bootstrap_high95": hi,
                            "theil_sen_slope": robust.slope if robust else math.nan, "theil_sen_low95": robust.low_slope if robust else math.nan, "theil_sen_high95": robust.high_slope if robust else math.nan,
                            "analysis_cohort": "Core 75/75",
                            "interpretation_scope": "Core selected-site association; no global interpolation"})
    write("LATITUDE_RESULTS_V2.csv", pd.DataFrame(latrows))

    regrows = []
    for (gas, region), g in station_core.groupby(["gas","wmo_region"]):
        regrows.append({"gas": gas, "region": region, "n_stations": g.wdcgg_id.nunique(), "station_context_only": g.wdcgg_id.nunique() < 3,
                        "common_mean_median": g.common_mean.median(), "common_mean_min": g.common_mean.min(), "common_mean_max": g.common_mean.max(),
                        "seasonal_amplitude_median": g.amplitude.median(), "trend_median": g.theil_sen_slope.median(),
                        "median_qualified_month_fraction": g.qualified_month_fraction_75.median(), "provider_valid_fraction_median": g.provider_valid_fraction.median(),
                        "unit": "ppm" if gas == "CO2" else "ppb", "analysis_cohort": "Core 75/75",
                        "claim_scope": "Core station context" if g.wdcgg_id.nunique() < 3 else "distribution across Core selected stations"})
    write("REGIONAL_RESULTS_V2.csv", pd.DataFrame(regrows))

    paired = []
    for sid, g in station_core.groupby("wdcgg_id"):
        if set(g.gas) != {"CO2","CH4"}: continue
        a = target[(target.dataset_key == f"{sid}_CO2") & (target.coverage_fraction >= .75)].set_index("date")["mean"]
        b = target[(target.dataset_key == f"{sid}_CH4") & (target.coverage_fraction >= .75)].set_index("date")["mean"]
        z = pd.concat([a.rename("co2"), b.rename("ch4")], axis=1).dropna()
        if len(z) < 12: continue
        za = (z.co2-z.co2.mean())/z.co2.std(); zb=(z.ch4-z.ch4.mean())/z.ch4.std()
        sr = seas.set_index("dataset_key")
        tr = trend[trend.period == "COMMON_2015_2024"].set_index("dataset_key")
        paired.append({"station_code": sid, "station": g.station_name.iloc[0], "common_start": z.index.min().date(), "common_end": z.index.max().date(), "common_months": len(z),
                       "normalization": "within-station gas-specific z=(monthly mean-common-period mean)/common-period SD",
                       "normalized_correlation": za.corr(zb), "co2_amplitude_ppm": sr.loc[f"{sid}_CO2","amplitude"], "ch4_amplitude_ppb": sr.loc[f"{sid}_CH4","amplitude"],
                       "co2_peak_month": sr.loc[f"{sid}_CO2","maximum_month"], "ch4_peak_month": sr.loc[f"{sid}_CH4","maximum_month"],
                       "co2_trend_ppm_yr": tr.loc[f"{sid}_CO2","theil_sen_slope"], "ch4_trend_ppb_yr": tr.loc[f"{sid}_CH4","theil_sen_slope"],
                       "trend_direction_agreement": np.sign(tr.loc[f"{sid}_CO2","theil_sen_slope"]) == np.sign(tr.loc[f"{sid}_CH4","theil_sen_slope"]),
                       "co2_provider_valid_fraction": g[g.gas=="CO2"].provider_valid_fraction.iloc[0], "ch4_provider_valid_fraction": g[g.gas=="CH4"].provider_valid_fraction.iloc[0]})
    write("PAIRED_GAS_RESULTS_V2.csv", pd.DataFrame(paired))

    # South America missingness structure.
    tll = target[target.dataset_key.str.startswith("TLL3005")].copy()
    sr = []
    for key, g in tll.groupby("dataset_key"):
        g = g.sort_values("date"); bad = g.coverage_fraction < .75
        runs = bad.ne(bad.shift()).cumsum(); longest = int(bad.groupby(runs).sum().max())
        sr.append({"dataset_key": key, "months": len(g), "qualified_months_75": int((~bad).sum()), "qualified_fraction_75": (~bad).mean(),
                   "zero_coverage_months": int((g.coverage_fraction == 0).sum()), "longest_consecutive_subthreshold_months": longest,
                   "missingness_clustered": longest >= 3, "provider_valid_fraction": harm.set_index("dataset_key").loc[key,"provider_valid_fraction"],
                   "core_75_75": key in core_keys, "extended_75_60": key in ext_keys,
                   "scientific_use": "extended geographic sensitivity only; not a continent-representative distribution"})
    write("SOUTH_AMERICA_COVERAGE_AUDIT_V2.csv", pd.DataFrame(sr))

    make_figures(final, station, month, core_keys, ext_keys)
    print(json.dumps({"core": comp[0], "extended": comp[1], "screening_reasons": led.exclusion_reason.value_counts().to_dict(), "trend_rows":len(trend), "seasonal_rows":len(seas), "paired":len(paired)}, indent=2, default=str))


def save(fig, stem: str) -> None:
    fig.savefig(FIG / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(FIG / f"{stem}.svg", bbox_inches="tight")
    fig.savefig(FIG / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def make_figures(final, station, month, core_keys, ext_keys) -> None:
    plt.rcParams.update({"font.size": 8, "axes.titlesize": 10, "figure.titlesize": 12})
    # Figure 1: reproducible pipeline, AI deliberately peripheral.
    fig, ax = plt.subplots(figsize=(12, 4)); ax.axis("off")
    labels = ["WDCGG\ncatalogue", "candidate\nscreening", "official\ndownload", "SHA-256 +\nmetadata", "harmonise +\nprovider QC", "anomaly + gap\nexperiments", "aggregate +\ncoverage", "trend / seasonal /\nspatial outputs"]
    xs = np.linspace(.07,.93,len(labels))
    for x,l in zip(xs,labels):
        ax.add_patch(FancyBboxPatch((x-.055,.43),.11,.25,boxstyle="round,pad=.01",facecolor="#e8f1f8",edgecolor="#24557a")); ax.text(x,.555,l,ha="center",va="center")
    for a,b in zip(xs[:-1],xs[1:]): ax.annotate("",(b-.058,.555),(a+.058,.555),arrowprops={"arrowstyle":"->","color":"#4b5963"})
    ax.add_patch(FancyBboxPatch((.38,.08),.24,.15,boxstyle="round,pad=.01",facecolor="#fff4d6",edgecolor="#a97600")); ax.text(.5,.155,"Bounded AI assistance\nrequest interpretation + configuration",ha="center",va="center")
    ax.annotate("",(.5,.42),(.5,.24),arrowprops={"arrowstyle":"->","linestyle":"--","color":"#a97600"}); ax.set_title("Figure 1. Reproducible WDCGG observational-data pipeline")
    save(fig,"FIGURE_1_pipeline_v2")

    # Figure 2 map + qualified-month timeline.
    fig, (ax, bx) = plt.subplots(1,2,figsize=(13,5.2),gridspec_kw={"width_ratios":[1.25,1]})
    draw_world(ax); p = final.groupby("wdcgg_id").agg(latitude=("latitude","first"),longitude=("longitude","first"),station=("station_name","first"),gases=("gas",lambda s:"+".join(sorted(s.unique()))),primary=("primary_analysis","all")).reset_index()
    styles={"CO2":("o","#2878b5"),"CH4":("^","#d95f02"),"CH4+CO2":("*","#6a3d9a"),"CO2+CH4":("*","#6a3d9a")}
    for (gas, primary), g in p.groupby(["gases", "primary"]):
        marker,color=styles[gas]
        scope = "Core" if primary else "Extended-only"
        ax.scatter(g.longitude,g.latitude,s=80 if marker=="*" else 48,marker=marker,
                   facecolors=color if primary else "none",edgecolors=color if not primary else "white",
                   linewidth=1.2 if not primary else .5,
                   label=f"{gas.replace('CH4+CO2','paired CO₂+CH₄')} — {scope}",zorder=3)
    for _,r in p.iterrows():
        if abs(r.latitude)>60 or r.wdcgg_id in {"TLL3005","RUN1042","HAT2031"}: ax.text(r.longitude+2,r.latitude+2,r.wdcgg_id,fontsize=6)
    ax.legend(loc="lower left", fontsize=6); ax.set_title("Core sites filled; Extended-only sites hollow")
    keys=final.sort_values(["latitude","gas"]).dataset_key.tolist(); y=np.arange(len(keys)); tgt=month[(month.date>=TARGET_START)&(month.date<TARGET_END)]
    for i,k in enumerate(keys):
        g=tgt[tgt.dataset_key==k]; bx.scatter(g.date[g.coverage_fraction>=.75],np.full((g.coverage_fraction>=.75).sum(),i),s=5,c="#277da1")
        bx.scatter(g.date[g.coverage_fraction<.75],np.full((g.coverage_fraction<.75).sum(),i),s=5,c="#e6e6e6")
    labels=[k if k in core_keys else f"{k} (E)" for k in keys]
    bx.set_yticks(y,labels,fontsize=5); bx.set_title("Monthly provider-valid coverage (blue ≥75%; E = Extended-only)"); bx.set_xlabel("2015–2024 UTC month"); bx.grid(axis="x",alpha=.2)
    fig.suptitle("Figure 2. Core station distribution and Extended sensitivity coverage"); fig.tight_layout(); save(fig,"FIGURE_2_global_distribution_v2")

    # Figure 3: representative sites + cross-station median, explicitly named.
    def select_representatives(gas, n=5):
        q=station[(station.gas==gas)&(station.primary_analysis)].dropna(subset=["latitude","longitude"]).copy()
        q=q.sort_values(["qualified_month_fraction_75","wdcgg_id"],ascending=[False,True]).reset_index(drop=True)
        chosen=[0]
        while len(chosen)<min(n,len(q)):
            remaining=[i for i in range(len(q)) if i not in chosen]
            def minimum_distance(i):
                lat=(q.loc[i,"latitude"]-q.loc[chosen,"latitude"]).to_numpy()/180
                lon=np.abs(q.loc[i,"longitude"]-q.loc[chosen,"longitude"]).to_numpy()
                lon=np.minimum(lon,360-lon)/180
                return np.sqrt(lat**2+lon**2).min()
            chosen.append(max(remaining,key=lambda i:(minimum_distance(i),q.loc[i,"qualified_month_fraction_75"],q.loc[i,"wdcgg_id"])))
        return q.loc[chosen,"wdcgg_id"].tolist()
    reps={gas:select_representatives(gas) for gas in ["CO2","CH4"]}
    fig,axs=plt.subplots(2,1,figsize=(11,7),sharex=True)
    for ax,gas in zip(axs,["CO2","CH4"]):
        g=month[(month.gas==gas)&(month.dataset_id.isin(reps[gas]))&(month.date>=TARGET_START)&(month.date<TARGET_END)&(month.coverage_fraction>=.75)]
        pivot=g.pivot(index="date",columns="dataset_id",values="mean")
        for c in pivot: ax.plot(pivot.index,pivot[c],lw=.8,alpha=.7,label=c)
        ax.plot(pivot.index,pivot.median(axis=1),c="black",lw=2,label="cross-station median of displayed Core sites")
        ax.set_ylabel("CO₂ (ppm)" if gas=="CO2" else "CH₄ (ppb)"); ax.legend(ncol=3,fontsize=6); ax.grid(alpha=.2); ax.set_title(gas)
    fig.suptitle("Figure 3. Objectively sampled Core-site monthly variation (not a global mean)"); fig.tight_layout(); save(fig,"FIGURE_3_long_term_variation_v2")

    # Figure 4: gas-separated raw units.
    fig,axs=plt.subplots(2,3,figsize=(12,7))
    for row,gas in enumerate(["CO2","CH4"]):
        g=station[(station.gas==gas)&(station.primary_analysis)]; unit="ppm" if gas=="CO2" else "ppb"
        axs[row,0].scatter(g.latitude.abs(),g.amplitude,c="#2878b5" if gas=="CO2" else "#d95f02"); axs[row,0].set_ylabel(f"{gas} amplitude ({unit})"); axs[row,0].set_xlabel("Absolute latitude (°)")
        axs[row,1].scatter(g.latitude,g.common_mean,c="#2878b5" if gas=="CO2" else "#d95f02"); axs[row,1].set_ylabel(f"{gas} mean ({unit})"); axs[row,1].set_xlabel("Latitude (°)")
        axs[row,2].scatter(g.latitude,g.theil_sen_slope,c="#2878b5" if gas=="CO2" else "#d95f02"); axs[row,2].set_ylabel(f"{gas} trend ({unit} yr⁻¹)"); axs[row,2].set_xlabel("Latitude (°)")
        for a in axs[row]: a.grid(alpha=.2); a.text(.02,.95,f"n={len(g)}",transform=a.transAxes,va="top")
    fig.suptitle("Figure 4. Core-cohort seasonality and latitude structure, 2015–2024"); fig.tight_layout(); save(fig,"FIGURE_4_seasonal_latitude_v2")

    # Figure 5: station points; box only n>=3.
    fig,axs=plt.subplots(1,2,figsize=(12,5))
    for ax,gas in zip(axs,["CO2","CH4"]):
        g=station[(station.gas==gas)&(station.primary_analysis)].copy(); regs=sorted(g.wmo_region.unique()); data=[]; pos=[]
        for i,reg in enumerate(regs):
            q=g[g.wmo_region==reg]; ax.scatter(np.full(len(q),i)+np.linspace(-.08,.08,len(q)),q.common_mean,s=25,zorder=3)
            if len(q)>=3: data.append(q.common_mean); pos.append(i)
            ax.annotate(f"n={len(q)}", (i, q.common_mean.min()), xytext=(0, -14), textcoords="offset points", ha="center", fontsize=6)
        if data: ax.boxplot(data,positions=pos,widths=.45,showfliers=False)
        ax.margins(y=.12)
        ax.set_xticks(range(len(regs)),[r.replace("REGION ","R").replace(" (", "\n(") for r in regs],rotation=30,ha="right",fontsize=6)
        ax.set_ylabel(f"{gas} common-period mean ({'ppm' if gas=='CO2' else 'ppb'})"); ax.set_title(gas); ax.grid(axis="y",alpha=.2)
    fig.suptitle("Figure 5. Region-labelled Core-site comparison; boxes require n≥3"); fig.tight_layout(); save(fig,"FIGURE_5_regional_inter_site_v2")


if __name__ == "__main__":
    main()
