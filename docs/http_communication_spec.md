# AstrBot Interconnect HTTP 通信规范

本文是插件 HTTP 通信格式的唯一规范。除特别说明外，JSON 字符串使用 UTF-8。

## 1. 服务与鉴权

配置：

```text
local.http.enabled
local.http.host
local.http.port
local.http.auth_token
local.http.max_body_bytes
```

接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 服务状态 |
| `POST` | `/v1/messages` | 本地服务向 QQ 发送消息 |

`auth_token` 非空时，两个接口都必须携带：

```http
Authorization: Bearer <token>
```

监听 `127.0.0.1` 或 `::1` 时可在本机调试中留空。监听局域网地址时插件要求
配置 Token，否则拒绝启动。

## 2. 本地服务到 QQ

### 2.1 最小请求

群聊：

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

私聊：

```json
{
  "target": {
    "id": "654321",
    "conversation_type": "qq_private"
  },
  "content": {
    "text": "hello from local"
  }
}
```

`target.type` 可以省略，默认且唯一的新类型为 `qq_session`。

### 2.2 顶层字段

| 字段 | 必需 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- | --- |
| `message_id` | 否 | string | 插件生成 UUID | 调用方消息 ID |
| `source` | 否 | object | HTTP 来源 | 本地调用方信息，供兜底路由匹配 |
| `target` | 条件必需 | object | 本地占位目标 | 直接发送时指定 QQ 会话；省略时使用兜底路由 |
| `sender` | 否 | object | 空对象 | 本地逻辑发送者信息 |
| `content` | 是 | object | 无 | 消息内容 |
| `extra` | 否 | object | `{}` | 调用方扩展数据 |

未知顶层字段当前会被忽略。调用方不应依赖这一行为扩展协议。

### 2.3 `target`

| 字段 | 必需 | 可填值 | 作用 |
| --- | --- | --- | --- |
| `type` | 否 | `qq_session` | 可省略 |
| `id` | 推荐 | string | 群号、QQ 号或平台 OpenID |
| `conversation_type` | ID 模式推荐 | `qq_group`、`qq_private` | 会话类型 |
| `source_type` | 否 | 同上 | `conversation_type` 的兼容名称 |
| `platform` | 否 | string | 同一 ID 在多个适配器存在时用于消歧 |
| `alias` | 兜底 | string | WebUI 映射或 `/interconnect bind` 创建的别名 |
| `extra` | 否 | object | 兼容扩展 |

`id` 与 `alias` 至少填写一个。两者同时存在时，插件先按
`id + conversation_type + platform` 查找，再使用 `alias`。

旧值 `type=qq_session_alias` 会在 HTTP 入口转换为 `qq_session`，仅用于兼容。

### 2.4 `source`

| 字段 | 必需 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `type` | 否 | `http_endpoint` | 本地来源类型 |
| `id` | 否 | `""` | 来源实例 ID |
| `alias` | 否 | `""` | 来源别名 |
| `extra.endpoint_id` | 否 | `""` | 省略 target 时供兜底路由匹配 |

只有省略 `target` 时，`source.extra.endpoint_id` 才通常需要填写。

### 2.5 `sender`

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| `id` | string | 本地发送者 ID |
| `name` | string | 显示名称 |
| `platform` | string | 调用方平台 |
| `group_id` | string | 调用方分组，可选 |
| `extra` | object | 扩展数据 |

`sender` 只进入内部标准包，不会改变 QQ 消息的实际发送账号。

### 2.6 `content`

当前 QQ sender 实际发送：

- `text`
- `images`

`text` 与 `images` 至少有一个非空。其他标准内容字段可被解析，但当前不会
发送到 QQ，调用方不应将其作为已实现能力。

| 字段 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `text` | string | `""` | 文本 |
| `images` | array | `[]` | 图片列表 |
| `mentions` | array[string] | `[]` | 标准包兼容字段 |
| `extra` | object | `{}` | 内容扩展 |

图片对象常用字段：

| 字段 | 必需 | 可填值 | 作用 |
| --- | --- | --- | --- |
| `source_type` | 是 | `url`、`file` | 图片来源 |
| `url` | URL 模式 | HTTP(S) URL | 网络图片 |
| `file_path` | file 模式 | data 目录内允许的路径 | 本地图片 |
| `media_id` | 否 | string | 调用方媒体 ID |
| `mime_type` | 否 | string | MIME 类型 |
| `sha256` | 否 | string | 校验摘要 |
| `size_bytes` | 否 | integer | 字节数 |
| `extra` | 否 | object | 扩展数据 |

