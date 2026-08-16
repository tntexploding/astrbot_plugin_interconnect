# HTTP 协议入口

HTTP 协议已统一到
[`http_communication_spec.md`](http_communication_spec.md)。该文件是字段、响应、
错误码和 JSON 模板的唯一规范，本页只保留快速入口。

## 本地到 QQ

优先按会话 ID 直发：

```json
{
  "target": {
    "id": "123456",
    "conversation_type": "qq_group"
  },
  "content": {
    "text": "hello"
  }
}
```

```text
POST http://127.0.0.1:8765/v1/messages
```

请求无法携带目标时才使用“HTTP 到 QQ 会话”兜底路由；会话 ID 无法解析时先
刷新 `sessions.bindings`，最后才使用人工 alias。

## QQ 到本地

在 WebUI 添加“QQ 到 HTTP Webhook”路由，并填写：

```text
source.type
source.conversation_id
target.url
```

不需要手动绑定。自定义包体使用
`protocol.webhook_payload_template_files` 选择 JSON 文件，不在线编辑。

完整规范：
[`http_communication_spec.md`](http_communication_spec.md)。
