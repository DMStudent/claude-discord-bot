# Claude Discord Bot

通过 Discord 使用完整 Claude Code 能力的 Bot。适用于通过 Google Vertex AI 等第三方 API 接入 Claude 的企业用户，无需 claude.ai 订阅即可获得类似 Channels 的体验。

## 核心特性

- **完整 Claude Code 能力**：文件读写、代码编辑、命令执行、Git 操作、网络搜索等全部可用
- **持久会话**：基于 Claude Agent SDK，每个频道维持一个长驻 Claude Code 连接，`/cost`、`/compact` 等状态命令数据准确
- **所有斜杠命令同步**：启动时自动从 Claude Code 探测并注册全部命令
- **流式输出**：回复过程中实时更新 Discord 消息
- **Thinking 显示**：可选显示模型思考过程（Discord spoiler 格式）
- **文件附件**：支持 Discord 文件上传分析，Claude 修改的文件可自动回传
- **运行时切换**：模型、effort 级别可在对话中随时切换
- **消息队列**：繁忙时排队而非拒绝
- **会话管理**：命名、恢复历史会话、超时自动清理
- **MCP / 插件 / 多目录**：通过配置扩展 Claude Code 能力
- **线程免 @**：首次 @mention 后，同一频道内后续消息无需再 @
- **安全控制**：用户白名单、角色白名单、频道白名单、速率限制

## 架构

```
Discord 消息
    |
    v
discord_adapter.py       消息路由、安全校验、附件处理、斜杠命令、消息队列
    |
    v
session_manager.py       每频道持有一个 ClaudeGateway（持久连接）
    |
    v
claude_runner.py         ClaudeGateway（封装 ClaudeSDKClient）
    |                    connect() 建立连接，query() 发送消息
    |                    _receive() 接收流式事件
    v
stream_consumer.py       text_delta / thinking / task progress -> Discord 消息编辑
    |
    v
Discord 回复（流式更新 + 附件 + 思考过程）
```

基于 `claude-agent-sdk` Python SDK 实现持久会话。每个频道维持一个长驻 `ClaudeSDKClient` 连接，所有消息在同一个会话内处理，进程级状态（费用、上下文、工具权限）跨消息保持。

## 快速开始

### 1. 创建 Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications) 创建应用
2. 在 Bot 页面开启 **Message Content Intent**
3. 复制 Bot Token
4. 使用 OAuth2 URL Generator 邀请 Bot 到你的服务器，权限需要：
   - Send Messages
   - Read Message History
   - Add Reactions
   - Attach Files
   - Use Slash Commands

### 2. 安装

```bash
cd /path/to/claude-discord-bot
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
```

编辑 `.env`，至少填写：

```bash
DISCORD_TOKEN=你的bot_token
DISCORD_ALLOWED_USERS=你的discord_user_id
CLAUDE_WORKING_DIR=/path/to/your/project
CLAUDE_PERMISSION_MODE=auto
```

权限模式建议设为 `auto`，否则需要审批的工具（如 WebSearch）会因无法交互确认而被阻塞。

### 4. 启动

```bash
# 前台运行（调试用）
source .venv/bin/activate
python bot.py

# 后台运行
./start.sh
```

`start.sh` 会自动杀掉旧进程，日志写入 `log/claude_bot.YYYYMMDD`。

## 配置项

### Discord

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DISCORD_TOKEN` | (必填) | Bot Token |
| `DISCORD_ALLOWED_USERS` | (空=全部允许) | 允许的用户 ID，逗号分隔 |
| `DISCORD_ALLOWED_ROLES` | (空) | 允许的角色 ID，逗号分隔 |
| `DISCORD_ALLOWED_CHANNELS` | (空=全部允许) | 频道白名单，逗号分隔 |
| `DISCORD_REQUIRE_MENTION` | `true` | 服务器中是否需要 @mention（DM 和已参与的线程不需要） |
| `DISCORD_REACTIONS` | `true` | 处理时显示 :eyes:，完成后显示 :white_check_mark: / :x: |
| `DISCORD_ALLOW_ATTACHMENTS` | `true` | 是否接受 Discord 文件附件 |
| `DISCORD_MAX_ATTACHMENT_SIZE_MB` | `10` | 单个附件最大大小（MB） |
| `DISCORD_SEND_FILE_OUTPUTS` | `false` | Claude 修改的文件是否自动作为附件回传到 Discord |

### Claude Code — 基础

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CLAUDE_BINARY` | `claude` | Claude CLI 路径 |
| `CLAUDE_WORKING_DIR` | 当前目录 | Claude Code 工作目录 |
| `CLAUDE_MODEL` | (空=默认) | 模型，如 `sonnet`、`opus` |
| `CLAUDE_PERMISSION_MODE` | `default` | 权限模式。建议 `auto` |
| `CLAUDE_MAX_BUDGET_USD` | `0`（不限制） | 单次会话费用上限 |
| `CLAUDE_ALLOWED_TOOLS` | (空=全部) | 限制可用工具，如 `Read,Glob,Grep,WebSearch` |
| `CLAUDE_SYSTEM_PROMPT` | (空) | 自定义系统提示词 |

