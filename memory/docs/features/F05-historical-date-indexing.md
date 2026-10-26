# 历史日期在 Windows 上的索引投影

日期：2026-09-22。影响范围：common 时间投影、construction 全文/向量共享索引投影。

## 背景与证据

LongMemEval S 批次第 3 题的 api.add 在 `_index_ops.index_metadata` 的
`t_event.timestamp()` 处抛出 OSError errno=22。原诊断没有保存该 datetime 的值，
不能断言模型具体抽取出了哪一天。在同一 Windows Python 上，naive 1903/1965 年日期
可复现该错误；数据首会话讨论 Kitty Hawk，存在历史事件时间输入。
这个错误独立于其他题的 LLM 网关 429；修复日期不会消除限流。

## 方案

共享 `common.time_utils.epoch_milliseconds` 用 datetime 差值与整数运算投影，
无时区值按 UTC，带时区值保留其瞬间。全文、向量、分层索引和 MemoryUnit 过滤复核
同时使用它，不修改真源日期，不把合法历史日期丢成 None/未知哨兵。
未知事件与开放有效期的既有哨兵不变。QA、judge、抽取提示词均不变。

拒绝只捕获异常跳过一条记忆：这会无声丢掉可评测事实。
拒绝只修索引：这会让过滤复核与索引下推的毫秒值分叉。

## 验证

`tests/unit/construction/test_index_historical_dates.py` 的四个纯函数测试覆盖历史日期、
边界日期、时区、整数精度、正负亚毫秒截断、索引/过滤一致性、哨兵与真源不变。
本机 venv 没有 pytest，使用 runpy 加载并逐个调用四个测试，全部通过；没有调用模型或数据库。

## 兼容边界

旧代码会把 naive 日期解释为主机本地时区；本次 UTC 投影会改变这类日期的索引毫秒。
已有索引不会自动重建，不能把新旧索引放在同一 scope 做时间过滤比较。
评测失败题的续跑使用新 scope，完成题的结果保持原样，并以批次补充说明记录修复边界。
严格同版本横比需要另开批次全部重跑，不能把保留的旧结果描述为统一版本重测。
