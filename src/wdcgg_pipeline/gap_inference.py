"""Frozen paired-gap, equal-stratum and station-block inference."""
from __future__ import annotations
from itertools import combinations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

_CONFIG = {}
METHODS = []


def scoped_seed(*parts):
    token = '|'.join(map(str, (_CONFIG['master_bootstrap_seed'], *parts))).encode()
    return int.from_bytes(hashlib.sha256(token).digest()[:8], 'big')

STRATUM=['dataset_key','physical_station_id','gas','season','gap_hours']
STATION=['physical_station_id','gas','gap_hours']
CONDITION=['gas','gap_hours']
PAIRS = []

def complete_comparison(long):
    successful=long[long.execution_status.eq('SUCCESS')]
    complete=successful.groupby('base_gap_id').method.nunique().eq(5)
    assert long.all_method_complete_base_gap.equals(long.base_gap_id.map(complete).fillna(False))
    return long[long.placement_role.eq('REPEATED_DESIGN') & long.all_method_complete_base_gap].copy()

def stratum_summary(frame):
    return frame.groupby(STRATUM+['method'],sort=True).agg(
        successful_placements=('base_gap_id','nunique'),median_rmse=('rmse','median'),
        mean_rmse=('rmse','mean'),q25_rmse=('rmse',lambda x:x.quantile(.25)),
        q75_rmse=('rmse',lambda x:x.quantile(.75)),median_mae=('mae','median'),
        mean_signed_bias=('bias','mean'),median_max_absolute_error=('max_absolute_error','median'),
        median_correlation=('correlation','median')).reset_index().assign(comparison_set='COMPLETE_FIVE_METHOD')

def station_summary(strata):
    result=strata.groupby(STATION+['method'],sort=True).agg(
        seasonal_strata=('dataset_key','size'),repeated_base_gaps=('successful_placements','sum'),
        median_rmse=('median_rmse','median'),median_mae=('median_mae','median')).reset_index()
    result['aggregation']='MEDIAN_OF_SEASONAL_STRATUM_MEDIANS'
    return result

def paired_base(frame):
    rows=[]
    metadata=['base_gap_id',*STRATUM,'placement_id','placement_role']
    for a,b in PAIRS:
        left=frame[frame.method.eq(a)][metadata+['rmse','mae','all_method_complete_base_gap']]
        right=frame[frame.method.eq(b)][['base_gap_id','rmse','mae']]
        g=left.merge(right,on='base_gap_id',validate='one_to_one',suffixes=('_a','_b'))
        g['method_a']=a;g['method_b']=b;g['pair']=a+' - '+b
        g['delta_rmse']=g.rmse_a-g.rmse_b;g['delta_mae']=g.mae_a-g.mae_b
        rows.append(g)
    return pd.concat(rows,ignore_index=True)

def paired_strata(base):
    repeated=base[base.placement_role.eq('REPEATED_DESIGN') & base.all_method_complete_base_gap]
    return repeated.groupby(STRATUM+['pair','method_a','method_b'],sort=True).agg(
        paired_n=('base_gap_id','nunique'),median_delta_rmse=('delta_rmse','median'),
        mean_delta_rmse=('delta_rmse','mean'),median_delta_mae=('delta_mae','median'),
        proportion_negative=('delta_rmse',lambda x:float((x<0).mean())),
        proportion_positive=('delta_rmse',lambda x:float((x>0).mean())),
        ties=('delta_rmse',lambda x:int((x==0).sum()))).reset_index()

def paired_stations(strata):
    return strata.groupby(STATION+['pair','method_a','method_b'],sort=True).agg(
        seasonal_strata=('dataset_key','size'),paired_n=('paired_n','sum'),
        median_delta_rmse=('median_delta_rmse','median'),
        median_delta_mae=('median_delta_mae','median')).reset_index()

def bootstrap(values, *scope, n=5000):
    values=np.asarray(values,dtype=float);assert len(values) and np.isfinite(values).all()
    rng=np.random.default_rng(scoped_seed('bootstrap',*scope))
    draws=rng.integers(0,len(values),size=(n,len(values)))
    return np.median(values[draws],axis=1),draws

