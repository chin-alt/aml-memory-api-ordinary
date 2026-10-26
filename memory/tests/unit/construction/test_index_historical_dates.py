"""Windows 历史日期回归；只调用纯投影，不访问数据库或模型。"""

from datetime import datetime, timedelta, timezone

from jiuwen_memory.common.time_utils import epoch_milliseconds
from jiuwen_memory.common.type_def import T_EVENT_UNKNOWN, T_INVALID_OPEN, MemoryUnit, Temporal
from jiuwen_memory.common.type_def.filter import FilterClause, FilterOp
from jiuwen_memory.common.type_def.memory_filter import matches_memory_unit
from jiuwen_memory.construction.index_builder_impl._index_ops import index_metadata


def test_historical_dates_index_and_filter_agree():
    for date in (datetime(1903, 12, 17), datetime.min, datetime(1970, 1, 1)):
        unit = MemoryUnit(temporal=Temporal(t_event=date, t_valid=date, t_invalid=date))
        expected = int(date.replace(tzinfo=timezone.utc).timestamp() * 1000)
        for layer in ("l0", "l1", "l2"):
            metadata = index_metadata(unit, layer=layer)
            for field in ("t_event", "t_valid", "t_invalid"):
                assert metadata[field] == expected
                assert matches_memory_unit(unit, FilterClause(field, FilterOp.EQ, expected))
                assert not matches_memory_unit(unit, FilterClause(field, FilterOp.GT, expected))
        assert unit.temporal.t_event is date, "投影不能改写真源时间"


def test_naive_dates_use_utc_and_aware_dates_preserve_offset():
    assert epoch_milliseconds(datetime(2024, 1, 1)) == 1704067200000
    local = datetime(2024, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))
    assert epoch_milliseconds(local) == 1704067200000
    historical = datetime(1903, 12, 17, 8, tzinfo=timezone(timedelta(hours=8)))
    assert epoch_milliseconds(historical) == epoch_milliseconds(datetime(1903, 12, 17))


def test_millisecond_precision_and_negative_truncation():
    assert epoch_milliseconds(datetime.max) == 253402300799999
    epoch = datetime(1970, 1, 1)
    assert epoch_milliseconds(epoch - timedelta(microseconds=1999)) == -1
    assert epoch_milliseconds(epoch - timedelta(microseconds=999)) == 0
    assert epoch_milliseconds(epoch + timedelta(microseconds=1999)) == 1


def test_unknown_and_open_sentinels_unchanged():
    unit = MemoryUnit()
    metadata = index_metadata(unit, layer="l2")
    assert metadata["t_event"] == T_EVENT_UNKNOWN
    assert metadata["t_invalid"] == T_INVALID_OPEN
    assert "t_valid" not in metadata
    for field, expected in (("t_event", T_EVENT_UNKNOWN), ("t_invalid", T_INVALID_OPEN)):
        assert matches_memory_unit(unit, FilterClause(field, FilterOp.EQ, expected))
