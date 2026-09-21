from pathlib import Path
import io, tarfile
import pytest
from wdcgg_pipeline.parser import _float, parse_wdcgg_layout, iter_wdcgg_rows
from wdcgg_pipeline.qc import map_wdcgg_qc
from wdcgg_pipeline.units import convert_value
from wdcgg_pipeline.models import Species
from wdcgg_pipeline.acquisition import extract_observation, month_hours

FIX=Path(__file__).resolve().parents[1]/"data/examples"
def test_header_and_variable_order():
    q=parse_wdcgg_layout(FIX/"wdcgg_surface_official_format_synthetic.txt"); assert q.columns and q.unit
def test_timestamp_is_utc():
    r=next(iter_wdcgg_rows(FIX/"wdcgg_surface_official_format_synthetic.txt")); assert r.timestamp_utc.utcoffset().total_seconds()==0
@pytest.mark.parametrize("flag,retain",[("1",True),("2",True),("3",False),("",False),("9",False)])
def test_qc(flag,retain): assert map_wdcgg_qc(flag).retain is retain
def test_units(): assert convert_value(1e-6,"mol/mol",Species.CO2)==(1.0,"ppm")
@pytest.mark.parametrize("year,month,hours",[(2024,2,696),(2023,2,672),(2024,1,744),(2024,4,720)])
def test_month_hours(year,month,hours): assert month_hours(year,month)==hours
def test_safe_archive_rejects_traversal(tmp_path):
    a=tmp_path/"bad.tar.gz"
    with tarfile.open(a,"w:gz") as t:
        info=tarfile.TarInfo("../escape_hourly.txt"); info.size=1; t.addfile(info,io.BytesIO(b"x"))
    with pytest.raises(RuntimeError): extract_observation(a,tmp_path/"out")
def test_fill_value_preserved_as_missing():
    assert _float("-999.999") is None and _float("401.25")==401.25