完整请求：

```json
{
  "message_id": "las-20260816-001",
  "source": {
    "type": "http_endpoint",
    "id": "las",
    "extra": {
      "endpoint_id": "notifications"
    }
  },
  "target": {
    "id": "123456",
    "conversation_type": "qq_group",
    "platform": "aiocqhttp"
  },
  "sender": {
    "id": "local-service",
    "name": "LAS"
  },
  "content": {
    "text": "构建完成",
    "images": [
      {
        "source_type": "url",
        "url": "https://example.com/result.png",
        "mime_type": "image/png"
      }
    ]
  },
  "extra": {
    "job_id": "build-42"
  }
}
```

## 3. 目标选择顺序

插件使用固定顺序，避免直达请求被路由意外覆盖：

1. 请求包含非空 `target`：直接发送到该 QQ 会话。
2. 请求省略 `target`：匹配 `local_to_qq` 路由。
3. 路由没有命中：返回投递失败。

兜底路由示意：

```text
模板             HTTP 到 QQ 会话
source.type       http_endpoint
source.endpoint_id notifications
target.source_type qq_group
target.id          123456
```

请求：

```json
{
  "source": {
    "type": "http_endpoint",
    "extra": {
      "endpoint_id": "notifications"
    }
  },
  "content": {
    "text": "route fallback"
  }
}
```

## 4. 本地到 QQ 响应

成功：

```json
{
  "ok": true,
  "message_id": "las-20260816-001",
  "results": [
    {
      "target": {
        "type": "qq_session",
        "id": "123456",
        "alias": "",
        "extra": {
          "source_type": "qq_group"
        }
      },
      "ok": true,
      "message": "sent"
    }
  ]
}
```

目标存在但投递失败时返回 HTTP 502：

```json
{
  "ok": false,
  "message_id": "las-20260816-001",
  "results": [
    {
      "target": {
        "type": "qq_session",
        "id": "123456",
        "alias": "",
        "extra": {
          "source_type": "qq_group"
        }
      },
      "ok": false,
      "message": "QQ session '123456' has not been recorded."
    }
  ]
}
```

请求级错误：

```json
{
  "ok": false,
  "error": {
    "code": "invalid_request",
    "message": "target.id or target.alias is required for QQ session targets."
  }
}
```

| HTTP 状态 | code | 含义 |
| --- | --- | --- |
| 400 | `invalid_json` | JSON 无法解析 |
| 400 | `invalid_request` | 字段或内容非法 |
| 401 | `unauthorized` | Bearer Token 错误 |
| 413 | `request_too_large` | 超过请求体上限 |
| 500 | `internal_error` | 未预期的服务端错误 |
| 502 | 无请求级 code | 请求有效，但 QQ 投递失败 |

## 5. 会话地址与恢复

插件不能仅凭群号或 QQ 号重新构造所有 AstrBot 适配器的发送地址，因此维护
`sessions.bindings`：

```json
{
  "alias": "qq_group_123456",
  "source_type": "qq_group",
  "conversation_id": "123456",
  "platform": "aiocqhttp",
  "unified_msg_origin": "...",
  "updated_at": "2026-08-16T12:00:00+00:00"
}
```

通常无需人工创建。QQ 消息命中 QQ->HTTP 路由时自动记录。WebUI 已存在的会话
项会在该会话下一次发言时自动补全或刷新地址。

若返回“未记录”或“没有发送地址”：

1. 检查 `sessions.auto_record`。
2. 检查映射中的 `source_type`、`conversation_id` 和可选 `platform`。
3. 让目标会话发送一条消息；新会话需命中一条 QQ->HTTP 路由。
4. 重试请求。
5. 最后在目标会话执行 `/interconnect bind <alias>`，并发送
   `{"target":{"alias":"<alias>"},...}`。

手动别名只是发送地址兜底，不控制 QQ 入站转发权限。

## 6. QQ 到 HTTP Webhook

### 6.1 触发条件

只有启用的“QQ 到 HTTP Webhook”路由会触发 POST。来源可限制：

- `source.type`：`qq_group`、`qq_private` 或 `*`
- `source.conversation_id`
- `source.sender_id`
- `source.session_alias`，仅兜底

内容可限制：

- `match.text_prefix`
- `match.regex`
- `match.require_image`

所有非空条件使用 AND 逻辑。QQ 会话不需要预先绑定。

