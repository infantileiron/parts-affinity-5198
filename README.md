# 远航 · 维保备件联用发现

将维保作业中的备件集合变成可解释的联用规则，并让维修策划组核对兼容性后形成备件包方案。

## 场景和业务模块

远航设备服务的维修策划组负责泵组、压缩机、风机三类设备，现有180张历史作业。每天从维修记录导出 `远航_维保用件_20261115.xlsx`，人工划线找共同用件，再把经验写进指引。三个痛点是重复备件把次数放大、只看一起出现而忽略总体使用率、方案缺少来源证据。上游为实际维保作业；本项目替代集合整理、关联挖掘和兼容性审阅；下游为组长编写作业指引。本项目不管理库存、采购或销售订单。

五模块是作业清单（录入/修订）、领退流水（领用/退料登记、逐件对账与结清）、阈值配置（异步挖掘）、规则解释（支持度/置信度/提升度）、备件包（来源规则/说明/签核）。实体 Job、MiningRun、AssociationRule、Kit、LedgerEntry、Audit 都真实持久化；规则按批次单独入表，备件包通过rule_id关联。

作业用件现在可以按作业登记领用与退料流水。领出的备件可能整批退回，因此“实际用件”由结清净用量决定，而不是领用次数：流水逐笔有唯一流水号和数量，退料引用原领用；乱序到达的退料先暂挂，原领用到齐后补算；跨作业引用和累计超退拒绝入账（保留拒绝痕）。作业核算状态 manual（手工用件）→ open（已接流水，核算中）→ settled（已结清）；只有结清作业中净用量为正的件进入新挖掘，未结清作业整体排除，历史批次快照不变。已接流水的作业不能手改用件；结清后补料须先重开核算。

|角色|页面权限|典型动作|
|---|---|---|
|admin|全部，独占审计和签核|签核适用范围|
|analyst|业务读写、挖掘、导出|登记领退流水、结清对账、配置阈值并起草备件包|
|viewer|全部业务只读、导出|核对来源规则与逐件对账；后端禁止写入/计算|

## 快速启动

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

