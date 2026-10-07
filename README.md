# Strava 近 42 天训练数据工具

这是一个本地命令行工具，可从 Strava API 获取活动数据，也可批量导入本地 FIT 文件，生成 JSON、CSV 和 TCX，供 Codex 分析间歇训练、CP5 等短时表现。API 模式只使用 Python 标准库；FIT 导入需要安装 `requirements.txt` 中的解析库。

## 首次授权

1. 在 Strava API 应用设置中将 **Authorization Callback Domain** 设为 `localhost`。
2. 在仓库根目录复制配置模板，再填入自己的 Strava API 凭据：

   ```powershell
   Copy-Item .\strava_secrets_sample.json .\strava_secrets.json
   ```

   `strava_secrets.json` 已被 Git 忽略。也可以设置环境变量 `STRAVA_CLIENT_ID` 和 `STRAVA_CLIENT_SECRET`；环境变量优先。不要把真实凭据写入 `strava_tool.py` 或提交到 Git。
3. 在 PowerShell 中运行授权命令。浏览器会打开 Strava 授权页面；授权后本机回调会自动保存令牌：

   ```powershell
   python .\strava_tool.py auth
   ```

工具请求 `read` 与 `activity:read_all` 权限，以便读取私有活动。访问令牌和刷新令牌保存在 `%LOCALAPPDATA%\strava-analysis\tokens.json`。

## 拉取与更新

```powershell
python .\strava_tool.py fetch
```

默认查询当前 UTC 时间往前 42 天，并生成或更新：

- `strava_data/activities_42days.json`：活动索引及抓取元数据，每项活动包含单项完整记录的相对路径。
- `strava_data/activities_42days.csv`：常用摘要字段，采用 UTF-8 BOM，方便表格软件打开。
- `strava_data/activity_details/{activity_id}.json`：单项活动详情、laps 和高分辨率 streams 原始数据。
- `strava_data/activity_details/{activity_id}.tcx`：时间序列 TCX，包含 API 提供的时间、GPS、海拔、心率、踏频、速度和功率。室内活动没有 GPS 时，时间、功率等序列仍会保存。

也可指定时间范围，例如 `python .\strava_tool.py fetch --days 90`。已完整下载的活动会使用本地缓存；再次运行会拉取新增或不完整的记录。要强制重新下载所有详情和 streams，使用 `python .\strava_tool.py fetch --refresh-details`。Strava API 有请求限流；如果活动较多而本次未完成，已经保存的单项记录会保留，稍后重跑即可续取。

每次成功获取活动列表后，工具会从单项记录中读取 UTC 开始时间，删除早于本次查询窗口的 `activity_details` JSON 及对应 TCX；日期缺失或无法解析的文件会保留。`--days` 同时决定查询和详情保留窗口，因此切换回较短窗口会清理较长窗口的旧详情。活动摘要列表每次都重新查询，每页最多 200 条活动；完整缓存的单项详情和 streams 则不会重复请求。

这些是 Strava API 提供的活动详情，不等同于设备原始 FIT 文件。TCX 由活动详情和 streams 生成；未上传到 Strava 或 API 未提供的传感器数据不会出现在导出中。单项 JSON 保留完整 API stream 序列，是做细节分析的主要数据源。

## 从 FIT 文件导入

1. 将 `.fit` 文件放入仓库根目录的 `fit_import/` 文件夹，也可以放在其子文件夹中。此目录已被 Git 忽略。
2. 首次使用时安装 FIT 解析依赖：

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. 运行导入命令：

   ```powershell
   python .\strava_tool.py import-fit
   ```

默认只保留活动开始时间在当前 UTC 时间往前 42 天内的文件。可用 `--days 90` 改窗口，或用 `--input "D:\Activities\FIT"` 指定其他目录。程序会递归扫描 `.fit` 文件、跳过重复文件，并直接生成与 API 模式相同布局的 `activities_42days.json`、CSV、`activity_details/{id}.json` 和 TCX；FIT 生成的活动 ID 是文件内容的稳定本地标识，索引的 `source` 会标明数据来自 FIT。若最近 42 天没有活动，索引会是空列表。

FIT 里实际存在的时间、距离、GPS、海拔、速度、心率、踏频、功率、温度和 laps 会写入对应的 streams/字段；文件没有提供的数据保持缺失。若文件提供本地时间戳，索引会记录 FIT 中的 UTC offset；否则按 UTC 标注。

Codex 在仓库内分析时可先运行 `python .\strava_tool.py import-fit --if-present`：存在默认 FIT 导入目录和文件时，程序自动完成筛选与转换；没有 FIT 文件时，程序不改动现有 API 数据。导入与 API 抓取写入相同的窗口文件名，因此每次运行会更新该窗口索引，不会把两个来源自动合并。原始 FIT 文件不会被修改或删除。

## 上传到 GitHub

仓库忽略 `strava_data/`、`fit_import/` 和 `strava_secrets*.json`，公开模板 `strava_secrets_sample.json` 是例外。活动数据可能包含精确路线、活动时间、心率和功率等个人信息；不要用 `git add -f` 强行提交这些本地文件。

## 用 Codex 读取与分析

仓库中的 `skills/strava-analysis/` 是可单独安装的 Codex Skill，提供导入 FIT 并分析统一 Strava 风格 JSON、CSV、TCX 的指引；Skill 不含个人活动数据或 API 凭据。

让 Codex 先读 `strava_data/activities_42days.json`，再根据每项活动的 `local_files.json` 路径读取完整记录。逐项 JSON 的 `streams` 可能包含：

- `time`：距活动开始的秒数。
- `watts`、`heartrate`、`cadence`：功率（W）、心率（bpm）、踏频（rpm）。
- `distance`、`altitude`、`latlng`、`velocity_smooth`：累计距离（m）、海拔（m）、经纬度、平滑速度（m/s）。
- `temp`、`moving`、`grade_smooth`：温度（°C）、移动状态、平滑坡度（百分比）。

可以直接提问，例如：

- “读取每次骑行的完整 streams，找出 5 分钟最佳平均功率，并比较各周变化。”
- “分析这次间歇训练每个 5 分钟工作段的平均功率、心率和恢复情况。”
- “从功率序列识别间歇，把工作段和恢复段分别汇总。”

缺失或空 stream 表示 Strava 没有提供该序列，不应当作零。摘要中的距离/爬升单位是米，时间是秒，速度是米/秒；`start_date` 为 UTC，`start_date_local` 为活动当地时间。