### 6.2 标准包体

```json
{
  "schema_version": "1.0",
  "event_type": "message",
  "message_id": "qq-message-id",
  "direction": "qq_to_local",
  "route_id": "qq_group_to_las",
  "message_type": "mixed",
  "source": {
    "type": "qq_group",
    "id": "123456",
    "alias": "qq_group_123456",
    "extra": {
      "group_id": "123456",
      "user_id": "654321",
      "platform": "aiocqhttp"
    }
  },
  "target": {
    "type": "http_webhook",
    "id": "las",
    "alias": "",
    "extra": {}
  },
  "sender": {
    "id": "654321",
    "name": "Alice",
    "platform": "aiocqhttp",
    "group_id": "123456",
    "extra": {}
  },
  "content": {
    "text": "hello from QQ",
    "images": [],
    "videos": [],
    "files": [],
    "attachments": [],
    "links": [],
    "forwards": [],
    "mentions": [],
    "extra": {}
  },
  "raw_refs": {
    "astrbot_message_id": "qq-message-id",
    "unified_msg_origin": "...",
    "raw_message_id": "qq-message-id",
    "extra": {}
  },
  "timestamp": "2026-08-16T12:00:00+00:00",
  "extra": {}
}
```

字段：

| 字段 | 作用 |
| --- | --- |
| `schema_version` | `protocol.schema_version` |
| `event_type` | 当前固定为 `message` |
| `message_id` | 标准化消息 ID |
| `direction` | 固定为 `qq_to_local` |
| `route_id` | 实际命中的路由 |
| `message_type` | `text`、`image`、`mixed` 等 |
| `source` | QQ 会话；`id` 是统一会话 ID |
| `target` | Webhook 逻辑目标 |
| `sender` | QQ 发送者 |
| `content` | 文本、媒体、链接、转发和 mentions |
| `raw_refs` | AstrBot 原始引用，可配置关闭 |
| `timestamp` | ISO 8601 时间 |
| `extra` | 扩展对象，可配置关闭 |

Webhook 的 URL、Token、Headers、超时和重试配置不会进入标准包体。

`protocol.include_raw_refs=false` 删除整个 `raw_refs`。
`protocol.include_extra=false` 递归删除所有名为 `extra` 的扩展对象。

完整示例文件：
[`templates/qq_to_http_webhook.standard.json`](templates/qq_to_http_webhook.standard.json)。

## 7. 自定义 Webhook JSON

设置：

```text
protocol.webhook_payload_mode = template
protocol.webhook_payload_template_files = [选择的 JSON 文件]
```

WebUI 文件目录：

```text
AstrBot/data/plugin_data/astrbot_plugin_interconnect/files/protocol/webhook_payload_template_files/
```

只使用选择列表中的第一个文件。模板必须是小于等于 1 MiB 的 UTF-8 JSON
对象，不能位于插件 data 目录之外。

模板示例：

```json
{
  "id": "${message_id}",
  "text": "${content.text}",
  "conversation": "${source.id}",
  "sender": "${sender}",
  "images": "${content.images}"
}
```

可使用标准包体的任意点路径，例如：

- `${schema_version}`
- `${message_id}`
- `${route_id}`
- `${source.id}`
- `${sender.id}`
- `${content.text}`
- `${content.images}`
- `${raw_refs.unified_msg_origin}`
- `${envelope}`

字符串值完全等于一个占位符时保留原 JSON 类型；占位符嵌入普通字符串时转换
为文本。未知占位符、非法 JSON、非对象根节点或文件错误会使该次投递失败并
记录到 diagnostics。

配置界面不提供内联 JSON 编辑器，也不支持每条路由覆盖模板。旧内联字段只为
已有配置兼容而保留。

## 8. PowerShell 示例

```powershell
$body = @{
  target = @{
    id = "123456"
    conversation_type = "qq_group"
  }
  content = @{
    text = "hello from LAS"
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/v1/messages" `
  -ContentType "application/json" `
  -Body $body
```

配置了 Token 时添加：

```powershell
-Headers @{ Authorization = "Bearer <token>" }
```

## 9. 兼容与版本

- 调用方应根据 `schema_version` 解析 QQ->HTTP 包体。
- 新代码使用 `target.type=qq_session`；旧 `qq_session_alias` 仅兼容。
- HTTP 和未来 WS 必须使用同一目标字段、消息模型与恢复顺序。
- 协议新增字段应保持向后兼容；破坏性修改必须提升版本并同步本文件与测试。
