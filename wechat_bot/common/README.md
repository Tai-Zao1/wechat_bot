# common 目录说明

`common/` 只放真正通用、且不带业务流程含义的内容。

当前建议这样理解：

- `defaults.py`
  默认文案、默认接口路径、缓存文件名、GUI 配置名。
- `json_store.py`
  轻量 JSON 读写辅助。
- `auto_reply.py`
  历史兼容入口，不是新的公共实现入口。

如果你在写新代码：

- 需要默认值，优先看 `defaults.py`
- 需要 JSON 存储，优先看 `json_store.py`
- 不要把自动回复专用逻辑继续塞回 `common/`