前端 [localhost:18207](http://localhost:18207)，后端 [localhost:19207/docs](http://localhost:19207/docs)，MySQL端口17707。账号为 `admin@demo.local`、`analyst@demo.local`、`viewer@demo.local`，密码均 `Demo123!`。`SEED_DEMO=true` 自动执行种子；手动再跑自然键幂等，不覆盖人工修订。初始3账号、180作业、12类标准备件，无预制计算结果。

## 一天的工作剧本

1. 08:50 viewer 打开YH-1115-001至180，核对泵组作业背景。
2. 09:00 analyst 查看目录：轴承、密封圈、润滑脂、滤芯、垫片、联轴器、皮带、紧固件、叶轮、阀片、传感器、清洗剂。
3. 09:10 录入 `YH-1115-181`，泵组，用件“轴承,润滑脂,密封圈”。
4. 09:20 检查重复用件会去重，不因领了两份润滑脂而计两次事务。
5. 09:30 建立“11月泵组联用复核”，支持度0.15、置信度0.6、提升度1。
6. 09:40 worker完成后刷新，进入规则解释，核对共同作业数和分母。
7. 10:00 选择有业务价值的一条规则，点击起草备件包，写“仅限同规格泵组，核对密封材质和数量”。
8. 10:10 admin签核；10:20 viewer下载 `parts-affinity-<run-id>.json`。

### 领退流水剧本（solo-2611）

1. analyst 在“领退流水”页给 `YH-1115-181` 登记领用 `I-181-1`：轴承×2、润滑脂×1，作业转为核算中，不进入新挖掘。
2. 先登记退料 `R-181-1` 引用 `I-181-1` 轴承×1：原领用已到，直接入账，可退余额剩1。
3. 再登记退料 `R-181-2` 引用 `I-181-1` 轴承×2：累计超退，409 拒绝并保留拒绝痕。
4. 另开作业登记一笔引用不存在流水的退料：暂挂；原领用到齐后自动补算为已入账。暂挂未清时结清会被拒绝，页面可撤回暂挂。
5. 流水号重试同一笔：返回 duplicate，不重复记账；同号改数量：409 冲突。
6. 结清 `YH-1115-181`：轴承净1、润滑脂净1；净用量为0的备件不写入用件。新挖掘快照只含已结清与手工用件作业，核算中作业列在 provenance.excluded_open。
7. 结清后补料：409 提示重开核算；重开后补登流水，再结清重新计算净用量。已有批次快照保持不变。

## 算法口径和失败态

Python标准库实现Apriori，最大频繁项集长度3。每张含备件的有效作业是一个事务，parts转集合后计数，空事务排除并记录excluded；有效事务N至少2。每张作业内的重复件不增加支持度。最小计数ceil(N×min_support)，用1e−12容差避免浮点整数边界。

对不相交非空A/B，support=count(A∪B)/N，confidence=count(A∪B)/count(A)，lift=confidence/support(B)。候选由上一层频繁项集连接，先检查所有子集频繁再计数。按提升度、置信度、支持度排序，签名作稳定次序。用户可配置支持度(0,1]、置信度(0,1]、提升度[0,50]，只接受12项标准目录。阈值太高时成功完成但没有规则，页面提示降低阈值或增加同类作业；不伪造规则。样本不足则任务failed。相同来源作业生成稳定checksum；提交即冻结样本，后续修订不改变已有报告。

关联不是必须联换，提升度也不证明因果。备件包用件来自规则并集，人工只能填写名称、适用性和限制；已签核不可覆盖。没有数量推算、设备兼容性数据库或采购动作。

## 工程与目录

|层|实现|
|---|---|
|前端|原生HTML/CSS/ES modules，Nginx|
|后端|Python3.11、FastAPI、SQLAlchemy、PyJWT|
|后台|Redis队列，独立worker执行Apriori，文件心跳|
|数据库|MySQL8.0，Redis7|

恰好frontend/backend/worker/db/redis五服务，全部healthcheck。backend等db/Redis健康，worker额外等backend，frontend等backend。数据库、Redis与计算产物各用命名卷；worker结果在共享`/artifacts`。Nginx `/api/` 转发到后端根路由；Redis和worker不映射宿主端口。`docker compose down -v --remove-orphans` 清理本项目数据。

`backend/src`分routes/controllers/services/models/schemas/middlewares/config/utils/constants；`frontend/src`分api/stores/pages/components/common/hooks/utils/router/constants/types。共用表格、字段、状态标签，表单hooks与HTML转义；请求ID和安全响应头中间件，业务写入审计。首次启动ORM建表，migrations保存实际DDL，不含版本迁移执行器。

本地开发：先起db/redis，backend安装requirements，配置DATABASE_URL/REDIS_URL/JWT_SECRET后 `uvicorn src.main:app --reload`；前端使用Nginx同源代理或本地同源开发代理。环境变量为COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO、NETWORK_SUBNET，见.env.example。当前显式子网10.253.108.0/24用于规避本机默认池耗尽，迁移可调整。演示凭据与JWT密钥部署时需替换。

枚举ROLES/STATES/EQUIPMENT/PARTS/LEDGER_STATES/DIRECTIONS/ENTRY_STATES分别在后端`src/constants/enums.py`、输入schema及前端`src/constants/enums.js`；实体列见models/entities.py，新增DDL见`migrations/002_ledger.sql`。领退流水领域测试在`backend/tests/test_ledger.py`，用临时SQLite运行：`cd backend && python -m unittest discover -s tests`。测试与截图在`../../test-artifacts/gb-3108/`。

已知范围：逐条表单/API录入，无CSV导入/删除/分页；领退流水按需登记，不管理库存余额、不推算采购量；规则单worker计算，最大项集3件，签核在站内完成，无采购、外部通知或自动作业指令。Google Fonts离线时回退字体；未做大规模压测。许可证MIT，见LICENSE。
