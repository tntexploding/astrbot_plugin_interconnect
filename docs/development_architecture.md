# astrbot_plugin_interconnect 开发架构

## 1. 项目边界

本插件只负责消息桥接：

1. 捕获 AstrBot 中的 QQ 私聊或群聊消息。
2. 规范化为内部 `MessageEnvelope`。
3. 按路由发送到 HTTP Webhook。
4. 接收本地 HTTP 消息并发送到指定 QQ 会话。

当前只实现 HTTP 双向通信。WebSocket 尚未实现，因此代码和 WebUI 中不保留
无效的 WS server、topic route 或状态开关。未来实现 WS 时复用相同包体、路由
和会话解析规则，不新增第二套业务模型。

## 2. 强制开发原则

所有后续开发必须同时遵循：

- AstrBot 开发指南：
  `AstrBot/docs/zh/dev/star/guides`。
- Google 开源风格指南，Python 代码保持清晰命名、类型标注和小职责函数。
- 对非显而易见的边界、兼容逻辑和错误恢复写简洁注释。
- 持久化数据只存入 AstrBot `data` 目录，不写入插件源码目录。
- AstrBot handler、网络边界、文件读写和后台任务必须隔离异常，避免单条消息
  导致插件退出。
- Python 使用 Ruff 格式化和检查。
- 网络 I/O 必须异步。禁止使用 `requests`；HTTP 使用 `aiohttp` 或
  `httpx`。
- 不大范围修改插件工作区之外的文件。

## 3. 单一运行模型

### 3.1 QQ 到本地

主路径：

1. QQ event 经 `QqMessageReceiver` 转为 `MessageEnvelope`。
2. `QqMessageReceiver.should_capture()` 检查消息类型、平台和媒体开关。
3. `InterconnectRouter` 使用 `source.type + source.id` 匹配 QQ 会话。
4. 命中路由后，`SessionStore` 自动记录 AstrBot 发送地址。
5. `MessageDispatcher` 调用 `HttpWebhookSender`。

会话映射绝不作为 QQ 入站白名单。没有命中 `routes` 的消息不会转发；命中
路由但未绑定的消息会正常转发并自动建立地址映射。

兜底只保留 `source.session_alias`，用于平台无法提供稳定会话 ID 的情况。

### 3.2 本地到 QQ

主路径是请求直接携带：

```json
{
  "target": {
    "id": "123456",
    "conversation_type": "qq_group"
  }
}
```

执行顺序固定为：

1. 请求有非空 `target`：直接解析目标并发送，不执行兜底路由。
2. 请求省略 `target`：匹配 `local_to_qq` 路由。
3. 会话 ID 无法解析时：刷新自动地址映射。
4. 自动刷新仍失败时：使用 WebUI 映射或 `/interconnect bind <alias>`，
   再通过 `target.alias` 发送。

“HTTP 到 QQ 会话”路由只服务于无法逐请求提供目标的旧系统，不是正常发送的
必要配置。

## 4. 模块边界

```text
main.py
interconnect/
  config.py
  lifecycle.py
  models.py
  interfaces.py
  router.py
  protocol.py
  qq/
    receiver.py
    sender.py
  local/
    http_server.py
    http_webhook.py
    auth.py
  services/
    dispatcher.py
    diagnostics.py
    media_store.py
  storage/
    sessions.py
```

职责如下：

| 模块 | 唯一职责 |
| --- | --- |
| `main.py` | AstrBot 生命周期、事件和管理员命令 |
| `config.py` | 配置迁移、解析和启动前校验 |
| `models.py` | 协议无关的消息值对象 |
| `qq/receiver.py` | AstrBot QQ event -> envelope |
| `qq/sender.py` | envelope -> AstrBot message chain |
| `router.py` | 无副作用匹配 |
| `services/dispatcher.py` | 选择直达目标或路由目标并调用 sink |
| `local/http_server.py` | HTTP 鉴权、请求校验和响应 |
| `local/http_webhook.py` | 异步 HTTP POST、超时和有限重试 |
| `protocol.py` | 标准 JSON 与文件模板渲染 |
| `storage/sessions.py` | QQ 会话 ID 到 AstrBot 发送地址的映射 |
| `services/media_store.py` | 媒体校验、下载和 data 目录缓存 |
| `services/diagnostics.py` | 内存投递统计和最近错误 |

模块通过 `MessageEnvelope`、`MessageSink` 和小型 Protocol 接口协作，不直接
访问其他模块的内部状态。

## 5. 内部消息包

`MessageEnvelope` 是唯一跨模块消息对象，包含：

- `message_id`
- `direction`：`qq_to_local` 或 `local_to_qq`
- `source` 和 `target`
- `sender`
- `content`
- `message_type`
- `route_id`
- `raw_refs`
- `timestamp`
- `extra`

适配器只做边界转换。路由器不得读取 AstrBot event；QQ sender 不得解析 HTTP
请求；HTTP sender 不得拼接另一套业务包体。

## 6. 配置模型

WebUI 直接影响运行的配置为：

