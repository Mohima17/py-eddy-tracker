from py_eddy_tracker.dataset.grid import RegularGridDataset
from py_eddy_tracker.data import get_path
from datetime import datetime

g = RegularGridDataset(
    get_path("dt_med_allsat_phy_l4_20160515_20190101.nc"), "longitude", "latitude"
)


def test_id():
    g.add_uv("adt")
    a, c = g.eddy_identification("adt", "u", "v", datetime(2019, 2, 23))
    assert len(a) == 35
    assert len(c) == 36
