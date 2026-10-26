# LongMemEval 原评测器的 HTTP 记忆接入

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-21 |
| 影响范围 | evaluation/longmemeval/{run,runner,http_harness}.py；同级 aml-adapter |
| 测试基线 | aml-adapter/tests/test_repo_lme.py 离线 HTTP + 真内存内核集成测试 |

## 背景

本机需要按题型各抽三题控制成本，且记忆必须经过比赛 Add/Search 服务。
原 harness 直接调用 MemoryAPI 并强制 no-source 抽取；已有 HTTP 冒烟只有简化 QA、
Top-100 以及正文/分数，不足以保留原仓库 Top-200、多 cutoff 和时间信息的判分口径。

## 决策

保留原 run.main、Dataset、Runner 与全部 metrics 模块。Runner 增加可选 HTTP harness，
通过 bearer 鉴权的 `/add` 写入每个原 MemorySeed，`/search` 获取排序后的记忆；
QA 的提示词、调用顺序、模型参数、yes/no 解析及异常行为均使用原实现。
相关性标注与答案不发送给记忆服务。会话选择由原 Dataset 控制。
2026-09-22 按用户要求，适配器批测改用 S 完整历史并显式关闭 oracle_sessions；
原一键脚本默认值不变，QA 逻辑不变。同日后续用户要求两组对照实验，
新增 Oracle-30 与 S-6，均排除拒答和无标准答案题；历史模式由各自清单 variant 显式传递，
因此重新允许明确标注的 Oracle 清单。结果模式不匹配时报失败，不能混用两组产物。

HTTP 兼容模式仅由本机批测启动器开启：Add 接收 seed 的 metadata/tags/原始 occurred_at，
返回 unit IDs；Search 允许 Top-200，额外返回 message_at/event_at、trajectory、
MemoryAPI 检索耗时和 storage.recall 耗时。HTTP 总耗时另记，避免混同原性能边界。
普通比赛模式不接受这些扩展。原始无时区日期按原值还原，避免本地时区造成漂移。

服务仍执行同一适配器的普通抽取，明确设置 infer=true、retain_source=true，
基线 fidelity=false。去除的是 no_source_extraction 及其专用行为，不改变内核算法。
源记忆保留会改变抽取结果，因此结果协议显式记录抽取路径，不能声称与原 direct 路线同分。

## 拒绝的方案

- 直接调用 MemoryAPI：不满足必须经过 HTTP 的要求。
- 复制 Answer/Judge 到适配器：会造成提示词和判分规则漂移。
- 将 Top-200 静默降为 Top-100、用单一 created_at 代替两个时间字段：改变原 QA 输入。
- 为节省调用仅评一个 cutoff：不满足本次保持原 QA 流程的要求。

## 验证

离线测试执行真正的 run.main → Runner → ASGI HTTP → 普通内存内核，模型响应使用确定性桩。
检查原提示词逐字一致、三档调用顺序和参数、yes/no/无法解析三种结果、真实时间与记忆 ID、
普通来源保留、无金标泄漏、Add 重试幂等、鉴权，以及常规模式拒绝评测扩展。
旧 Oracle 清单计划为 21 题、440 Add、21 Search、126 次 QA 请求。
新 S 清单保留相同题号与顺序，包含 987 会话、10,322 消息、10,163 Add；
Search 和 QA 调用数不变。33 项离线测试通过，覆盖干扰会话保留与拒绝 Oracle 清单。
未访问公司模型网关；真实模型运行交由用户执行。

## 已知限制

- HTTP 扩展是本地对比评测协议，不能宣称是原封不动的比赛请求合同。
- 每题单 worker、串行执行；适配器批处理可用独立进程最多同时执行六题，各题独立服务和 scope。
  父进程串行写入批次状态；失败停止派发并等待在途题，中断时清理本批次 worker 及 HTTP 子进程。
  不支持过滤器、as_of 或自动数据清理。
- Add 的 unit ID 对照保存在进程内；批量续跑以题为边界，失败题用新服务、新 scope 重跑，
  不在服务重启后伪造映射。已完成但答错的题同样跳过。
- 审计提供 HTTP 召回及 seed→unit IDs，不提供原 no-source extractor 全量内部快照。
- QA 沿用仓库原请求设置，不额外注入 disable-thinking；网关须支持这些参数。
- 每桶三题的 S 子集保留各题完整历史，但不能代表完整 500 题或 AML 官方成绩。
