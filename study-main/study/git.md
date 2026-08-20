# 本地开发 = 不影响线上(origin/develop_langragh)

线上(生产)版本 = `origin/develop_langragh`。你本机的任何东西,只要满足以下任意一条,都**永远不可能**影响线上:

1. 未 `git add` / 未 `git commit` 的修改 → 只是工作区草稿,不会进 git;
2. 被 `.gitignore` 忽略的文件(如 `.venv312/`、`.uv-python/`、`.env`、`__pycache__/`)→ git 根本不看它;
3. 只在 **feature 分支** 上提交/推送 → 只有通过 MR 合并才会上 `origin/develop_langragh`。

**黄金法则:永远不要在 `mirror/origin/develop_langragh`(基线镜像)和 `origin/develop_langragh`(线上)上直接 commit、push。
提交和推送只发生在 feature 分支,最终经 MR 合并到 `origin/develop_langragh` 才算"上线上"。**

---

## 分支角色速查

| 分支 | 角色 | 允许操作 |
| --- | --- | --- |
| `origin/develop_langragh` | 线上主干(远程) | 只读;唯一入口是 MR |
| `mirror/origin/develop_langragh` | 本地基线镜像 | 只 `git merge --ff-only origin/develop_langragh` |
| `bwq_develop` | 本地开发分支 | 随意改、提交;推送前先 rebase 到 mirror |
| `feature/xxx` | MR 功能分支 | 只在这里提交/推送,最后 MR 合入线上 |

## 当前仓库状态(已就位,可直接开工)

```text
* mirror/origin/develop_langragh        ← 当前分支,与线上 0 0 严格一致
  feature/debug_code_context_lost       ← 干净空白 feature 分支(0 0,可直接用或 rename)
  bwq_develop                           ← 本地开发分支(与线上一致)
stash@{0}                               ← wip: 未完成调试,先放一边 (recovered),不需要就 git stash drop
.gitignore                              ← 已含 .venv312/、.uv-python/(环境永不进 git)
环境                                     ← .venv312 已重建(alembic/uvicorn 齐全)
```

---

## 1. 环境与学习代码保护(永不进 git)

- `.venv312/`、`.uv-python/` 已在 `.gitignore` → 虚拟环境不会再出现在 `git status`,`git add .`/`git stash -u` 也带不走它(曾因此把 275MB 环境吞进 stash)。
- **学习/实验代码**:一律只 `git add <具体文件>`,**绝不 `git add .` / `git add -A`**。
- `git status` 里出现不认识的未跟踪文件 → 先判断:入库 or 加 `.gitignore`,不要无脑 commit。

## 2. 开工前:有 WIP 先隔离

```bash
cd ~/桌面/develop_langgraph/emb_claude_cicd

git status -sb                                        # 看当前分支和改动

# 当前在基线镜像且有未提交修改 → 暂存
git stash push -u -m "wip: 先放一边"
```

> `-u` 会把未跟踪的新文件一起暂存;只想暂存已跟踪的修改就去掉 `-u`。

## 3. 新功能开发(唯一安全路径)

```bash
# 3.1 把本地基线镜像刷到最新线上
git switch mirror/origin/develop_langragh
git fetch --prune origin
git merge --ff-only origin/develop_langragh

# 3.2 确认与线上严格一致(预期输出: 0 0)
git rev-list --left-right --count mirror/origin/develop_langragh...origin/develop_langragh

# 3.3 从基线切出功能分支(已有干净 feature 分支则直接复用)
git switch -c feature/具体功能名称
# 例如: git switch -c feature/test-workspace-update
```


# 不懂， 但是会进入 “开发时那种为提交的状态“

git checkout 01201959 && git diff 01201959 bf64c752 | git apply
(emb-ci-agent) pc@pc-System-Product-Name:~/桌面/develop_langgraph/emb_claude_cicd$ git checkout 01201959 && git diff 01201959 bf64c752 | git apply
注意：正在切换到 '01201959'。

您正处于分离头指针状态。您可以查看、做试验性的修改及提交，并且您可以在切换
回一个分支时，丢弃在此状态下所做的提交而不对分支造成影响。

