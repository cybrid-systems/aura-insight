# 需求洞察

开源产品的需求洞察。第一类信号是 GitHub issue（标题、正文、标签、评论、反应、时间、仓库）。洞察策略本身是可改写的 Aura 代码：用户的确认 / 否认 / 补充会触发诊断，候选策略在分叉的世界线上重放，**只有严格更好才 KEEP，否则 ROLLBACK**。每次决定都留在审计记录和 `AURA_EVO_LOG` 里。

## 架构

```
浏览器（中文页面）
    │  HTTP JSON
    ▼
Python 宿主                         Aura 进程（每次请求启动一次）
  读示例 CSV/JSON 或可选的 GitHub      aura/insight.aura
  SQLite：原始信号、反馈、策略版本、审计   ├─ 聚类 / 打分 / 晋升 / 遗忘（policy_lib.aura，可变 define）
  把输入写成 JSON，读 Aura 的 JSON 输出  ├─ observe → propose（fiber + std/orch）
                                        ├─ check：证据不足则 back-off；agent:check 为 commit / back-off / escalate
                                        ├─ evo:trial 在子工作区装入候选并重放标注
                                        ├─ evo:commit!：不可行则 ROLLBACK；更优则 KEEP
                                        └─ KEEP 的发布走 mutate:atomic-batch-safe（MutationBoundary）
```

不把摄取、存储和 HTTP 放进 Aura。不使用 `aura::eval` C ABI，也不使用 `aura --serve`。

## 钉住的 Aura

官方 **v0.2.0**，提交 `cf7353373ea276bc03e8364592814382ebd62539`。

