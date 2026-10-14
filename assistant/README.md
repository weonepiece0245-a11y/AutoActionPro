# AI 小助理

一个在终端里运行的 AI 聊天助理,基于 Claude API(模型 `claude-opus-5-5`)。回答逐字流式显示,并且会记住上下文,支持连续追问。

## 安装

需要 Python 3.9 及以上版本。

```bash
pip install -r assistant/requirements.txt
```

## 设置 API Key

先到 <https://console.anthropic.com/> 获取 API Key,然后任选一种方式配置。

**方式一(推荐):写在 .env 文件里**,只需设置一次:

```bash
copy assistant\.env.example assistant\.env      # Windows
cp assistant/.env.example assistant/.env        # macOS / Linux
```

用记事本(或任意编辑器)打开 `assistant/.env`,把 `在这里填你的Key` 换成你的 Key,保存即可。`.env` 已被 `.gitignore` 忽略,不会被提交到 Git。

**方式二:设置环境变量**

```bash
export ANTHROPIC_API_KEY=你的Key          # macOS / Linux
set ANTHROPIC_API_KEY=你的Key             # Windows CMD
$env:ANTHROPIC_API_KEY="你的Key"          # Windows PowerShell
```

两种方式都配置了时,以环境变量为准。如果想用别的位置的文件,可以加参数 `--env-file 路径`。

## 运行

```bash
python assistant/assistant.py
```

对话示例:

```
你好!我是你的 AI 小助理,有什么问题尽管问我。
命令:/clear 清空对话  /exit 退出

你:Python 里列表和元组有什么区别?
助理:……
```

### 命令

| 命令 | 作用 |
|---|---|
| `/clear` | 清空对话历史,开始新话题 |
| `/exit` 或 `/quit` | 退出(也可以按 Ctrl+C 或 Ctrl+D) |

回答过程中按 Ctrl+C 可以中断当前回答,不会退出程序。

### 可选参数

```bash
python assistant/assistant.py --effort high        # 思考更仔细(更慢)
python assistant/assistant.py --model claude-sonnet-5-5
```

`--effort` 可选 `low`(默认,适合日常聊天)、`medium`、`high`、`xhigh`、`max`。

## 测试

```bash
python -m unittest assistant/test_assistant.py
```

测试使用假客户端,不会调用真实 API,也不会产生费用。