### Claude Code — 增强

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CLAUDE_EFFORT` | (空=默认) | 思考深度：`low`、`medium`、`high`、`max` |
| `CLAUDE_FALLBACK_MODEL` | (空) | 主模型过载时自动降级的备用模型 |
| `CLAUDE_ADD_DIRS` | (空) | 额外目录，逗号分隔，Claude 可访问这些目录的文件 |
| `CLAUDE_MCP_CONFIG` | (空) | MCP 服务器配置，JSON 文件路径或内联 JSON |
| `CLAUDE_PLUGINS` | (空) | 插件路径，逗号分隔 |
| `CLAUDE_TASK_BUDGET_TOKENS` | `0` | 长任务 token 预算上限 |

### 显示

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SHOW_THINKING` | `false` | 是否在 Discord 中显示模型思考过程（spoiler 格式） |

### 会话

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SESSION_TIMEOUT_MINUTES` | `60` | 空闲会话自动清理时间 |
| `SESSION_MAX_CONCURRENT` | `5` | 最大并发会话数 |
| `MESSAGE_QUEUE_SIZE` | `0` | 消息队列大小。0=繁忙时拒绝，>0=排队等待 |

### 流式输出

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STREAM_EDIT_INTERVAL` | `0.6` | Discord 消息编辑间隔（秒） |
| `STREAM_BUFFER_THRESHOLD` | `8` | 首条消息最少字符数 |
| `STREAM_CURSOR` | ` \|` | 流式输出时的光标字符 |

## 使用方式

### 普通对话

在允许的频道中 @mention Bot，或在 DM / Bot 已参与的线程中直接发消息：

```
@小克 帮我看一下 src/main.py 有什么问题
```

首次 @ 后，同一频道内后续消息无需再 @。

### 文件附件

直接在 Discord 中拖拽或上传文件，附带消息发送给 Bot。文件会保存到工作目录的 `.discord-uploads/` 下，Claude 可通过 Read 工具读取分析。

如果开启 `DISCORD_SEND_FILE_OUTPUTS=true`，Claude 创建或修改的文件会自动作为附件回传到 Discord。

### 斜杠命令

所有 Claude Code 斜杠命令自动注册为 Discord 斜杠命令（`:` 转为 `-`）：

| Discord 命令 | 对应 Claude Code | 说明 |
|---|---|---|
| `/compact` | `/compact` | 压缩上下文 |
| `/cost` | `/cost` | 查看累计费用 |
| `/init` | `/init` | 初始化 CLAUDE.md |
| `/review` | `/review` | 代码审查 |
| `/security-review` | `/security-review` | 安全审查 |
| `/context` | `/context` | 上下文使用情况 |
| `/insights` | `/insights` | 会话洞察 |
| `/superpowers-brainstorming` | `/superpowers:brainstorming` | 头脑风暴 |
| `/superpowers-writing-plans` | `/superpowers:writing-plans` | 编写计划 |
| ... | ... | 启动时自动同步全部命令 |

### Bot 管理命令

| 命令 | 说明 |
|---|---|
| `/reset` | 重置当前频道的会话 |
| `/stop` | 中断正在运行的请求 |
| `/status` | 查看会话状态（名称、模型、effort、费用、队列等） |
| `/sessions` | 列出最近的历史会话 |
| `/resume <id>` | 恢复历史会话（支持前 8 位短 ID） |
| `/model <name>` | 运行时切换模型（如 `sonnet`、`opus`） |
| `/effort <level>` | 设置思考深度（low/medium/high/max），下次新建会话或 `/reset` 后生效 |
| `/session-name <name>` | 为当前会话命名 |

## 项目结构

