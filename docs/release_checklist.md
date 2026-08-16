# v0.1.0 发布验收

本文定义 HTTP 首个稳定版本的封版边界。WebSocket 不在本次验收范围内。

## 自动化检查

发布前在插件目录执行：

```powershell
..\..\..\.venv\Scripts\ruff.exe format --check .
..\..\..\.venv\Scripts\ruff.exe check .
..\..\..\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

测试覆盖以下关键行为：

- 配置迁移、WebUI Schema 和路由条件。
- QQ 消息规范化、原始前缀保留和媒体元数据。
- HTTP 服务真实监听、停启和健康检查。
- Bearer 鉴权、请求体上限、非法 JSON 与非法字段。
- QQ 投递失败时返回 HTTP 502 和逐目标结果。
- Webhook 标准包体、请求头、5xx 重试和超时隔离。
- 会话映射在存储对象重建后恢复。
- 自定义 JSON 文件模板及路径边界。

## 已完成的真实联调

- AstrBot 正确加载插件并展示 WebUI 配置。
- QQ 消息命中路由并发送到本地 HTTP 服务。
- 本地 HTTP 请求通过插件发送到 QQ 会话。
- 群聊来源限制和文本前缀限制能够共同生效。

## 发布前 Git 检查

- 确认 `metadata.yaml` 的版本与标签一致。
- 将 `metadata.yaml` 的 `repo` 设置为插件实际仓库地址。
- 确认 Git 远端属于本插件，禁止推送到 AstrBot 示例插件仓库。
- 确认运行配置、鉴权令牌、缓存和 AstrBot 持久化数据未进入提交。
- 完整测试通过后再创建并推送 `v0.1.0` 标签。