def block_inference(stations,strata,pair_stations,pair_strata):
    summaries=[];registry=[]
    definitions=[('METHOD',stations,strata,'method','median_rmse'),
                 ('PAIR',pair_stations,pair_strata,'pair','median_delta_rmse')]
    for analysis,station_frame,stratum_frame,label,value in definitions:
        for block,frame in [('PHYSICAL_STATION_PRIMARY',station_frame),('STRATUM_BLOCK_SENSITIVITY',stratum_frame)]:
            for (gas,length,identity),g in frame.groupby(CONDITION+[label],sort=True):
                # Every input row is already one whole station or one stratum.
                values=g[value].to_numpy();n_station=g.physical_station_id.nunique()
                draws,indices=bootstrap(values,gas,length,analysis,identity,block)
                low_cluster=block=='PHYSICAL_STATION_PRIMARY' and n_station<3
                low,high=np.quantile(draws,[.025,.975])
                record={'gas':gas,'gap_hours':length,'analysis_type':analysis,'identity':identity,
                    'block_unit':block,'unique_physical_stations':int(n_station),'resampled_blocks':len(g),
                    'point_estimate':float(np.median(values)),'ci_lower':np.nan if low_cluster else low,
                    'ci_upper':np.nan if low_cluster else high,'replicates':len(draws),
                    'station_block_status':'INSUFFICIENT_STATION_CLUSTERS' if low_cluster else
                        'STRATUM_BLOCK_SENSITIVITY' if block=='STRATUM_BLOCK_SENSITIVITY' else 'AVAILABLE_REPRESENTATIVE_DESIGN',
                    'seed':scoped_seed('bootstrap',gas,length,analysis,identity,block),
                    'interval_scope':'DESCRIPTIVE_NOT_SIMULTANEOUS_FAMILYWISE'}
                summaries.append(record)
                registry.append(pd.DataFrame({'gas':gas,'gap_hours':length,'analysis_type':analysis,
                    'identity':identity,'block_unit':block,'replicate_id':np.arange(1,len(draws)+1),
                    'bootstrap_value':draws,'sampled_block_indices':[';'.join(map(str,row)) for row in indices]}))
    return pd.DataFrame(summaries),pd.concat(registry,ignore_index=True)

def rank_stability(comparison,strata,stations,gas_summary):
    out=[]
    for (gas,length),g in gas_summary.groupby(CONDITION,sort=True):
        ranks=g.set_index('method').median_rmse.rank(method='min');mae_ranks=g.set_index('method').median_mae.rank(method='min')
        s=stations[stations.gas.eq(gas)&stations.gap_hours.eq(length)].copy()
        z=strata[strata.gas.eq(gas)&strata.gap_hours.eq(length)].copy()
        b=comparison[comparison.gas.eq(gas)&comparison.gap_hours.eq(length)].copy()
        s['lowest']=s.median_rmse.eq(s.groupby('physical_station_id').median_rmse.transform('min'))
        z['lowest']=z.median_rmse.eq(z.groupby(['dataset_key','season']).median_rmse.transform('min'))
        b['lowest']=b.rmse.eq(b.groupby('base_gap_id').rmse.transform('min'))
        for method in METHODS:
            out.append({'gas':gas,'gap_hours':length,'method':method,'rmse_rank':ranks[method],
                'mae_rank':mae_ranks[method],'station_lowest_count':int(s[s.method.eq(method)].lowest.sum()),
                'contributing_stations':s.physical_station_id.nunique(),
                'stratum_lowest_count':int(z[z.method.eq(method)].lowest.sum()),
                'contributing_strata':len(z[z.method.eq(method)]),
                'base_gap_lowest_proportion':float(b[b.method.eq(method)].lowest.mean()),
                'ties_rule':'EVERY_EXACTLY_TIED_METHOD_COUNTS_AS_LOWEST'})
    return pd.DataFrame(out)