如果您想要通过创建分支来保留在此状态下所做的提交，您可以通过在 switch 命令
中添加参数 -c 来实现（现在或稍后）。例如：

  git switch -c <新分支名>

或者撤销此操作：

  git switch -

通过将配置变量 advice.detachedHead 设置为 false 来关闭此建议

HEAD 目前位于 01201959 Merge branch 'fix/mr-description' into develop_langragh


之后只在 feature 分支上改、提交、推送。改完想合入线上,走 MR(见第 5 节)。

## 4. 开发中要切走 / 同步最新线上

```bash
git stash push -u -m "wip"                            # 保存当前 feature 的修改

git switch mirror/origin/develop_langragh             # 同步线上
git fetch --prune origin
git merge --ff-only origin/develop_langragh

git switch feature/defer-final-approve                       # 回到功能分支,重放基线
git rebase mirror/origin/develop_langragh

git stash pop                                          # 取回修改(有冲突手动解决)
git status -sb
```

## 5. 提交、推送并创建 MR

```bash
# 只 add 本次真正要提交的文件,不要 git add . 一锅端
git add <本次修改的文件>
git commit -m "feat: describe the change"

# 推送前自查:确认当前在 feature 分支,而不是 mirror/origin/develop_langragh
git status -sb

# 创建并推送 MR(目标固定为线上 develop_langragh)
git push -u origin feature/具体功能名称 \
  -o merge_request.create \
  -o merge_request.target=develop_langragh \
  -o merge_request.title="feat(web): update test workspace"
```

线上只有在你这个 MR 被合并之后才会变化。

## 6. 同步"别人已上线"的版本到本地

```bash
git switch mirror/origin/develop_langragh
git fetch --prune origin
git merge --ff-only origin/develop_langragh
git rev-list --left-right --count mirror/origin/develop_langragh...origin/develop_langragh   # 预期: 0 0
```

## 7. 事故恢复

- **stash 被清(如 `git stash clear`)、改动疑似丢失** → stash 提交对象还在 git 里,可找回:
  ```bash
  git fsck --lost-found            # 列出"悬空 commit",按提交信息找你的 stash
  git stash store <commit-id> -m "wip: recovered"   # 塞回 stash 列表
  git stash list
  ```
- **未提交/未跟踪文件被删** → git 无法找回(只有编辑器回收站之类的途径),所以重要草稿要么 stash、要么及时 commit。
- **分支名不想要了** → `git branch -m 新名字`(当前分支)或 `git branch -m 旧名 新名`。

## 8. 禁止 / 高危操作(会害本地修改影响线上)

- 在 `mirror/origin/develop_langragh` 上直接 `git commit`、`git push`;
- 对共享分支(origin/develop_langragh / mirror 镜像)执行 `git push --force`;
- `git add .` 后不清点直接 commit(会把调试代码、临时文件一并带上);
- 在 MR 合并前,对 feature 分支 `git push --force` 覆盖已 review 的提交。

## 9. 提交 / 推送前自查清单

- [ ] `git status -sb` → 当前在 `feature/xxx`,不是 `mirror/origin/develop_langragh`;
- [ ] `git diff --cached` → 本次提交只含想提交的文件;
- [ ] `.venv312/`、`__pycache__/`、`.env` 等不出现在未跟踪列表(已被 .gitignore 吃掉);
- [ ] `git rev-list --left-right --count feature/xxx...origin/develop_langragh` → 与线上差异符合预期。



# spec行动指南:

## 1, 保存当前修改
cd /home/pc/桌面/develop_langgraph/emb_claude_cicd
git add -A && git commit -m "wip: 保存当前进度"

## 2, 确定编码约束
在 Claude Code 里输入：

```python
/speckit-constitution 项目必须测试先行(pytest)，orchestrator 不依赖 claude-agent-sdk，Jenkins 保持薄 job，硬件板锁资源必须受控
```

回车后 AI 会生成宪法文件，*这一步定义了你说的"编码约束"*。

## 新增功能需要步骤

