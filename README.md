# doc-proofreading-service

这是一个文档校对服务项目。

```bash
gunicorn -w 4 -k gevent -b 0.0.0.0:5000 app:app
```

打包

```bash
uv run pyinstaller --onefile --noconsole --add-data ".env;." run.py
uv run pyinstaller --onefile --noconsole --add-data ".env;." --add-data "coze_private_key.pem;." run.py
```


```bash
taskkill /F /IM run.exe
```