def seasonal_summary(strata):
    rows=[]
    for (gas,season,length,method),g in strata.groupby(['gas','season','gap_hours','method'],sort=True):
        n=g.physical_station_id.nunique();low=high=np.nan
        if n>=3:
            values,_=bootstrap(g.median_rmse.to_numpy(),gas,length,'SEASON_METHOD',method,season,'PHYSICAL_STATION_PRIMARY')
            low,high=np.quantile(values,[.025,.975])
        rows.append({'gas':gas,'season':season,'gap_hours':length,'method':method,'physical_stations':n,
            'strata':len(g),'repeated_base_gaps':int(g.successful_placements.sum()),
            'median_rmse':float(g.median_rmse.median()),'median_mae':float(g.median_mae.median()),
            'ci_lower':low,'ci_upper':high,'status':'DESCRIPTIVE_ONLY_LOW_CLUSTER_COUNT' if n<3 else 'AVAILABLE_REPRESENTATIVE_DESIGN'})
    return pd.DataFrame(rows)

def historical_comparison(long,comparison,summary,intervals):
    rows=[]
    historical=long[long.placement_role.eq('HISTORICAL_ANCHOR')]
    for row in summary.itertuples():
        condition=(comparison.gas.eq(row.gas)&comparison.gap_hours.eq(row.gap_hours)&comparison.method.eq(row.method))
        old=historical[historical.gas.eq(row.gas)&historical.gap_hours.eq(row.gap_hours)&historical.method.eq(row.method)]
        ci=intervals[intervals.gas.eq(row.gas)&intervals.gap_hours.eq(row.gap_hours)&
            intervals.identity.eq(row.method)&intervals.block_unit.eq('PHYSICAL_STATION_PRIMARY')].iloc[0]
        anchor_median=float(old.rmse.median());values=comparison.loc[condition,'rmse']
        rows.append({'gas':row.gas,'gap_hours':row.gap_hours,'method':row.method,
            'historical_median_rmse':anchor_median,'repeated_station_equal_median_rmse':row.median_rmse,
            'repeated_station_ci_lower':ci.ci_lower,'repeated_station_ci_upper':ci.ci_upper,
            'historical_percentile_in_pooled_placement_rmse':float((values<=anchor_median).mean()),
            'comparison_caveat':'HISTORICAL_STRATUM_MEDIAN_VS_REPEATED_STATION_EQUAL_MEDIAN; POOLED_PLACEMENT_PERCENTILE_DESCRIPTIVE'})
    return pd.DataFrame(rows)