| 顶层项 | 作用 |
| --- | --- |
| `enabled` | 插件总开关 |
| `qq` | QQ 捕获类型、平台和媒体 |
| `local.http` | HTTP server、鉴权和请求体上限 |
| `sessions` | 自动记录开关与可编辑会话地址簿 |
| `routes` | QQ->HTTP 主路由与 HTTP->QQ 兜底路由 |
| `protocol` | 标准协议或 JSON 文件模板 |
| `media` | 媒体缓存和限制 |
| `observability` | 日志与投递历史 |

不再保留全局 QQ ID 白名单。QQ 来源限制统一配置在每条
`qq_to_http_webhook.source` 中：

- `type`
- `conversation_id`
- `session_alias`，仅兜底
- `sender_id`

一个目标使用一条路由。旧多目标路由在启动迁移时拆成多条普通路由；旧
`advanced_route` 中可表达的 HTTP/QQ target 同样拆分；未实现的 WS target
被移除。

实际配置由 AstrBot 管理，`_conf_schema.json` 只是表单定义。配置迁移通过
`AstrBotConfig.save_config()` 保存，不直接写 AstrBot 配置文件。

## 7. 会话地址簿

会话项包含：

- `alias`
- `source_type`
- `conversation_id`
- `platform`
- `unified_msg_origin`
- `updated_at`

`conversation_id` 是外部身份，`unified_msg_origin` 是 AstrBot 发送地址。
`alias` 只用于人工兜底。

自动记录规则：

1. 新 QQ 会话只有在命中转发路由时才自动加入地址簿。
2. WebUI 已存在的会话映射在该会话任意被捕获的消息上刷新地址，即使没有
   QQ->HTTP 路由。
3. 相同会话 ID、类型和兼容平台不得产生重复映射。
4. WebUI 的 `sessions.bindings` 是可编辑真源；AstrBot KV 仅用于旧版本一次
   迁移和运行适配。

## 8. HTTP 协议与模板

HTTP 协议唯一规范见
[`http_communication_spec.md`](http_communication_spec.md)。

标准模式由 `MessageProtocolCodec` 输出版本化 JSON。Webhook URL、Token、
Headers、超时和重试等运行配置不得泄漏到消息包体。

模板模式只读取
`protocol.webhook_payload_template_files` 选择的第一个 JSON 文件。文件必须：

- 位于 AstrBot 插件 data 目录。
- 使用 `.json` 扩展名。
- 为 UTF-8 JSON 对象。
- 不超过 1 MiB。
- 解析后仍为 JSON 对象。

旧内联模板字段只用于已有配置兼容，在 WebUI 中隐藏；不得新增在线编辑入口或
每条路由的模板覆盖。

## 9. 错误隔离

- 配置错误：启动前抛出 `ConfigError`，指出完整字段路径。
- QQ event 错误：记录日志并结束当前 event，不影响 AstrBot。
- HTTP 入站错误：返回稳定 JSON 错误和 4xx/5xx。
- Webhook 错误：转换为 `DispatchResult`，只对网络错误、超时、429 和 5xx
  做有限重试。
- QQ 发送错误：返回未记录、地址缺失、平台不可用或媒体拒绝等明确原因。
- 清理错误：使用 best-effort stop，插件卸载必须释放 server、client session
  和媒体任务。

日志默认不记录完整消息包。敏感 Token 不进入标准包体、投递记录或普通日志。

## 10. 生命周期

`PluginRuntime` 统一持有 router、dispatcher、HTTP server、HTTP client、
QQ adapters、media store 和 diagnostics。

启动：

1. 迁移旧路由配置。
2. 加载并校验 typed config。
3. 同步 WebUI 会话映射与旧 KV。
4. 启动媒体维护、共享异步 HTTP client 和可选 HTTP server。

终止：

1. 停止 HTTP server。
2. 关闭共享 HTTP client。
3. 停止媒体维护任务。
4. 即使一个步骤失败，也继续清理其余资源。

## 11. 验证要求

每次行为修改至少执行：

```text
python -m ruff format .
python -m ruff check .
python -m unittest discover -s tests -v
```

重点测试：

- QQ 会话类型、会话 ID、发送者和内容条件匹配。
- 未绑定但命中路由的 QQ 消息正常转发。
- 已配置会话自动补全和刷新 `unified_msg_origin`。
- HTTP 明确目标优先于兜底路由。
- 会话 ID 主发送与 alias 兜底。
- 标准包体不泄漏 Webhook 配置。
- JSON 文件模板路径、大小、格式和占位符错误。
- 鉴权、请求体上限、超时、重试和生命周期清理。
- 旧路由与旧 target 类型迁移。

## 12. WebSocket 后续约束

开始实现 WS 前必须先完成真实 server、连接生命周期、鉴权、发送 sink 和测试。
只有可运行后才能加入 WebUI。其业务规则必须与 HTTP 一致：

- WS->QQ 默认直接携带 `target.id + conversation_type`。
- 省略 target 时才使用本地到 QQ 兜底路由。
- QQ->WS 使用 `source.type + conversation_id`。
- 使用同一 `MessageEnvelope` 和同一会话地址簿。
- 不引入 WS 专属绑定、第二套 JSON 或重复来源白名单。