```python
$ /speckit-specify 我想增加一个【你的功能，比如：请求超时后自动重试，并把重试次数记录到数据库】
$ /speckit-plan 使用现有的 FastAPI + LangGraph + PostgreSQL，复用 orchestrator 的现有模式，不加新依赖
$ /speckit-tasks
$ /speckit-implement
```

能解决什么问题: 需求模糊;

# Git 开发分支复用指南

## 背景

本地保留一批「无关文件」,要求:**任何分支都不追踪、不进提交、不进 MR/PR**,但文件留在本地随时可用。分两类处理:

- **未跟踪文件**(specs 编号归档、`docs/pipeline_*.md`、`.codegraph/`、speckit 技能、`uv.lock` 等)→ 写入 `.git/info/exclude` 忽略
- **已跟踪文件的本地修改**(`README.md`、`.gitignore`、`.specify/*` 等)→ `git update-index --skip-worktree` 冻结:git 永远看不到这些改动,不进提交/推送,但文件内容保留在磁盘上,开发照常用

## 1. 未跟踪文件:写入本地忽略(仅本仓库生效)

规则写在 `.git/info/exclude`(git 的本地私有忽略文件,永不进入任何提交)。**新建克隆后需重新执行**：

```bash
cat >> .git/info/exclude <<'EOF'

# ===== 本地无关文件:任何分支都不索引、不进提交/PR(仅本仓库生效)=====
.codegraph/
.agents/skills/speckit-*/
.specify/scripts/
.specify/integrations/codex.manifest.json
docs/pipeline_*.md
specs/0*/
uv.lock
git.md
EOF
```

校验：

```bash
git status --short          # 无关文件不再出现
git check-ignore .codegraph/ uv.lock   # 逐个确认命中
```

## 2. 已跟踪文件的本地修改:skip-worktree 冻结

`git update-index --skip-worktree` 只接受**具体文件路径**(目录/通配符无效)。按需选一种：

```bash
# 方式一:逐个列出要冻结的文件(推荐,精确)
git update-index --skip-worktree README.md .gitignore \
  .specify/feature.json .specify/init-options.json .specify/integration.json \
  .specify/integrations/speckit.manifest.json .specify/memory/constitution.md \
  .specify/templates/checklist-template.md .specify/templates/plan-template.md \
  .specify/templates/tasks-template.md

# 方式二:把"当前有本地修改的已跟踪文件"一次性全部冻结(注意:会连想提交的改动一起冻结)
git ls-files -m | xargs git update-index --skip-worktree
```

校验与解除：

```bash
git ls-files -v | grep '^S'                    # 大写 S 开头 = 已冻结
git update-index --no-skip-worktree <文件路径>  # 解除冻结(恢复为可提交状态)
```

## 3. 从主线新建开发分支

```bash
git fetch origin
git checkout -b feat/新任务 origin/develop_langragh
```

- 未跟踪无关文件被 exclude 忽略、已跟踪文件的本地修改被 skip-worktree 冻结 → 新分支 `git status` 完全干净,`git add -A` 也带不上任何无关内容,天然进不了提交/推送/MR
- 冻结与忽略只对**当前克隆**生效;换机器或重新 clone 后需重跑第 1、2 步

## 4. 注意事项 / 高危

- **`git reset --hard` 会覆盖 skip-worktree 冻结文件的本地内容**(不可恢复),重要内容先 `git commit` 或备份
- 冻结期间,这些文件来自线上的更新不会被拉到本地;想同步需先 `--no-skip-worktree` 解除冻结
- 若想让团队所有分支自带这些规则,需把规则并入 `.gitignore` 并经 MR 合并到 `develop_langragh`(会进入仓库历史,一般不建议)


# 什么时候需要push到Jenkins？ 什么时候本地修改即可？？

## 背景:代码分两侧跑,同步规则不同

这个项目是「控制面 + 执行面」两段式:

- **控制面**(本机 orchestrator,端口 8000)= 跑**你本地分支**的代码。改 `orchestrator/`、`web/`、`deploy/.env` → **本地重启即生效,不用 push**。
- **执行面**(Jenkins `devbot` 多分支 job)= 每次 build 从 **git 远端** checkout 分支代码。凡在 Jenkins/容器里跑的代码,本地改了**不 push,Jenkins 永远用旧版**。

