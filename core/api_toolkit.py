"""API 工具箱 — 从 agent.py 拆分出来"""
import json as _json


class _APIToolkit:
    def fetch_get(self, url, headers=None, timeout=15):
        import json, urllib.request
        if not url or not url.startswith(("http://", "https://")):
            return {"success": False, "error": "URL 必须以 http:// 或 https:// 开头"}
        req_headers = {"User-Agent": "Wudao/0.3.2"}
        if headers: req_headers.update(headers)
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type:
                    data = json.loads(raw.decode("utf-8"))
                else:
                    data = raw.decode("utf-8", errors="replace")
                return {"success": True, "status": status, "data": data, "content_type": content_type, "size_bytes": len(raw), "summary": f"GET {url} → {status} ({len(raw)} bytes)"}
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}", "status": e.code, "summary": f"GET {url} 失败: HTTP {e.code}"}
        except urllib.error.URLError as e:
            return {"success": False, "error": f"网络错误: {e.reason}", "summary": f"GET {url} 网络错误"}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}", "summary": f"GET {url} 异常"}

    def websocket_route(self, uri, handlers):
        if not handlers: return {"error": "至少需要一个 handler"}
        cases = "\n".join([f"        data = json.loads(message)\n        data_type = data.get('type')\n        if data_type == '{type_name}':\n            await {handler.__name__ if hasattr(handler, '__name__') else 'handler'}(websocket, data)" for type_name, handler in handlers.items()])
        template = f"@app.websocket(\"{uri}\")\nasync def websocket_endpoint(websocket):\n    await websocket.accept()\n    try:\n        while True:\n            message = await websocket.receive_text()\n{cases}\n    except WebSocketDisconnect:\n        print(\"客户端断开\")"
        return {"route": uri, "handler_count": len(handlers), "handlers": list(handlers.keys()), "code_template": template, "summary": f"WS 路由 {uri} 已定义，{len(handlers)} 个消息类型"}

    def find_free_api(self, category="", keyword=""):
        FREE_APIS = [
            {"name": "OpenWeatherMap", "url": "https://api.openweathermap.org", "category": "weather", "desc": "天气数据，需 API Key"},
            {"name": "wttr.in", "url": "https://wttr.in", "category": "weather", "desc": "命令行天气，无需 Key"},
            {"name": "JSONPlaceholder", "url": "https://jsonplaceholder.typicode.com", "category": "development", "desc": "假 REST API 用于测试"},
            {"name": "Open Library", "url": "https://openlibrary.org/developers/api", "category": "books", "desc": "图书数据查询"},
            {"name": "CoinDesk", "url": "https://api.coindesk.com/v1/bpi/currentprice.json", "category": "finance", "desc": "比特币实时价格"},
            {"name": "ExchangeRate-API", "url": "https://api.exchangerate-api.com/v4/latest/USD", "category": "finance", "desc": "汇率转换"},
            {"name": "TheCatAPI", "url": "https://api.thecatapi.com/v1", "category": "animals", "desc": "随机猫咪图片"},
            {"name": "DogCEO", "url": "https://dog.ceo/api/breeds/image/random", "category": "animals", "desc": "随机狗狗图片"},
            {"name": "Jikan", "url": "https://api.jikan.moe/v4", "category": "entertainment", "desc": "MyAnimeList 非官方 API"},
            {"name": "Open Trivia DB", "url": "https://opentdb.com/api_config.php", "category": "entertainment", "desc": "trivia 问答数据库"},
            {"name": "REST Countries", "url": "https://restcountries.com/v3.1", "category": "geography", "desc": "国家信息数据"},
            {"name": "IPify", "url": "https://api.ipify.org?format=json", "category": "network", "desc": "获取公网 IP"},
            {"name": "ipapi", "url": "https://ipapi.co/json/", "category": "network", "desc": "IP 地理位置查询"},
            {"name": "NASA APOD", "url": "https://api.nasa.gov/planetary/apod", "category": "science", "desc": "NASA 每日天文图"},
            {"name": "Faker API", "url": "https://fakerapi.it/api/v1", "category": "development", "desc": "生成假数据用于测试"},
            {"name": "Genderize.io", "url": "https://api.genderize.io", "category": "social", "desc": "根据名字判断性别"},
            {"name": "Agify.io", "url": "https://api.agify.io", "category": "social", "desc": "根据名字预测年龄"},
            {"name": "Nationalize.io", "url": "https://api.nationalize.io", "category": "social", "desc": "根据名字预测国籍"},
        ]
        results = FREE_APIS
        if category: results = [a for a in results if category.lower() in a["category"].lower()]
        if keyword:
            kw = keyword.lower()
            results = [a for a in results if kw in a["name"].lower() or kw in a["desc"].lower() or kw in a["category"].lower()]
        if not results: return {"results": [], "summary": f"未找到匹配的免费 API（分类={category}, 关键词={keyword}）"}
        lines = [f"{r['name']} | {r['category']} | {r['url']} | {r['desc']}" for r in results[:10]]
        return {"results": results[:10], "total_matches": len(results), "summary": f"找到 {len(results)} 个免费 API\n" + "\n".join(lines)}


api = _APIToolkit()


def run_api_tool(args: dict) -> dict:
    action = args.get("action", "")
    params = args.get("params", "{}")
    if isinstance(params, str):
        params = _json.loads(params)

    if action == "api.fetch_get":
        url = params.get("url", "")
        if not url:
            return {"error": "需要 url 参数"}
        timeout = params.get("timeout", 15)
        headers = params.get("headers")
        r = api.fetch_get(url, headers=headers, timeout=timeout)
        return {"success": r["success"], "result": r, "summary": r.get("summary", "")}

    elif action == "api.websocket_route":
        uri = params.get("uri", "/ws/chat")
        handlers = params.get("handlers", {"chat": "chat_handler"})
        r = api.websocket_route(uri, handlers)
        return {"success": True, "result": r, "summary": r.get("summary", "")}

    elif action == "api.find_free_api":
        category = params.get("category", "")
        keyword = params.get("keyword", "")
        r = api.find_free_api(category=category, keyword=keyword)
        return {"success": True, "result": r, "summary": r.get("summary", "")[:200]}

    else:
        return {"error": f"未知 API 操作: {action}"}
