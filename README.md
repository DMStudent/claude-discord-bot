# Claude Discord Bot

通过 Discord 使用完整 Claude Code 能力的 Bot。适用于通过 Google Vertex AI 等第三方 API 接入 Claude 的企业用户，无需 claude.ai 订阅即可获得类似 Channels 的体验。

## 核心特性

- **完整 Claude Code 能力**：文件读写、代码编辑、命令执行、Git 操作、网络搜索等全部可用
- **持久会话**：基于 Claude Agent SDK，每个频道维持一个长驻 Claude Code 连接，`/cost`、`/compact` 等状态命令数据准确
- **所有斜杠命令同步**：启动时自动从 Claude Code 探测并注册 `/compact`、`/review`、`/init` 等全部命令
- **流式输出**：回复过程中实时更新 Discord 消息，而非等待结束后一次性发送
- **线程免 @**：首次 @mention 后，同一频道/线程内后续消息无需再 @
- **安全控制**：用户白名单、角色白名单、频道白名单、消息去重、速率限制

## 架构

```
Discord 消息
    |
    v
discord_adapter.py       消息路由、安全校验、斜杠命令
    |
    v
session_manager.py       每频道持有一个 ClaudeGateway（持久连接）
    |
    v
claude_runner.py         ClaudeGateway -> ClaudeSDKClient
    |                    connect() 建立连接，query() 发送消息
    |                    receive_response() 接收流式事件
    v
stream_consumer.py       text_delta -> 渐进式 Discord 消息编辑
    |
    v
Discord 回复（流式更新）
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
```

如果使用 Vertex AI：

```bash
CLAUDE_CODE_USE_VERTEX=1
ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
CLOUD_ML_REGION=us-east5
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

权限模式建议设为 `auto`，否则需要审批的工具（如 WebSearch）会因无法交互确认而被阻塞：

```bash
CLAUDE_PERMISSION_MODE=auto
```

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

### Claude Code

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CLAUDE_BINARY` | `claude` | Claude CLI 路径（SDK 会自动查找，一般不需要设置） |
| `CLAUDE_WORKING_DIR` | 当前目录 | Claude Code 工作目录 |
| `CLAUDE_MODEL` | (空=默认) | 模型，如 `sonnet`、`opus` |
| `CLAUDE_PERMISSION_MODE` | `default` | 权限模式。建议 `auto`，否则 WebSearch 等工具会被阻塞 |
| `CLAUDE_MAX_BUDGET_USD` | `0` (无限) | 单次会话费用上限 |
| `CLAUDE_ALLOWED_TOOLS` | (空=全部) | 限制可用工具，如 `Read,Glob,Grep,WebSearch` |
| `CLAUDE_SYSTEM_PROMPT` | (空) | 自定义系统提示词 |

### 会话

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SESSION_TIMEOUT_MINUTES` | `60` | 空闲会话自动清理时间（断开 SDK 连接并释放进程） |
| `SESSION_MAX_CONCURRENT` | `5` | 最大并发会话数 |

### 流式输出

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STREAM_EDIT_INTERVAL` | `0.6` | Discord 消息编辑间隔（秒），越小越流畅但更容易触发 rate limit |
| `STREAM_BUFFER_THRESHOLD` | `8` | 首条消息最少字符数 |
| `STREAM_CURSOR` | ` \|` | 流式输出时的光标字符 |

## 使用方式

### 普通对话

在允许的频道中 @mention Bot，或在 DM / Bot 已参与的线程中直接发消息：

```
@小克 帮我看一下 src/main.py 有什么问题
```

首次 @ 后，同一频道内后续消息无需再 @。

### 斜杠命令

所有 Claude Code 斜杠命令自动注册为 Discord 斜杠命令（`:` 转为 `-`）：