## 对照表:改哪类代码,要做什么才能测

| 目录 | 跑在哪 | 改动后要做的 | 需不需要 push |
| --- | --- | --- | --- |
| `orchestrator/` | 本机控制面 | 重启 orchestrator | 不需要 |
| `web/frontend/` | 浏览器 | dev 模式热更新 | 不需要 |
| `deploy/.env` | 本机配置 | 重启 orchestrator | 不需要 |
| `jenkins/`(Jenkinsfile/stages/lib) | Jenkins 机器 | commit + push | **需要** |
| `agent/` | Jenkins 容器(随 git 走) | commit + push | **需要** |
| `agent_tools/` | Jenkins 容器(随 git 走) | commit + push | **需要** |
| `scripts/` | Jenkins 容器(随 git 走) | commit + push | **需要** |
| `prompts/` | Jenkins 容器(随 git 走) | commit + push | **需要** |
| `config/*.json` | Jenkins 容器(scm.py 读) | commit + push | **需要** |

> `agent/`、`scripts/` 是通过挂载的活代码跑(engine.groovy:`PYTHONPATH=/work`),**随 git 走、push 即生效,无需重建镜像**。但前提是 push —— Jenkins 的工作区代码永远从 git 远端 checkout,不是你的本地目录。

## 本地测试(orchestrator / web 改动,不 push)

```bash
# 停掉旧 orchestrator,再用 deploy/.env 配置拉起
pkill -f "uvicorn orchestrator.main:app"
set -a; source deploy/.env; set +a
nohup uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 > /tmp/orch_uvicorn.log 2>&1 &
curl -sf -m2 http://127.0.0.1:8000/api/health   # 验证起来了
```

> 注意:进程必须带着 `deploy/.env` 的环境变量启动,否则 ROUTE_VIA 等配置取不到 → 会沿用旧的或不生效。可用 `tr '\0' '\n' < /proc/<PID>/environ | grep ROUTE` 核对已加载配置。

## Jenkins 侧代码改动(必须 push 才能测)

```bash
# 1. 先确认在正确分支(当前开发分支 = bwq_verify)
git status -sb

# 2. 只 add 本次要提交的文件(别 git add . 一锅端)
git add jenkins/stages/mr_cleanup.groovy jenkins/Jenkinsfile orchestrator/...

# 3. 提交并 push 到 bwq_verify
git commit -m "feat(exec): ..."
git push origin bwq_verify
```

push 之后**不用重启 Jenkins** —— devbot 下次 build 时自动 checkout 到该分支最新代码。

## 特殊情况:改了 Jenkinsfile 的参数表

如果改了 `Jenkinsfile` 顶部的 `properties([parameters(...))`(参数定义),push 后需**手动跑一次该 job** 让参数表刷新(会失败也没关系,参数定义在流水线开头就执行了)。验证:

```bash
curl -s -u "chris_zhu:113f8dbf3d310ab0ef8abf9ac967200ae0" \
  "http://10.88.70.229:8080/job/devbot/job/bwq_verify/api/json?tree=property\[parameterDefinitions\[name,choices\]\]" \
  | grep -o "stage-mr_cleanup"
```

## 一句话总结

> **控制面/前端/配置 → 本地重启即可;执行面(Jenkins/agent/scripts/prompts/config)→ commit + push 到 bwq_verify 即可,不用重启 Jenkins。push 到 bwq_verify 不影响线上(develop_langragh),线上只走 MR。**

## 排查工具:确认 Jenkins job 侧代码是不是最新的

```bash
# 看远端分支某文件是否存在/内容
git ls-tree origin/bwq_verify --name-only jenkins/stages/ | grep mr_cleanup

# 看本地与远端是否同步(无输出 = 已同步)
git status -sb
git log --oneline origin/bwq_verify..HEAD
```

## 排查工具:确认 orchestrator 读到的配置

```bash
# 进程实际加载的 ROUTE_VIA
tr '\0' '\n' < /proc/$(pgrep -f "uvicorn orchestrator.main" | head -1)/environ | grep ROUTE
# 配置文件里的
grep ROUTE deploy/.env
```