```
claude-discord-bot/
  bot.py                 入口，加载配置并启动 Bot
  config.py              环境变量 -> BotConfig 数据类（含所有 SDK 增强配置）
  discord_adapter.py     Discord 事件处理、斜杠命令、附件处理、消息队列
  claude_runner.py       ClaudeGateway：封装 ClaudeSDKClient，适配事件格式
  stream_consumer.py     流式事件 -> Discord 消息编辑（含 thinking、task progress、文件追踪）
  session_manager.py     每频道会话管理（含命名、模型/effort override、队列）
  message_formatter.py   2000 字符分片、工具状态格式化、ANSI 清理
  security.py            用户/频道白名单、消息去重、速率限制、线程跟踪
  start.sh               启动脚本（自动杀旧进程、后台运行、日志归档）
  .env.example           配置模板
  requirements.txt       Python 依赖
  pyproject.toml         项目元数据
```

## 工作原理

### 消息处理流程

```
用户发送消息（可附带文件）
  -> 过滤：忽略自身、Bot、重复消息
  -> 安全：用户白名单 + 角色检查
  -> 频道：频道白名单检查
  -> @mention：DM/已参与线程免 @，否则需要 @mention
  -> 速率限制：每用户每分钟 10 条
  -> 附件处理：下载到 .discord-uploads/，路径拼入 prompt
  -> 获取/创建 Session（持有 ClaudeGateway）
  -> 并发检查：排队（MESSAGE_QUEUE_SIZE>0）或拒绝
  -> 添加 :eyes: 反应 / typing 状态
  -> gateway.query(prompt) 发送到持久 Claude Code 连接
  -> receive_response() 流式接收
     -> text_delta: 渐进编辑 Discord 消息
     -> thinking_delta: spoiler 格式显示思考（SHOW_THINKING=true 时）
     -> tool_use: 显示工具状态 + 追踪文件修改
     -> task progress: 显示任务进度
     -> result: 最终编辑，附加费用，发送修改文件
  -> 完成后替换为 :white_check_mark: 或 :x:
  -> 标记频道为已参与（后续免 @）
  -> 处理队列中的下一条消息
```

### 会话管理

- 每个频道/线程维护一个独立 Session，持有一个 `ClaudeGateway`
- 首条消息通过 `connect(prompt)` 建立连接
- 后续消息通过 `query(prompt)` 在同一连接内发送
- 进程级状态（费用、上下文、工具权限）跨消息完整保持
- `/model` 运行时切模型（SDK `set_model()`，无需重连）
- `/effort` 设置思考深度（下次新建会话生效）
- `/session-name` 命名会话（SDK `rename_session()`）
- `/sessions` + `/resume` 查看和恢复历史会话
- 空闲超过 `SESSION_TIMEOUT_MINUTES` 自动断开并释放进程
- 消息队列：繁忙时排队，空闲后自动处理

### 流式输出

- SDK 的 `receive_response()` 逐条返回类型化事件对象
- `text_delta` 实时追加，每 0.6 秒编辑 Discord 消息
- `thinking_delta` 以 Discord spoiler 格式（`||text||`）显示在独立消息中
- 工具调用显示状态（如 "Running: `git status`"），同时追踪文件修改
- Task 进度/完成/失败通知显示对应 emoji 和描述
- 超过 2000 字符自动分片，代码块跨片保持完整
- Discord rate limit 自动退避（间隔翻倍，最多 3 次）
- 斜杠命令使用 `interaction.followup.send()` 回复

### 斜杠命令同步

启动时运行 `claude -p --output-format stream-json --verbose "hi"`，从 `init` 事件的 `slash_commands` 字段提取所有可用命令，动态注册为 Discord 斜杠命令。安装新的 Claude Code 插件或 Skills 后重启 Bot 即可同步。

## 安全注意事项

- **务必设置 `DISCORD_ALLOWED_USERS`**：不设置时所有人都可以使用，Bot 会在你的机器上执行代码
- **`CLAUDE_PERMISSION_MODE`**：建议设为 `auto`。`default` 模式下 WebSearch 等工具需要交互式确认，但 Bot 无法弹出确认框
- **`CLAUDE_WORKING_DIR`**：Claude Code 在此目录下操作，确保指向正确的项目目录
- **附件安全**：上传的文件保存在工作目录下，Claude 有完整读写权限
- **线程跟踪持久化**：`~/.claude-discord-bot/threads.json` 记录 Bot 参与过的频道，重启后保留
- **会话隔离**：每个频道独立的 SDK 连接和 Claude Code 进程，互不影响

## 依赖

- Python >= 3.11
- [discord.py](https://github.com/Rapptz/discord.py) >= 2.3.0
- [python-dotenv](https://github.com/theskumar/python-dotenv) >= 1.0.0
- [claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/) >= 0.1.70
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 已安装并完成认证