| Discord 命令 | 对应 Claude Code | 说明 |
|---|---|---|
| `/compact` | `/compact` | 压缩上下文 |
| `/cost` | `/cost` | 查看累计费用（持久会话，数据准确） |
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
| `/reset` | 重置当前频道的会话（断开旧连接，创建新会话） |
| `/stop` | 中断正在运行的 Claude Code 请求 |
| `/status` | 查看当前会话状态（连接状态、消息数、累计费用、空闲时间） |

## 项目结构

```
claude-discord-bot/
  bot.py                 入口，加载配置并启动 Bot
  config.py              环境变量 -> BotConfig 数据类
  discord_adapter.py     Discord 事件处理、斜杠命令动态注册、消息路由
  claude_runner.py       ClaudeGateway：封装 ClaudeSDKClient，适配事件格式
  stream_consumer.py     流式事件 -> Discord 消息渐进编辑
  session_manager.py     每频道会话管理（持有 ClaudeGateway，超时清理）
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
用户发送消息
  -> 过滤：忽略自身、Bot、重复消息
  -> 安全：用户白名单 + 角色检查
  -> 频道：频道白名单检查
  -> @mention：DM/已参与线程免 @，否则需要 @mention
  -> 速率限制：每用户每分钟 10 条
  -> 获取/创建 Session（持有 ClaudeGateway）
  -> 并发检查：同一频道只允许一个请求
  -> 添加 :eyes: 反应 / typing 状态
  -> gateway.query(prompt) 发送到持久 Claude Code 连接
  -> receive_response() 流式接收，渐进编辑 Discord 消息
  -> 完成后替换为 :white_check_mark: 或 :x:
  -> 标记频道为已参与（后续免 @）
```

### 会话管理

- 每个频道/线程维护一个独立 Session，持有一个 `ClaudeGateway`（`ClaudeSDKClient` 封装）
- 首条消息通过 `connect(prompt)` 建立连接并发送
- 后续消息通过 `query(prompt)` 在同一连接内发送
- SDK 自动管理底层 Claude Code 进程生命周期
- 进程级状态（费用、上下文、工具权限）跨消息完整保持
- 空闲超过 `SESSION_TIMEOUT_MINUTES` 自动断开连接并释放进程
- `/reset` 命令断开旧连接，创建全新会话

### 流式输出

- SDK 的 `receive_response()` 逐条返回类型化事件对象
- `StreamEvent` 中的 `text_delta` 实时追加到缓冲区
- 每 0.6 秒编辑 Discord 消息，显示最新内容 + 光标
- 超过 Discord 2000 字符限制时自动分片
- 遇到 Discord rate limit 自动退避（间隔翻倍，最多 3 次）
- `ResultMessage` 触发最终编辑，去除光标，附加费用信息
- 斜杠命令使用 `interaction.followup.send()` 回复，普通消息使用 `channel.typing()` 显示输入状态

### 斜杠命令同步

启动时运行 `claude -p --output-format stream-json --verbose "hi"`，从 `init` 事件的 `slash_commands` 字段提取所有可用命令，动态注册为 Discord 斜杠命令。安装新的 Claude Code 插件或 Skills 后重启 Bot 即可同步。

## 安全注意事项

- **务必设置 `DISCORD_ALLOWED_USERS`**：不设置时所有人都可以使用，Bot 会在你的机器上执行代码
- **`CLAUDE_PERMISSION_MODE`**：建议设为 `auto`。`default` 模式下 WebSearch 等工具需要交互式确认，但 Bot 无法弹出确认框，会导致请求永远挂起
- **`CLAUDE_WORKING_DIR`**：Claude Code 在此目录下操作，确保指向正确的项目目录
- **线程跟踪持久化**：`~/.claude-discord-bot/threads.json` 记录 Bot 参与过的频道，重启后保留
- **会话隔离**：每个频道独立的 SDK 连接和 Claude Code 进程，互不影响

## 依赖

- Python >= 3.11
- [discord.py](https://github.com/Rapptz/discord.py) >= 2.3.0
- [python-dotenv](https://github.com/theskumar/python-dotenv) >= 1.0.0
- [claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/) >= 0.1.70
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 已安装并完成认证