`./run.sh` 按 [aura-runner 的 `run.sh`](https://github.com/cybrid-systems/aura-runner) 下载对应架构的 release（linux x86_64 或 arm64），校验 sha256（进度和校验输出在 stderr，stdout 只有二进制路径），不编译、不下载 LLVM。已安装且 `aura --version` 含 `0.2.0` 时直接用它。

## 怎么跑

```bash
./run.sh
```

浏览器打开 <http://127.0.0.1:8080>。没有 GitHub token 也会起来：启动时若库是空的，会装入 `fixtures/issues.json`。

```bash
python3 -m unittest discover -s tests -v
./scripts/smoke.sh
```

端口、库和演化日志：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `INSIGHT_PORT` | `8080` | 页面和 API |
| `INSIGHT_DB` | `data/insight.sqlite` | 信号、反馈、策略、审计 |
| `AURA_EVO_LOG` | `data/evo.jsonl` | `evo:record!` / `evo:commit!` 的世界线 |
| `AURA_BIN` | 自动安装 | 指向已有的 v0.2.0 二进制 |
| `GITHUB_TOKEN` 或 `GH_TOKEN` | 空 | 有 token 才拉在线 issue |

在线拉取（可选，默认仓库 `cybrid-systems/aura`、`aura-perf`、`aura-runner`、`aura-harness`、`aura-memory`）：

```bash
export GITHUB_TOKEN=...
curl -s -X POST http://127.0.0.1:8080/api/ingest/github \
  -H 'content-type: application/json' -d '{}'
```

没有 token 时这个接口返回 400，示例路径不受影响。

## 页面

- 按优先级列出已晋升的假设，以及未晋升 / 判为伪需求的簇。
- 每条假设有证据链（哪些 issue）、策略版本、以及「为什么」的原文。
- 确认 / 否认 / 补充。这是演化的输入。至少 **1 条否认和 2 条确认** 才会试变体，否则记 back-off，不改策略。
- 策略版本、本轮试验和 KEEP / ROLLBACK 历史。
- 一键回滚到以前的版本（走 MutationBoundary 检查、`ast:snapshot`，再 `set-code` 恢复那一版全文，并 `evo:record!` 一条 `user-restore`）。

种子策略是故意写松的：最少 1 条证据就晋升，提问标签也当真实需求，反应数权重 0.05，所以「怎么安装」这种高反应提问会排得很靠前。

诊断出的两个变体：

| 臂 | 规则 | 预期 |
| --- | --- | --- |
| `overstrict` | 最少 4 条才晋升，提问仍当真实需求 | 比种子更差或持平，ROLLBACK |
| `stricter` | 最少 2 条，提问视为伪需求，反应权重降到 0.02 | 同意率严格更高，KEEP |

选择顺序用 `std/evo` 的 UCB（`evo:policy` / `evo:policy-select` / `evo:policy-report`）。两个臂一开始都没试过，声明顺序里 `overstrict` 在前，所以先试它。

同意率 = 标注里 confirm/deny 对上「是否晋升为真实需求」的比例。留出标注（`held_out`）单独计分，变体不得比基线更差。持平不算更好。

## 演化怎么被证明

`tests/test_evolution.py` 用示例 issue 走完这条序列：

1. 否认安装提问、补充一条说明、否认吉祥物（留出）、只确认一组真实需求：每次都是 **BACK-OFF**，策略仍是种子。
2. 再确认另一组真实需求（留出）后，证据够了：`overstrict` **ROLLBACK**（同意率 0.5，不优于基线），`stricter` **KEEP**（同意率 1.0 > 0.5，留出同样从 0.5 到 1.0），发布途径是 `atomic-batch`。提问簇退出晋升列表。
3. 再确认文档簇：两个变体都 **ROLLBACK**，版本号停在 2，名字仍是 `stricter`。
4. 回滚到版本 1：种子恢复，提问重新被晋升。

`AURA_EVO_LOG` 里能看到 `TRIAL`、`SCORED`、`ROLLBACK`（`infeasible: regression`）和 `KEEP`（`committed`），以及 `user-restore`。

## 安全与沙箱

必须说实话：

- 宿主调用是 `aura --quiet --preset dev`，并且 **`AURA_SANDBOX=off`**。
- v0.2.0 在 restricted 沙箱里只把 mutate 授给工作区 0。`evo:trial` 的子工作区 `set-code` 会得到 `mutate not granted`，trial 失败，父世界线不动（`std/evo-core` 写明了这一点，测试套件同样用 `AURA_SANDBOX=off`）。
- 因此这个 MVP **不是**一个收紧了的能力沙箱。Aura 进程拥有 sandbox off 的全部能力。它只处理本地 JSON，不读 GitHub token。
- 候选 KEEP 先 `mutate:safe-yield`，再要求 `mutate:boundary-safe?`，然后 `mutate:atomic-batch-safe` 重绑四个策略参数（这是 MutationBoundary）。原子批次失败才退回整段 `set-code`（#5162 的路径；在 `AURA_SANDBOX=off` 下本机走的是 atomic-batch）。复核分数如果没有变好，会把上一版源码装回去。
- 用户回滚同样先看 boundary，做 `ast:snapshot`，再用 `set-code` 装回那一版全文。

已知问题，只规避、不在本仓库里改 Aura：

| 问题 | 规避 |
| --- | --- |
| #5160 `json-encode` 把向量编成 null；空列表 `()` 也会变成 null | 不用向量。宿主把 `insights`、`trials`、`notes` 等已知列表字段上的 null 还原成 `[]` |
| #5162 trial 之后父工作区 `mutate:rebind` 在 restricted 下 cow-refused | sandbox off 下原子 rebind 可用；失败则 `set-code` |
| #5163 子工作区拒绝 `vector-set!` / `hash-set!` | 策略和打分是纯函数，只构造新的 hash 和 list |

本仓库没有新发现需要另报的 Aura 缺陷。

## MVP 有什么，没有什么

有：

- 信号、证据、假设（描述、置信度、真实/伪需求、优先级）、可改写的策略 define。
- 手写的初始规则，以及上面两条可被门控的变体。没有在线 LLM。
- 观察 → 提议 → 检查 → 安全提交 → 用标注复核 → 审计和记忆（记住已晋升的假设 id）。
- 反馈驱动的 KEEP / ROLLBACK，含留出标注。
- 无 token 的示例数据；可选的 GitHub issue 拉取。
- 中文页面、策略历史、一键回滚。

没有：

- 工单、社交、访谈等第二种在线信号。
- 模拟用户代理。
- 比「晋升则记住」更复杂的记忆提升。
- 生产级加固、多租户、鉴权。
- Aura v0.3 bus 或 v0.4 kernel。

## 本机验证过 / 没验证过

验证过：Linux x86_64 上的示例闭环、HTTP 冒烟、页面能打开并提交反馈。arm64 的 release 包脚本能选中，但没有在 arm64 上跑。没有用真实 `GITHUB_TOKEN` 打过 GitHub API。