def reproduce_inference(long: pd.DataFrame, output_dir: Path, config_path: Path):
    """Reproduce the validated paired and blocked analysis from gap evaluations."""
    global _CONFIG, METHODS, PAIRS
    _CONFIG = json.loads(config_path.read_text(encoding='utf-8'))
    METHODS = _CONFIG['methods']
    PAIRS = list(combinations(sorted(METHODS), 2))
    comparison=complete_comparison(long)
    strata=stratum_summary(comparison);strata['iqr_rmse']=strata.q75_rmse-strata.q25_rmse
    stations=station_summary(strata)
    summary=stations.groupby(CONDITION+['method'],sort=True).agg(
        physical_stations=('physical_station_id','nunique'),contributing_strata=('seasonal_strata','sum'),
        repeated_base_gaps=('repeated_base_gaps','sum'),median_rmse=('median_rmse','median'),
        median_mae=('median_mae','median'),minimum_station_rmse=('median_rmse','min'),
        maximum_station_rmse=('median_rmse','max'),q25_station_rmse=('median_rmse',lambda x:x.quantile(.25)),
        q75_station_rmse=('median_rmse',lambda x:x.quantile(.75))).reset_index()
    summary['unit']=summary.gas.map({'CO2':'ppm','CH4':'ppb'})
    successful=long[long.execution_status.eq('SUCCESS')]
    pairs=paired_base(successful);pair_strata=paired_strata(pairs);pair_stations=paired_stations(pair_strata)
    intervals,replicates=block_inference(stations,strata,pair_stations,pair_strata)
    names=[(strata,'REV03_GAP_STRATUM_SUMMARY.csv'),(stations,'REV16_GAP_STATION_SUMMARY.csv'),
        (summary,'REV16_GAS_GAP_METHOD_SUMMARY.csv'),(pairs,'REV16_BASE_GAP_PAIRED_CONTRASTS.csv'),
        (pair_strata,'REV16_STRATUM_PAIRED_CONTRASTS.csv'),(pair_stations,'REV16_STATION_PAIRED_CONTRASTS.csv'),
        (intervals,'REV16_BLOCK_BOOTSTRAP_SUMMARY.csv'),(replicates,'REV16_BOOTSTRAP_REPLICATES.csv'),
        (rank_stability(comparison,strata,stations,summary),'REV16_METHOD_RANK_STABILITY.csv'),
        (seasonal_summary(strata),'REV16_SEASON_SPECIFIC_METHOD_SUMMARY.csv'),
        (historical_comparison(long,comparison,summary,intervals),'REV03_HISTORICAL_VS_REPEATED_COMPARISON.csv')]
    response=summary.sort_values(['gas','method','gap_hours']).copy()
    response['one_hour_rmse']=response.groupby(['gas','method']).median_rmse.transform('first')
    response['relative_to_one_hour']=response.median_rmse/response.one_hour_rmse.replace(0,np.nan)
    response['decrease_from_previous_length']=response.groupby(['gas','method']).median_rmse.diff().lt(0)
    names.append((response,'REV16_GAP_LENGTH_RESPONSE.csv'))
    winners=[]
    for (gas,length),g in summary.groupby(CONDITION,sort=True):
        ordered=g.sort_values(['median_rmse','method']);best=ordered.iloc[0];next_best=ordered.iloc[1]
        winners.append({'gas':gas,'gap_hours':length,'LOWEST_OBSERVED_MEDIAN_RMSE_METHOD':best.method,
            'lowest_median_rmse':best.median_rmse,'next_lowest_method':next_best.method,
            'difference_from_next_lowest':next_best.median_rmse-best.median_rmse,
            'exact_tied_lowest_methods':';'.join(g[g.median_rmse.eq(best.median_rmse)].method)})
        shared=comparison[comparison.gas.eq(gas)&comparison.gap_hours.eq(length)].pivot(
            index='base_gap_id',columns='method',values='rmse')
        ci=intervals[intervals.gas.eq(gas)&intervals.gap_hours.eq(length)&
            intervals.identity.eq(best.method)&intervals.block_unit.eq('PHYSICAL_STATION_PRIMARY')].iloc[0]
        winners[-1].update({'lower_rmse_than_next_proportion':float((shared[best.method]<shared[next_best.method]).mean()),
            'ties_with_next_proportion':float((shared[best.method]==shared[next_best.method]).mean()),
            'physical_stations':int(best.physical_stations),'primary_ci_lower':ci.ci_lower,
            'primary_ci_upper':ci.ci_upper,'station_block_status':ci.station_block_status})
    names.append((pd.DataFrame(winners),'REV16_CONDITION_LOWEST_OBSERVED.csv'))
    success_counts=long.groupby(['placement_role','method'],sort=True).execution_status.agg(
        evaluations='size',successful=lambda x:int(x.eq('SUCCESS').sum()),failed=lambda x:int(x.ne('SUCCESS').sum())).reset_index()
    names.append((success_counts,'REV03_METHOD_EXECUTION_COUNTS.csv'))
    pair_counts=pairs.groupby(['placement_role','gas','gap_hours','pair'],sort=True).agg(
        pairwise_available=('base_gap_id','nunique'),complete_five_available=('all_method_complete_base_gap','sum')).reset_index()
    names.append((pair_counts,'REV16_PAIR_AVAILABILITY_COUNTS.csv'))
    # Pairwise-available summaries remain auditable even if complete-five differs.
    if long.execution_status.ne('SUCCESS').any():
        available=pairs[pairs.placement_role.eq('REPEATED_DESIGN')].copy()
        available['all_method_complete_base_gap']=True
        names.append((paired_strata(available),'REV16_PAIRWISE_AVAILABLE_STRATUM_SUMMARY.csv'))
    output_dir.mkdir(parents=True, exist_ok=True)
    for frame,name in names:
        public_name = name.lower().replace('rev16_', '').replace('rev03_', '')
        frame.to_csv(output_dir/public_name, index=False, float_format='%.17g')
    return {'gas_gap_method_summary': summary,
            'paired_station_contrasts': pair_stations,
            'block_bootstrap_summary': intervals,
            'bootstrap_replicate_count': len(replicates)}
