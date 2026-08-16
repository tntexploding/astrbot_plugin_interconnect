# astrbot_plugin_interconnect

一个轻量级 AstrBot QQ 消息桥接插件。当前可用功能只有两条：

- QQ 消息转为标准 JSON，并通过 HTTP POST 发送到本地服务。
- 本地服务通过 HTTP 请求向指定 QQ 私聊或群聊发送文字和图片。

`v0.1.0` 的 HTTP 双向链路已经完成自动化测试和真实 QQ 联调，当前进入封版
状态。版本变更见 [`CHANGELOG.md`](CHANGELOG.md)，发布验收范围见
[`docs/release_checklist.md`](docs/release_checklist.md)。

当前版本尚未实现 WebSocket。配置界面不会保留无效的 WS 开关或路由；后续
实现 WS 时会沿用本文相同的会话 ID、消息包体和路由规则。

## 统一规则

| 方向 | 一般用法 | 兜底用法 |
| --- | --- | --- |
| QQ -> 本地 | 用 `routes` 按会话类型和 `conversation_id` 转发到 HTTP URL | 会话 ID 不稳定时才使用 `session_alias` |
| 本地 -> QQ | 请求中直接填写 `target.id` 和 `target.conversation_type` | 调用方不能填写目标时使用“HTTP 到 QQ”路由；仍失败时使用 `target.alias` |

三个字段的职责不同：

- `conversation_id`：群号、QQ 号或平台 OpenID，是对外使用的会话身份。
- `unified_msg_origin`：AstrBot 实际发送地址，由插件自动记录和刷新。
- `alias`：人工可读的备用名称，不是正常转发的前置条件。

QQ 入站是否转发只由 `routes` 决定。`sessions.bindings` 只是本地向 QQ 发送时
使用的地址簿，不是 QQ 入站白名单。

## 最小配置

### 1. 启用 HTTP 服务

在 AstrBot WebUI 的插件配置中设置：

```text
enabled = true
local.http.enabled = true
local.http.host = 127.0.0.1
local.http.port = 8765
```

保存后检查：

```text
GET http://127.0.0.1:8765/health
```

监听非回环地址时必须配置 `local.http.auth_token`，请求使用
`Authorization: Bearer <token>`。

### 2. QQ 转发到 HTTP

在 `routes` 新增“QQ 到 HTTP Webhook”：

```text
路由 ID          qq_group_to_las
会话类型         QQ 群聊
会话 ID          123456
发送者用户 ID    留空
文本前缀         留空
正则表达式       留空
必须包含图片     关闭
Webhook URL      http://127.0.0.1:8400/hearing
```

在群 `123456` 发送普通消息即可触发。无需执行绑定命令。命中路由后，插件也会
自动把该会话的发送地址写入 `sessions.bindings`，供反向发送使用。

所有已填写的来源和内容条件使用 AND 逻辑。一条消息需要发往多个 HTTP 地址
时，添加多条普通路由即可。

默认发送的 JSON 核心结构如下：

```json
{
  "schema_version": "1.0",
  "event_type": "message",
  "message_id": "qq-message-id",
  "direction": "qq_to_local",
  "route_id": "qq_group_to_las",
  "source": {
    "type": "qq_group",
    "id": "123456",
    "alias": "qq_group_123456"
  },
  "sender": {
    "id": "654321",
    "name": "Alice",
    "platform": "aiocqhttp",
    "group_id": "123456"
  },
  "content": {
    "text": "hello from QQ",
    "images": [],
    "videos": [],
    "files": []
  },
  "timestamp": "2026-05-30T12:00:00+00:00"
}
```

完整示例见
[`docs/templates/qq_to_http_webhook.standard.json`](docs/templates/qq_to_http_webhook.standard.json)。

### 3. HTTP 发送到 QQ

正常情况下直接指定会话 ID，不需要“HTTP 到 QQ”路由：

```http
POST /v1/messages
Content-Type: application/json
```

```json
{
  "target": {
    "id": "123456",
    "conversation_type": "qq_group"
  },
  "content": {
    "text": "hello from local"
  }
}
```

`conversation_type` 可填 `qq_group` 或 `qq_private`。请求成功时返回
`"ok": true`；投递失败时返回 HTTP 502 和逐目标错误信息。

发送网络图片：

```json
{
  "target": {
    "id": "123456",
    "conversation_type": "qq_group"
  },
  "content": {
    "text": "图片测试",
    "images": [
      {
        "source_type": "url",
        "url": "https://example.com/image.png"
      }
    ]
  }
}
```

只有调用方无法在请求中填写 `target` 时，才新增“HTTP 到 QQ 会话”路由，按
`source.extra.endpoint_id` 映射到一个固定 QQ 会话。

## 会话地址修复

本地向 QQ 发送依赖 AstrBot 会话地址。正常情况下它会在 QQ 消息命中转发路由
时自动记录。发送失败并提示会话未记录或地址不存在时，按以下顺序处理：

1. 在 WebUI 检查 `sessions.auto_record = true`。
2. 检查 `sessions.bindings` 中的会话类型和会话 ID。
3. 让目标群或私聊发送一条消息。已有映射会自动刷新；新会话需让消息命中一条 QQ 到 HTTP 路由。
4. 重试 HTTP 请求，并用 `/interconnect errors 10` 查看失败原因。
5. 仍无法自动修复时，在目标会话执行 `/interconnect bind <alias>`，然后改用 `target.alias`。

最后兜底请求：

```json
{
  "target": {
    "alias": "main_group"
  },
  "content": {
    "text": "fallback message"
  }
}
```

`/interconnect bind` 只负责保存一个备用别名，不会授权或开启 QQ 入站转发。

## 自定义 QQ 到 HTTP JSON

默认使用 `protocol.webhook_payload_mode = standard`。需要兼容已有服务时：

1. 将 `protocol.webhook_payload_mode` 改为 `template`。
2. 在 `protocol.webhook_payload_template_files` 上传或选择一个 `.json` 文件。
3. 多选时只使用列表中的第一个文件。

WebUI 管理的文件位于：

```text
AstrBot/data/plugin_data/astrbot_plugin_interconnect/files/protocol/webhook_payload_template_files/
```

配置界面不提供内联 JSON 编辑器。模板示例：

```json
{
  "text": "${content.text}",
  "sender_id": "${sender.id}",
  "conversation_id": "${source.id}",
  "images": "${content.images}"
}
```

完整字段与占位符见
[`docs/http_communication_spec.md`](docs/http_communication_spec.md)。

## 排错命令

```text
/interconnect status
/interconnect routes
/interconnect route-test [text]
/interconnect sessions
/interconnect deliveries [limit]
/interconnect errors [limit]
/interconnect bind <alias>
/interconnect unbind <alias>
```

QQ 到 HTTP 无消息时，先在对应会话执行 `/interconnect route-test test`。命中
路由但投递失败时查看 `/interconnect errors 10`，再检查目标 HTTP 服务、URL、
鉴权和超时。管理命令本身不会被转发。

实际运行配置由 AstrBot WebUI 管理，通常位于：

```text
AstrBot/data/config/astrbot_plugin_interconnect_config.json
```

`_conf_schema.json` 只定义 WebUI 表单，不是实际运行配置。
