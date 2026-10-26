"""与操作系统本地时区无关的索引时间转换。"""

from datetime import datetime, timezone


def epoch_milliseconds(value: datetime) -> int:
    """带时区值保留其瞬间；无时区值按 UTC 投影，不修改原对象。

    不调用 naive datetime.timestamp()，避免 Windows 的 mktime 拒绝历史日期。
    用整数运算保留毫秒精度，负值按 int(timestamp * 1000) 的规则向零截断。
    """
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    microseconds = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    return microseconds // 1000 if microseconds >= 0 else -((-microseconds) // 1000)
